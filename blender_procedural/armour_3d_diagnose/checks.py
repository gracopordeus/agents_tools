"""Geometry checks on a fitted armour. Runs inside Blender on ``<run>/3_fit/armour_fitted.blend``.

Every check reads the body from the rig of the fit job and the pieces from the file, and names bones by
their role in the body profile: nothing here knows a bone name or a piece name. The result is a JSON list
of tables, one per check and piece; ``run.py`` prints them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "armour_3d_fit"))
import core  # noqa: E402
import fit  # noqa: E402

DEFAULTS = {"bands": 5,                  # slices along a bone or along the height of a piece
            "reach_m": 0.2,              # how far from the skin a plate is looked for
            "cap_normal": 0.7,           # a skin normal this close to straight up or down counts as top or bottom
            "owned": 0.5,                # skin weight from which a vertex of a piece counts as moved by a bone
            "collar_step_m": 0.03, "symmetry_samples": 4000,
            # close-ups for the visual review: joints by role, where pieces meet the body and each other
            "views_joints": ["neck", "forearm.L", "forearm.R", "hand.L", "hand.R", "shin.L", "shin.R", "thigh.L", "upper_arm.L"],
            "views_distance_m": 0.9, "views_lens_mm": 60, "views_lift": 0.35, "views_midline_m": 0.03, "views_resolution": [900, 700],
            "proportion_reference": "suit",      # slot of the piece the others are sized against
            "shape_neighbours": 24, "shape_min_vertices": 40}


class Scene:
    def __init__(self, request: dict):
        job = json.loads(Path(request["job"]).read_text(encoding="utf-8"))
        base = Path(request["job"]).parent
        resolve = lambda value: str((base / value).resolve())
        self.settings = {**DEFAULTS, **request.get("settings", {})}
        self.body = fit.Body(Path(resolve(job["body"]["rig"])), json.loads(Path(resolve(job["body_profile"])).read_text(encoding="utf-8")))
        slots = json.loads(Path(resolve(job["slot_profile"])).read_text(encoding="utf-8"))["slots"]
        report = json.loads(Path(request["report"]).read_text(encoding="utf-8"))
        self.pieces = {}
        for name in job["pieces"]:
            if request.get("pieces") and name not in request["pieces"]:
                continue
            obj = bpy.data.objects[name]
            points = np.asarray([obj.matrix_world @ vertex.co for vertex in obj.data.vertices], dtype=np.float64)
            obj.data.calc_loop_triangles()
            triangles = np.asarray([tuple(t.vertices) for t in obj.data.loop_triangles], dtype=np.int64)
            groups = {group.index: group.name for group in obj.vertex_groups}
            weights = {}
            for vertex in obj.data.vertices:
                for item in vertex.groups:
                    weights.setdefault(groups[item.group], np.zeros(len(points)))[vertex.index] = item.weight
            slot = report["pieces"][name]["slot"]                    # after the fit put each gauntlet on its hand
            self.pieces[name] = {"points": points, "triangles": triangles, "weights": weights, "slot_name": slot,
                                 "slot": slots[slot], "tree": BVHTree.FromPolygons(points.tolist(), triangles.tolist(), all_triangles=True)}
        self.source_blend, self.source, self.report = resolve(job["armour"]), None, report
        self.children = {}
        for bone in self.body.rig_bones:
            if bone["parent"] >= 0:
                self.children.setdefault(self.body.rig_bones[bone["parent"]]["name"], []).append(bone["name"])

    def covered_bones(self, piece: dict) -> list[str]:
        """Bones the piece lies on and leaves visible: its skin bones without the ones it hides whole."""
        hidden = set(self.body.bones(piece["slot"].get("mask_bones", [])))
        return [self.body.bone_names[i] for i in self.body.bones(piece["slot"]["skin_bones"]) if i not in hidden]

    def bone_axis(self, name: str) -> tuple[np.ndarray, np.ndarray, float] | None:
        """Head of a bone, its direction and its length: towards the farthest of its children."""
        head = self.body.heads[name]
        ends = [self.body.heads[child] for child in self.children.get(name, []) if child in self.body.heads]
        if not ends:
            return None
        end = max(ends, key=lambda point: float(np.linalg.norm(point - head)))
        length = float(np.linalg.norm(end - head))
        return (head, (end - head) / length, length) if length > 1e-6 else None

    def frame(self, axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Across a limb: towards the character's left and towards its front."""
        left = np.eye(3)[self.body.mirror_axis] * self.body.left_sign
        left = core.unit(left - (left @ axis) * axis)
        return left, np.cross(left, axis) * (1.0 if np.cross(left, axis) @ self.front() > 0 else -1.0)

    def front(self) -> np.ndarray:
        up = np.array([0.0, 0.0, 1.0])
        left = np.eye(3)[self.body.mirror_axis] * self.body.left_sign
        return np.cross(left, up)                                    # front x left = up


def cm(value: float) -> float | None:
    return None if value is None or not np.isfinite(value) else round(float(value) * 100, 1)


def clearance(scene: Scene) -> list[dict]:
    """Room between the skin and the plate over it, by slice of each bone and by side of the limb."""
    tables, body = [], scene.body
    for name, piece in scene.pieces.items():
        for bone in scene.covered_bones(piece):
            limb = scene.bone_axis(bone)
            vertices = np.flatnonzero(body.dominant == body.bone_names.index(bone))
            if limb is None or len(vertices) < 20:
                continue
            head, axis, length = limb
            left, front = scene.frame(axis)
            distance = np.full(len(vertices), np.nan)
            for k, vertex in enumerate(vertices):
                hit = piece["tree"].ray_cast(Vector(body.positions[vertex]), Vector(body.normals[vertex]), scene.settings["reach_m"])
                if hit[0] is not None:
                    distance[k] = hit[3]
            along = (body.positions[vertices] - head) @ axis / length
            normal = body.normals[vertices]
            side = np.where(np.abs(normal @ front) >= np.abs(normal @ left),
                            np.where(normal @ front > 0, "front", "back"), np.where(normal @ left > 0, "left", "right"))
            rows = []
            edges = np.linspace(0.0, 1.0, scene.settings["bands"] + 1)
            for a, b in zip(edges[:-1], edges[1:]):
                for where in ("front", "back", "left", "right"):
                    members = (along >= a) & (along < b) & (side == where)
                    if members.sum() < 3:
                        continue
                    found = distance[members]
                    rows.append({"along_bone_%": f"{int(a * 100)}-{int(b * 100)}", "side": where, "skin_vertices": int(members.sum()),
                                 "no_plate_over_it": int(np.isnan(found).sum()),
                                 "min_cm": cm(np.nanmin(found)) if np.isfinite(found).any() else None,
                                 "median_cm": cm(np.nanmedian(found)) if np.isfinite(found).any() else None})
            tables.append({"check": "clearance", "piece": name, "bone": bone, "rows": rows,
                           "note": "0% is the head of the bone; sides are the character's; no_plate_over_it = skin with no plate "
                                   "outwards of it (bare, or already outside the plate)"})
    return tables


def enclosed(scene: Scene) -> list[dict]:
    """A piece against the part of the body it hides whole (a helmet on the head, a boot on the foot).

    The other measures follow the bones a piece leaves visible; here the body is the flesh of the slot's
    ``mask_bones``, sliced by height. Each slice gives the room from the skin out to the plate by side, how
    far the middle of the piece is from the middle of the flesh, and the two sizes of each.
    """
    tables, body = [], scene.body
    up = np.array([0.0, 0.0, 1.0])
    left = np.eye(3)[body.mirror_axis] * body.left_sign
    front = scene.front()
    for name, piece in scene.pieces.items():
        hidden = body.bones(piece["slot"].get("mask_bones", []))
        vertices = np.flatnonzero(np.isin(body.dominant, hidden))
        if len(vertices) < 20:
            continue
        distance = np.full(len(vertices), np.nan)
        for k, vertex in enumerate(vertices):
            hit = piece["tree"].ray_cast(Vector(body.positions[vertex]), Vector(body.normals[vertex]), scene.settings["reach_m"])
            if hit[0] is not None:
                distance[k] = hit[3]
        flesh, normal = body.positions[vertices], body.normals[vertices]
        low, high = float((flesh @ up).min()), float((flesh @ up).max())
        along = (flesh @ up - low) / (high - low)
        across = np.where(np.abs(normal @ front) >= np.abs(normal @ left),
                          np.where(normal @ front > 0, "front", "back"), np.where(normal @ left > 0, "left", "right"))
        side = np.where(np.abs(normal @ up) > scene.settings["cap_normal"], np.where(normal @ up > 0, "top", "bottom"), across)
        plate_along = (piece["points"] @ up - low) / (high - low)
        rows, sizes = [], []
        edges = np.linspace(0.0, 1.0, scene.settings["bands"] + 1)
        middle = lambda points, direction: (float((points @ direction).min()) + float((points @ direction).max())) / 2
        for a, b in zip(edges[:-1], edges[1:]):
            band = f"{int(a * 100)}-{int(b * 100)}"
            for where in ("front", "back", "left", "right", "top", "bottom"):
                members = (along >= a) & (along <= b) & (side == where)
                if members.sum() < 3:
                    continue
                found = distance[members]
                rows.append({"height_%": band, "side": where, "skin_vertices": int(members.sum()),
                             "no_plate_over_it": int(np.isnan(found).sum()),
                             "min_cm": cm(np.nanmin(found)) if np.isfinite(found).any() else None,
                             "median_cm": cm(np.nanmedian(found)) if np.isfinite(found).any() else None})
            plate, skin = piece["points"][(plate_along >= a) & (plate_along <= b)], flesh[(along >= a) & (along <= b)]
            if len(plate) >= 8 and len(skin) >= 6:
                sizes.append({"height_%": band, "off_to_the_left_cm": cm(middle(plate, left) - middle(skin, left)),
                              "off_to_the_front_cm": cm(middle(plate, front) - middle(skin, front)),
                              "width_piece_cm": cm(np.ptp(plate @ left)), "width_body_cm": cm(np.ptp(skin @ left)),
                              "depth_piece_cm": cm(np.ptp(plate @ front)), "depth_body_cm": cm(np.ptp(skin @ front))})
        over = float((piece["points"] @ up).max()) - high
        under = low - float((piece["points"] @ up).min())
        sizes.append({"height_%": "whole", "above_the_body_cm": cm(over), "below_the_body_cm": cm(under),
                      "height_piece_cm": cm(np.ptp(piece["points"] @ up)), "height_body_cm": cm(high - low)})
        tables.append({"check": "enclosed", "piece": name, "bone": "room", "rows": rows,
                       "note": "0% is the lowest point of the hidden body, 100% its top; room is from the skin outwards to the first plate"})
        tables.append({"check": "enclosed", "piece": name, "bone": "sizes", "rows": sizes,
                       "note": "outer sizes of the piece against the hidden body in the same slice of height"})
    return tables


def alignment(scene: Scene) -> list[dict]:
    """Middle of the piece against the middle of the limb it is on, slice by slice."""
    tables, body = [], scene.body
    for name, piece in scene.pieces.items():
        for bone in scene.covered_bones(piece):
            limb = scene.bone_axis(bone)
            flesh = body.positions[body.dominant == body.bone_names.index(bone)]
            own = piece["points"][piece["weights"].get(bone, np.zeros(len(piece["points"]))) > scene.settings["owned"]]
            if limb is None or len(flesh) < 20 or len(own) < 20:
                continue
            head, axis, length = limb
            left, front = scene.frame(axis)
            rows = []
            edges = np.linspace(0.0, 1.0, scene.settings["bands"] + 1)
            for a, b in zip(edges[:-1], edges[1:]):
                slab = lambda points: points[((points - head) @ axis / length >= a) & ((points - head) @ axis / length < b)]
                plate, skin = slab(own), slab(flesh)
                if len(plate) < 8 or len(skin) < 6:
                    continue
                middle = lambda points, direction: (float((points @ direction).min()) + float((points @ direction).max())) / 2
                width = lambda points, direction: float(np.ptp(points @ direction))
                rows.append({"along_bone_%": f"{int(a * 100)}-{int(b * 100)}",
                             "off_to_the_left_cm": cm(middle(plate, left) - middle(skin, left)),
                             "off_to_the_front_cm": cm(middle(plate, front) - middle(skin, front)),
                             "width_piece_cm": cm(width(plate, left)), "width_limb_cm": cm(width(skin, left)),
                             "depth_piece_cm": cm(width(plate, front)), "depth_limb_cm": cm(width(skin, front))})
            tables.append({"check": "alignment", "piece": name, "bone": bone, "rows": rows,
                           "note": "middle of the extent of the piece minus middle of the limb; a flap on one side moves the middle of a piece"})
    return tables


def reach(scene: Scene) -> list[dict]:
    """Where along each bone the piece starts and ends: short of a joint, on it, or past it."""
    rows, body = [], scene.body
    for name, piece in scene.pieces.items():
        for bone in scene.covered_bones(piece):
            limb = scene.bone_axis(bone)
            own = piece["points"][piece["weights"].get(bone, np.zeros(len(piece["points"]))) > scene.settings["owned"]]
            if limb is None or len(own) < 20:
                continue
            head, axis, length = limb
            along = (own - head) @ axis
            rows.append({"piece": name, "bone": bone, "bone_length_cm": cm(length), "starts_at_%": round(float(along.min() / length * 100)),
                         "ends_at_%": round(float(along.max() / length * 100)),
                         "before_the_near_joint_cm": cm(max(0.0, -float(along.min()))),
                         "past_the_far_joint_cm": cm(max(0.0, float(along.max()) - length))})
    return [{"check": "reach", "rows": rows,
             "note": "only the part of the piece that bone moves; past a joint, a rigid part swings off the next limb when it bends"}]


def gloves(scene: Scene) -> list[dict]:
    """A gauntlet on its arm: the cuff against the forearm, and each finger against the finger in it."""
    tables, body = [], scene.body
    for name, piece in scene.pieces.items():
        rule = piece["slot"].get("fingers")
        place = piece["slot"]["place"]
        if rule is None or place.get("type") != "axis":
            continue
        start, end = (body.head(role) for role in place["bones"])
        axis, length = core.unit(end - start), float(np.linalg.norm(end - start))
        forearm = body.bone_names[body.bones([place["bones"][0]])[0]]
        cuff = piece["points"][piece["weights"].get(forearm, np.zeros(len(piece["points"]))) > scene.settings["owned"]]
        rows = []
        if len(cuff) >= 30:
            height = cuff @ axis
            point, direction = core.centre_line(cuff, axis, float(height.min()), float(height.max()))
            off = (point - start) - ((point - start) @ axis) * axis
            rows.append({"part": "cuff", "tilt_against_forearm_deg": round(float(np.degrees(np.arccos(np.clip(direction @ axis, -1, 1)))), 1),
                         "off_the_bone_cm": cm(np.linalg.norm(off)), "covers_from_%": round(float(((cuff - start) @ axis).min() / length * 100)),
                         "covers_to_%": round(float(((cuff - start) @ axis).max() / length * 100))})
        for finger, group in rule["chains"].items():
            ids = sorted(body.bones([group]), key=lambda i: body.bone_names[i])
            names = [body.bone_names[i] for i in ids]
            own = np.zeros(len(piece["points"]))
            for bone in names:
                own = own + piece["weights"].get(bone, 0.0)
            plate, skin = piece["points"][own > scene.settings["owned"]], body.positions[np.isin(body.dominant, ids)]
            if len(plate) < 5 or len(skin) < 5 or len(names) < 2:
                rows.append({"part": finger, "note": "no vertices of the piece follow this finger"})
                continue
            root, along = body.heads[names[0]], core.unit(body.heads[names[-1]] - body.heads[names[0]])
            gap = plate.mean(axis=0) - skin.mean(axis=0)
            rows.append({"part": finger, "vertices": int(len(plate)),
                         "piece_from_cm": cm(((plate - root) @ along).min()), "piece_to_cm": cm(((plate - root) @ along).max()),
                         "finger_from_cm": cm(((skin - root) @ along).min()), "finger_to_cm": cm(((skin - root) @ along).max()),
                         "beside_the_finger_cm": cm(np.linalg.norm(gap - (gap @ along) * along))})
        tables.append({"check": "gloves", "piece": name, "rows": rows,
                       "note": "cuff: centre line of the part on the forearm; fingers: along the finger from its first joint"})
    return tables


def collar(scene: Scene) -> list[dict]:
    """A collar around the neck: how far its inner wall stands from the skin, slice by slice and side by side."""
    tables, body = [], scene.body
    for name, piece in scene.pieces.items():
        rule = piece["slot"].get("collar")
        if rule is None:
            continue
        rule = {**fit.COLLAR_DEFAULTS, **rule}
        base = body.head(rule["bone"])
        axis = core.unit(body.head(rule["toward"]) - base)
        left, front = scene.frame(axis)
        neck = body.positions[np.isin(body.dominant, body.bones(rule["body"]))]
        polar = lambda points: ((points - base) @ axis, (points - base) @ left, (points - base) @ front)
        height, x, y = polar(piece["points"])
        own = (height > 0) & (np.hypot(x, y) < rule["reach_m"])
        neck_h, neck_x, neck_y = polar(neck)
        rows = []
        top = float(height[own].max()) if own.any() else 0.0
        for a in np.arange(0.0, top, scene.settings["collar_step_m"]):
            ring, flesh = own & (height >= a) & (height < a + scene.settings["collar_step_m"]), (neck_h >= a) & (neck_h < a + scene.settings["collar_step_m"])
            if ring.sum() < 8 or flesh.sum() < 6:
                continue
            row = {"above_neck_base_cm": f"{cm(a)}-{cm(a + scene.settings['collar_step_m'])}"}
            for where, mask_piece, mask_neck in (("left", x > np.abs(y), neck_x > np.abs(neck_y)), ("right", -x > np.abs(y), -neck_x > np.abs(neck_y)),
                                                 ("front", y > np.abs(x), neck_y > np.abs(neck_x)), ("back", -y > np.abs(x), -neck_y > np.abs(neck_x))):
                p, n = ring & mask_piece, flesh & mask_neck
                row[f"gap_{where}_cm"] = cm(np.percentile(np.hypot(x, y)[p], 10) - np.percentile(np.hypot(neck_x, neck_y)[n], 90)) \
                    if p.sum() >= 3 and n.sum() >= 3 else None
            rows.append(row)
        tables.append({"check": "collar", "piece": name, "rows": rows,
                       "note": "inner wall of the collar minus outer skin of the neck; negative = the neck is through it; "
                               "left and right should match"})
    return tables


def weights(scene: Scene) -> list[dict]:
    """Which bones move each height of a piece."""
    tables = []
    for name, piece in scene.pieces.items():
        z = piece["points"][:, 2]
        edges = np.linspace(z.min(), z.max(), scene.settings["bands"] + 1)
        rows = []
        for a, b in zip(edges[:-1], edges[1:]):
            members = (z >= a) & (z <= b)
            if members.sum() == 0:
                continue
            share = {bone: float(weight[members].mean()) for bone, weight in piece["weights"].items()}
            rows.append({"height_cm": f"{cm(a)}-{cm(b)}", "vertices": int(members.sum()),
                         "bones": ", ".join(f"{bone} {value:.2f}" for bone, value in sorted(share.items(), key=lambda item: -item[1]) if value >= 0.02)})
        tables.append({"check": "weights", "piece": name, "rows": rows, "note": "mean skin weight per bone in each slice of height"})
    return tables


def symmetry(scene: Scene) -> list[dict]:
    """How far the fitted armour is from being its own mirror image across the middle of the body."""
    rows, body = [], scene.body
    left, right = body.side_tokens
    done = set()
    for name, piece in scene.pieces.items():
        slot = piece["slot_name"]
        twin = next((other for other, item in scene.pieces.items() if other != name and item["slot_name"][:-1] == slot[:-1]
                     and slot[-2:] in (".L", ".R")), None)
        if twin in done:
            continue
        done.add(name)
        target = scene.pieces[twin or name]
        mirrored = body.mirrored(piece["points"])
        step = max(1, len(mirrored) // scene.settings["symmetry_samples"])
        error = np.asarray([target["tree"].find_nearest(Vector(point))[3] for point in mirrored[::step]])
        rows.append({"piece": name, "against": twin or "its own mirror image", "max_mm": round(float(error.max()) * 1000, 2),
                     "p95_mm": round(float(np.percentile(error, 95)) * 1000, 2), "mean_mm": round(float(error.mean()) * 1000, 2)})
    return [{"check": "symmetry", "rows": rows, "note": "distance from each mirrored vertex to the surface it should land on"}]


def length(scene: Scene) -> list[dict]:
    """Every length rule of the slots against the limb it names: where the asset drew the rim, where the rule
    put it, and where it is.

    ``lengthen`` of a sub-part (a sleeve) and ``fit_length`` of a piece (a boot) both say "this far along this
    limb", in chain coordinates: 100% is the joint at the end of the first bone, 150% half the bone after it.
    ``drawn_%`` is the asset as it came, standing on the body in its own proportions; ``design_%`` is what
    that reading asks for once rims near a joint are put on it and pieces that meet are kept meeting.
    """
    rows, body = [], scene.body
    for name, piece in scene.pieces.items():
        entry = scene.report["pieces"][name]
        told = {"fit_length": entry.get("fit_length") or {}}
        told.update({f"lengthen {item['name']}": item["lengthened"] for item in entry.get("proportion", []) if "lengthened" in item})
        for key, design in (entry.get("design") or {}).items():
            first_bone, second_bone = design["bones"]
            first, second = fit.limb_lengths(body, first_bone, second_bone, design.get("beyond_bone"))
            start = body.head(first_bone)
            points = piece["points"]
            part = next((item for item in piece["slot"].get("parts", []) if f"lengthen {item['name']}" == key), None)
            if part is not None:                             # a sub-part: what its bones move
                moved = sum(piece["weights"].get(body.bone_names[i], np.zeros(len(points))) for i in body.bones(part["bones"]))
                points = points[moved > scene.settings["owned"]]
            if len(points) < 20:
                continue
            reach = float(((points - start) @ core.unit(body.head(second_bone) - start)).max())
            applied = told.get(key) or {}
            fraction = applied.get("fraction", design["fraction"])
            target = core.chain_length(fraction, first, second)
            rows.append({"piece": name, "rule": key, "from": first_bone, "to": second_bone, "bone_length_cm": cm(first),
                         "drawn_%": round(design["drawn"] * 100), "design_%": round(design["fraction"] * 100),
                         "touches_neighbour": design.get("touches"), "told_%": round(fraction * 100), "applied": bool(applied),
                         "ends_at_%": round(core.chain_fraction(reach, first, second) * 100, 1),
                         "beyond_cm": cm(max(0.0, reach - target)), "short_cm": cm(max(0.0, target - reach)),
                         "why": design["why"]})
    return [{"check": "length", "rows": rows,
             "note": "100% = the joint at the end of the first bone, 150% = half the bone after it. drawn = the asset as it came; "
                     "design = what that asks for; told = what the rule used; applied = the rule acted on this piece. "
                     "beyond/short = the rim against where it was told"}]


def source_points(scene: Scene) -> dict[str, np.ndarray]:
    """The pieces as they came into the fit (step 2), in their own units: same vertices, same order."""
    if scene.source is None:
        with bpy.data.libraries.load(scene.source_blend) as (found, wanted):
            wanted.objects = [name for name in found.objects if name in scene.pieces]
        scene.source = {}
        for obj in wanted.objects:
            name = next(key for key in scene.pieces if obj.name.split(".")[0] == key or obj.name == key)
            scene.source[name] = np.asarray([obj.matrix_world @ vertex.co for vertex in obj.data.vertices], dtype=np.float64)
    return scene.source


def proportion(scene: Scene) -> list[dict]:
    """Size of each piece against the reference piece of the set, as designed and as fitted.

    A set is drawn with proportions (a helmet so wide and so tall against the body armour). The design is the
    slot's ``relative_to`` when it has one, otherwise the asset as it came. Width is across the body, height is up.
    """
    rows, body = [], scene.body
    source = source_points(scene)
    across, up = body.mirror_axis, 2
    reference = next((name for name, piece in scene.pieces.items() if piece["slot_name"] == scene.settings["proportion_reference"]), None)
    if reference is None:
        return [{"check": "proportion", "rows": [], "note": f"no piece in slot {scene.settings['proportion_reference']}"}]
    size = lambda points: {"width": float(np.ptp(points[:, across])), "height": float(np.ptp(points[:, up]))}
    base_design, base_fitted = size(source[reference]), size(scene.pieces[reference]["points"])
    for name, piece in scene.pieces.items():
        if name == reference or name not in source:
            continue
        design, fitted = size(source[name]), size(piece["points"])
        planned = piece["slot"].get("proportion", {}).get("relative_to", {})
        row, ratios = {"piece": name, "design_from": "slot relative_to" if planned else "asset"}, []
        for key in ("width", "height"):
            wanted = planned.get(key, design[key] / base_design[key])
            got = fitted[key] / base_fitted[key]
            ratios.append(got / wanted)
            row[f"{key}_design_%"], row[f"{key}_fitted_%"] = round(wanted * 100, 1), round(got * 100, 1)
        row["fitted_over_design"] = round(float(np.exp(np.mean(np.log(ratios)))), 3)
        row["sized_by_body"] = bool("fit_length" in piece["slot"])
        rows.append(row)
    return [{"check": "proportion", "rows": rows,
             "note": f"% of the {reference}; fitted_over_design is the mean of width and height (1 = as designed). A piece "
                     "sized_by_body takes its length from a bone and is not expected to keep the design"}]


def shape(scene: Scene) -> list[dict]:
    """How much each piece was bent out of its own shape by the fit.

    Around every vertex, the fitted neighbourhood is compared with the same neighbourhood as it came, allowing
    it to move, turn and grow by one factor. What is left is distortion: a dent, a crumple, a stretch one way.
    Plate should show little of it; a piece fitted section by section shows more, evenly, and a knuckle a lot.
    """
    rows, body = [], scene.body
    source = source_points(scene)
    count = scene.settings["shape_neighbours"]
    for name, piece in scene.pieces.items():
        if name not in source or len(source[name]) != len(piece["points"]):
            rows.append({"piece": name, "note": "vertices do not match the piece that came into the fit"})
            continue
        before, after = source[name], piece["points"]
        tree = KDTree(len(before))
        for index, point in enumerate(before):
            tree.insert(Vector(point), index)
        tree.balance()
        near = np.asarray([[item[1] for item in tree.find_n(Vector(point), count)] for point in before])
        a = before[near] - before[near].mean(axis=1, keepdims=True)
        b = after[near] - after[near].mean(axis=1, keepdims=True)
        u, singular, vt = np.linalg.svd(np.einsum("nki,nkj->nij", a, b))
        sign = np.sign(np.linalg.det(u @ vt))
        singular[:, 2] *= sign
        u[:, :, 2] *= sign[:, None]
        scale = singular.sum(axis=1) / np.maximum((a ** 2).sum(axis=(1, 2)), 1e-18)
        fitted = scale[:, None, None] * np.einsum("nki,nij->nkj", a, u @ vt)
        left = np.sqrt(((b - fitted) ** 2).sum(axis=2).mean(axis=1))
        radius = np.sqrt((b ** 2).sum(axis=2).mean(axis=1))
        share = left / np.maximum(radius, 1e-9) * 100
        weights = piece["weights"]
        bones = list(weights)
        owner = np.asarray(bones)[np.argmax(np.stack([weights[bone] for bone in bones]), axis=0)] if bones else np.full(len(after), "-")
        worst = int(np.argmax(share))
        rows.append({"piece": name, "median_%": round(float(np.median(share)), 1), "p95_%": round(float(np.percentile(share, 95)), 1),
                     "max_%": round(float(share.max()), 1), "p95_mm": round(float(np.percentile(left, 95)) * 1000, 1),
                     "worst_under_bone": str(owner[worst]), "worst_at_height_m": round(float(after[worst][2]), 2)})
        by_bone = {}
        for bone in set(owner.tolist()):
            members = owner == bone
            if members.sum() >= scene.settings["shape_min_vertices"]:
                by_bone[bone] = round(float(np.percentile(share[members], 95)), 1)
        rows[-1]["p95_%_by_bone"] = ", ".join(f"{bone.replace('mixamorig_', '')} {value}" for bone, value in sorted(by_bone.items(), key=lambda item: -item[1])[:5])
    return [{"check": "shape", "rows": rows,
             "note": "distortion left after allowing each neighbourhood to move, turn and grow by one factor, as % of its size"}]


def views(scene: Scene) -> list[dict]:
    """Close-up pictures of the places where pieces meet the body and each other, for a person to look at.

    One set per joint named in the settings (by role in the body profile): the camera looks at the joint from
    the front, the back, the outer side and, for a joint on the middle of the body, from above. Nothing is
    measured: this is the evidence for what no gate sees (a dent, a crumple, a rim that looks wrong).
    """
    out, body, settings = Path(scene.settings["views_out"]), scene.body, scene.settings
    render = bpy.context.scene
    render.render.engine = "BLENDER_WORKBENCH"
    render.display.shading.light, render.display.shading.color_type, render.display.shading.show_cavity = "STUDIO", "OBJECT", True
    render.render.resolution_x, render.render.resolution_y = settings["views_resolution"]
    camera = bpy.data.objects.new("diagnose_camera", bpy.data.cameras.new("diagnose_camera"))
    render.collection.objects.link(camera)
    render.camera, camera.data.lens = camera, settings["views_lens_mm"]
    left = np.eye(3)[body.mirror_axis] * body.left_sign
    front, up = scene.front(), np.array([0.0, 0.0, 1.0])
    rows = []
    for role in settings["views_joints"]:
        try:
            target = body.head(role)
        except KeyError:
            continue
        offset = float((target - body.positions.mean(axis=0)) @ left)
        outer = left * (1.0 if offset >= 0 else -1.0)
        looks = {"front": front, "back": -front, "side": outer}
        if abs(offset) < settings["views_midline_m"]:
            looks["top"] = up * 0.98 - front * 0.2                  # straight down has no "up" for the camera
            looks["side"] = left
        for name, direction in looks.items():
            eye = target + core.unit(direction + up * settings["views_lift"] * (name != "top")) * settings["views_distance_m"]
            camera.location = Vector(eye)
            camera.rotation_euler = (Vector(target) - Vector(eye)).to_track_quat("-Z", "Y").to_euler()
            file = out / f"{role.replace('.', '_')}_{name}.png"
            render.render.filepath = str(file)
            bpy.ops.render.render(write_still=True)
            rows.append({"joint": role, "view": name, "file": file.name})
    return [{"check": "views", "rows": rows, "note": f"pictures in {out}; open them, a list of files proves nothing"}]


CHECKS = {"views": views, "length": length, "proportion": proportion, "shape": shape, "clearance": clearance, "enclosed": enclosed, "alignment": alignment, "reach": reach, "gloves": gloves, "collar": collar,
          "weights": weights, "symmetry": symmetry}

if __name__ == "__main__":
    request = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    scene = Scene(request)
    tables = [table for check in request["checks"] for table in CHECKS[check](scene)]
    Path(request["result"]).write_text(json.dumps(tables, indent=1), encoding="utf-8")
