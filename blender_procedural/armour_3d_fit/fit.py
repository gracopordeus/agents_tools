"""Blender pipeline: place, refine, mask the body, skin, test poses, render and export."""
from __future__ import annotations

import hashlib
import dataclasses
import json
import math
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

import core
from sdf import SurfaceSDF

DEFAULTS = {
    "clearance_m": 0.003,              # the cavity surface stays this far outside the body
    "gap_weight": 0.3,                 # pull of the cavity surface towards the body, relative to penetration
    "regularisation": 0.002,            # cost of leaving the landmark estimate
    "iterations": 30,
    "refine_scale": False,             # let the optimiser also resize the piece (within limits)
    "samples_interior": 1200, "samples_exterior": 600,
    "limits": {"translation_m": 0.05, "rotation_deg": 10.0, "scale": 0.08, "anisotropy": 0.05},
    # the size of a set of plate is one: every piece gets the scale of the set, also the slots that declare a
    # size of their own (a suit sized by the distance from neck to knee). Rigid styles only
    # the body is symmetric, a generated armour is not, and fitted on their own the two sides end up different.
    # The side named here is fitted and the other one repeats its fit, mirrored: sleeves and thighs of a suit
    # (parts "name.L" / "name.R") and pieces that are mirror images of each other (slots "glove.L" / "glove.R").
    # None: each side on its own. Rigid styles only
    "symmetry_reference": "R",
    "mirror_tolerance_m": 1e-5,        # two pieces are mirror images of each other when their vertices agree this well
    "uniform_set_scale": True,
    # True: no piece is resized at all, it is only placed and turned at the joints. An armour slimmer than the
    # body is then pierced by it (on the reference body: 21.7% of the skin hidden, 519 cm2 in the worst pose)
    "keep_shape": False,
    "proportion": {"step_m": 0.03, "sectors": 24, "harmonics": 2, "expectile": 0.9},
    # how a piece takes the proportions of the body:
    #   plate   - rigid: one scale in depth and one in width per piece, sized by its tightest sections;
    #             silhouettes stay straight and the piece stands off the body
    #   leather - flexible: every cross-section follows the girth of the body under it
    "style": "plate",
    # a straight plate does not follow the body, so more skin ends up against it and is hidden by the mask:
    # the limit on hidden skin is per style
    # plate, "isotropic": True - each piece (and each sleeve or thigh) grows by one factor in every direction,
    # about the joint it hangs from: its shape stays exactly as modelled, only its size follows the body
    "styles": {"plate": {"rigid": True, "isotropic": True, "isotropic_max": 0.45,
                         "quantile": 0.85, "taper": 0.2, "extra_gap_m": 0.004,
                         "wall_max_axis_dot": 0.8, "max_poke_masked_body_pct": 8.0},
               "leather": {"rigid": False, "quantile": 0.9, "taper": 0.0, "extra_gap_m": 0.0,
                           "wall_max_axis_dot": 2.0, "max_poke_masked_body_pct": 5.0}},
    "exterior_rays": 12, "exterior_escape_fraction": 0.5,
    "mask": {"max_cover_distance_m": 0.12, "rays": 16, "occluded_fraction": 0.85, "erode_rings": 1},
    "poke_depth_m": 0.03,              # armour found this close under visible skin counts as poke-through
    "skin": {"neighbours": 6, "smooth_iterations": 8, "max_influences": 4},
    "pose_fractions": [0.0, 0.25, 0.5, 0.75],
    "pose_clips": [],                  # clips to test; empty = every clip of the poses file
    "render_resolution": 900,
    "gates": {"max_exterior_penetration_area_cm2": 25.0, "max_pose_poke_area_cm2": 150.0},
}
VIEWS = {"front": (0, -1, 0), "back": (0, 1, 0), "left": (1, 0, 0), "right": (-1, 0, 0),
         "three_quarter_front": (0.65, -0.70, 0.25), "three_quarter_back": (-0.65, 0.70, 0.25)}
BODY_COLOR, ARMOUR_COLOR = (0.85, 0.62, 0.50, 1.0), (0.62, 0.64, 0.68, 1.0)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def merge(base: dict, override: dict, path: str = "config") -> dict:
    out = dict(base)
    for key, value in override.items():
        if key not in base:
            raise ValueError(f"Unknown option: {path}.{key}")
        out[key] = merge(base[key], value, f"{path}.{key}") if isinstance(base[key], dict) else value
    return out


# --------------------------------------------------------------------------- inputs

class Body:
    def __init__(self, rig_path: Path, profile: dict):
        data = json.loads(rig_path.read_text(encoding="utf-8"))
        mesh = next((m for m in data["meshes"] if m["name"] == profile["mesh"]), None)
        if mesh is None:
            raise ValueError(f"Body mesh not found in rig data: {profile['mesh']}")
        surface, influences = mesh["surfaces"][0], profile["influences_per_vertex"]
        to_blender = (lambda v: np.asarray(v, dtype=np.float64) @ core.GODOT_TO_BLENDER.T) if profile["rig_frame"] == "godot" \
            else (lambda v: np.asarray(v, dtype=np.float64))
        self.positions = to_blender(surface["positions"])
        triangles = np.asarray(surface["indices"], dtype=np.int64).reshape(-1, 3)
        self.flipped = core.signed_volume(self.positions, triangles) < 0
        self.triangles = triangles[:, ::-1].copy() if self.flipped else triangles      # outward normals
        self.bone_names = [bind["name"] for bind in mesh["binds"]]
        self.bone_ids = np.asarray(surface["bone_indices"], dtype=np.int64).reshape(-1, influences)
        weights = np.asarray(surface["weights"], dtype=np.float64).reshape(-1, influences)
        self.weights = weights / np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
        self.dominant = core.dominant_bone(self.bone_ids, self.weights)
        self.dense = core.dense_weights(self.bone_ids, self.weights, len(self.bone_names))
        self.rest = {bone["name"]: core.godot_matrix(bone["global_rest"]) if profile["rig_frame"] == "godot"
                     else np.asarray(bone["global_rest"]) for bone in data["bones"]}
        self.rig_bones = data["bones"]
        self.heads = {name: matrix[:3, 3] for name, matrix in self.rest.items()}
        self.profile = profile
        self.normals = core.vertex_normals(self.positions, self.triangles)
        self.vertex_area = core.vertex_areas(self.positions, self.triangles)
        self.face_bone = self.dominant[self.triangles[:, 0]]
        self.sdf = SurfaceSDF(self.positions, self.triangles)
        # the plane the body is symmetric about, which side of it is the character's left, and how the two
        # sides are told apart in bone names. Without it: x = 0, left on +x, "Left"/"Right"
        mirror = {"axis": "x", "at_m": 0.0, "left": "+", "bone_tokens": {"left": "Left", "right": "Right"},
                  **profile.get("mirror", {})}
        self.mirror_axis, self.mirror_at = "xyz".index(mirror["axis"]), float(mirror["at_m"])
        self.left_sign = 1.0 if mirror["left"] == "+" else -1.0
        self.side_tokens = (mirror["bone_tokens"]["left"], mirror["bone_tokens"]["right"])
        self.mirror_signs = np.ones(3)
        self.mirror_signs[self.mirror_axis] = -1.0

    def mirrored(self, points: np.ndarray) -> np.ndarray:
        """Points reflected across the middle plane of the body."""
        out = np.asarray(points, dtype=np.float64) * self.mirror_signs
        out[..., self.mirror_axis] += 2.0 * self.mirror_at
        return out

    def twin_bone(self, bone: int) -> int:
        """Index of the same bone on the other side of the body (the bone itself when it has no side)."""
        name = self.bone_names[int(bone)]
        left, right = self.side_tokens
        for a, b in ((left, right), (right, left)):
            if a in name and name.replace(a, b) in self.bone_names:
                return self.bone_names.index(name.replace(a, b))
        return int(bone)

    def bones(self, roles: list[str]) -> list[int]:
        """Bone indices of roles or bone groups of the body profile."""
        out = []
        for role in roles:
            if role in self.profile["bones"]:
                out.append(self.bone_names.index(self.profile["bones"][role]))
            elif role in self.profile.get("bone_groups", {}):
                group = self.profile["bone_groups"][role]
                out += [i for i, name in enumerate(self.bone_names)
                        if name.startswith(group["prefix"]) and name not in group["exclude"]]
            else:
                raise ValueError(f"Unknown bone role or group: {role}")
        return out

    def head(self, role: str) -> np.ndarray:
        return self.heads[self.profile["bones"][role]]

    def region(self, roles: list[str]) -> tuple[np.ndarray, np.ndarray]:
        return core.region_bounds(self.positions, self.dominant, self.bones(roles))


RECESS_GROUP = "a3d_recess"      # vertex group the step before leaves on the vertices of a recessed lid


class Piece:
    def __init__(self, obj, slot_name: str, slot: dict):
        self.obj, self.name, self.slot_name, self.slot = obj, obj.name, slot_name, slot
        mesh = obj.data
        self.source = np.asarray([obj.matrix_world @ vertex.co for vertex in mesh.vertices], dtype=np.float64)
        mesh.calc_loop_triangles()
        self.triangles = np.asarray([tuple(t.vertices) for t in mesh.loop_triangles], dtype=np.int64)
        sides = np.asarray([polygon.loop_total for polygon in mesh.polygons])
        # the fit only moves vertices: quads stay quads in the .blend (a GLB stores them as triangle pairs)
        self.faces = {"faces": len(sides), "tri_faces": int((sides == 3).sum()), "quad_faces": int((sides == 4).sum()),
                      "ngon_faces": int((sides > 4).sum())}
        self.areas, self.face_normals = core.triangle_areas_normals(self.source, self.triangles)
        # a lid recessed by the step before is not a wall of the piece: the limb passes through it. Its
        # vertices come marked; the mark is read and dropped, so that it is not exported as a bone
        self.recess_vertex = np.zeros(len(self.source), dtype=bool)
        group = obj.vertex_groups.get(RECESS_GROUP)
        if group is not None:
            for vertex in mesh.vertices:
                self.recess_vertex[vertex.index] = any(g.group == group.index and g.weight > 0.5 for g in vertex.groups)
            obj.vertex_groups.remove(group)
        self.recess_face = self.recess_vertex[self.triangles].any(axis=1)
        self.low, self.high = self.source.min(axis=0), self.source.max(axis=0)
        self.placement = None
        # height and middle of the whole set, once all pieces are read
        self.set_height, self.set_centre, self.left_sign, self.mirror_axis = float(self.high[2] - self.low[2]), 0.0, 1.0, 0
        self.uniform_ratio = None      # the set-wide height ratio, also for slots sized on their own
        self.positions = self.source

    def classify(self, rays: int, escape_fraction: float) -> None:
        """Outer faces see the outside; cavity faces are enclosed by the piece itself."""
        tree = BVHTree.FromPolygons(self.source.tolist(), self.triangles.tolist(), all_triangles=True)
        centroids = self.source[self.triangles].mean(axis=1)
        directions = core.orient(core.hemisphere(rays), self.face_normals)
        reach = float(np.linalg.norm(self.high - self.low)) * 2
        escaped = np.zeros(len(centroids))
        for i, (origin, fan) in enumerate(zip(centroids + 1e-5 * self.face_normals, directions)):
            start = Vector(origin)
            escaped[i] = sum(tree.ray_cast(start, Vector(d), reach)[0] is None for d in fan) / rays
        self.exterior = escaped >= escape_fraction
        self.centroids = centroids

    def choose_samples(self, interior: int, exterior: int, seed: int = 0) -> None:
        rng = np.random.default_rng(seed)

        def pick(mask: np.ndarray, count: int) -> np.ndarray:
            ids = np.flatnonzero(mask & (self.areas > 0) & ~self.recess_face)
            if len(ids) <= count:
                return ids
            return rng.choice(ids, count, replace=False, p=self.areas[ids] / self.areas[ids].sum())
        self.sample_interior, self.sample_exterior = pick(~self.exterior, interior), pick(self.exterior, exterior)


# --------------------------------------------------------------------------- placement

def bbox_of(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return points.min(axis=0), points.max(axis=0)


def piece_axis(piece: Piece) -> tuple[np.ndarray, float, float]:
    """Long axis of a piece placed by an ``axis`` rule, and the heights of its two ends along it.

    The slot declares the axis the piece was drawn along. With ``"piece_axis_from": "mesh"`` that is only a
    hint of which way it points and the axis is measured: a gauntlet of a figure in A-pose runs along the
    arm, not along the vertical the slot expects.
    """
    rule = piece.slot["place"]
    axis = core.unit(rule["piece_axis"])
    if rule.get("piece_axis_from") == "mesh":
        axis = core.long_axis(piece.source, axis)
    height = piece.source @ axis
    return axis, float(height.min()), float(height.max())


def wrist_fraction(piece: Piece, body: Body | None = None, scale: float = 1.0) -> float:
    """Where the wrist of a gauntlet is, as a fraction of its length from the start of its axis.

    A number in the slot is taken as it is. ``{"from_finger_roots": true, "fallback": f}`` measures it: the
    fingers are found in the mesh, and the wrist is put as far behind their roots as the wrist of the body is
    behind the roots of its fingers. Without a body, or when the fingers cannot be told apart, ``fallback``.
    """
    rule = piece.slot["place"]
    setting = rule["piece_fraction"]
    if not isinstance(setting, dict):
        return float(setting)
    fingers = piece.slot.get("fingers")
    if body is None or fingers is None or not setting.get("from_finger_roots"):
        return float(setting["fallback"])
    axis, low, high = piece_axis(piece)
    length = high - low
    along = piece.source @ axis - (low + high) / 2
    tubes = core.finger_tubes(along, core.unique_edges(piece.triangles), fingers.get("min_vertices", 15), 0.0)
    names = list(fingers["chains"])
    if len(tubes) != len(names):
        return float(setting["fallback"])
    roots = float(np.mean(sorted(joined for _, joined in tubes)[1:]))        # without the thumb, the lowest
    start, end = (body.head(role) for role in rule["bones"])
    direction = core.unit(end - start)
    palm = []
    for name in names[1:]:
        ids = sorted(body.bones([fingers["chains"][name]]), key=lambda i: body.bone_names[i])
        first, second = (body.heads[body.bone_names[i]] for i in ids[:2])
        palm.append((first + fingers.get("start_fraction", 0.3) * (second - first) - body.head(rule["at_bone"])) @ direction)
    return float(np.clip((roots - float(np.mean(palm)) / scale) / length + 0.5, 0.05, 0.95))


def glove_frame(piece: Piece) -> tuple[np.ndarray, str]:
    """Hand frame of a glove piece and the hand it was modelled for (from thumb side and declared back)."""
    rule = piece.slot["place"]
    axis, low, high = piece_axis(piece)
    centre = (piece.low + piece.high) / 2
    along = (piece.source @ axis - low) / (high - low)
    frame = core.hand_frame(axis, piece.source[along > wrist_fraction(piece)], None)
    if rule["palm"].get("handedness_from") == "layout":
        # a set modelled as a figure facing -Y: the glove on +X is the left hand
        return frame, "left" if piece.left_sign * (centre[piece.mirror_axis] - piece.set_centre) > 0 else "right"
    # right hand: back of the hand = long axis x thumb side
    right = frame[:, 2] @ np.asarray(rule["palm"]["piece_back"], dtype=float) > 0
    return frame, "right" if right else "left"


def set_scale(piece: Piece, body: Body, ratio: float) -> float:
    """Scale of a set modelled as a whole figure: body height over set height, the same for every piece."""
    return float(np.ptp(body.positions[:, 2])) * ratio / piece.set_height


def foot_yaw(piece: Piece, body: Body, rule: dict) -> np.ndarray:
    """Turn about the vertical that points the foot of a boot the way the foot of the body points."""
    height = piece.high[2] - piece.low[2]
    foot = piece.source[piece.source[:, 2] < piece.low[2] + rule.get("piece_below", 0.2) * height][:, :2]
    shaft = piece.source[piece.source[:, 2] > piece.low[2] + 0.5 * height][:, :2].mean(axis=0)
    centred = foot - foot.mean(axis=0)
    direction = np.linalg.eigh(centred.T @ centred)[1][:, 1]
    direction = direction * np.sign(direction @ (foot.mean(axis=0) - shaft) or 1.0)
    start, end = (body.head(role) for role in rule["bones"])
    wanted = core.unit(np.append((end - start)[:2], 0.0))
    angle = math.atan2(wanted[1], wanted[0]) - math.atan2(direction[1], direction[0])
    return core.rotation(np.array([0.0, 0.0, (angle + math.pi) % (2 * math.pi) - math.pi]))


def lean(piece: Piece, body: Body) -> dict | None:
    """Shear an upright shaft along a leaning limb: it follows the shin and its sole stays flat on the ground."""
    rule = piece.slot.get("lean")
    if rule is None:
        return None
    top, bottom = (body.head(role) for role in rule["bones"])
    slope = (top[:2] - bottom[:2]) / (top[2] - bottom[2])
    above = np.clip(piece.positions[:, 2] - bottom[2], 0.0, None)
    piece.positions = piece.positions + np.concatenate([above[:, None] * slope[None, :], np.zeros((len(above), 1))], axis=1)
    return {"bones": rule["bones"], "slope_xy_per_m": slope.tolist(),
            "angle_deg": float(np.degrees(np.arctan(np.linalg.norm(slope))))}


def fit_length(piece: Piece, body: Body) -> dict | None:
    """Shorten the part of a piece that runs up a limb so that its rim stops short of the next joint.

    A gauntlet cuff drawn for a longer forearm would reach into the elbow and be pierced by the upper arm
    whenever the arm bends. The piece is compressed along the limb, from ``from_bone`` towards ``to_bone``;
    it is never stretched.
    """
    rule = piece.slot.get("fit_length")
    if rule is None:
        return None
    origin, far = body.head(rule["from_bone"]), body.head(rule["to_bone"])
    direction, (limb, after) = core.unit(far - origin), limb_lengths(body, rule["from_bone"], rule["to_bone"], rule.get("beyond_bone"))
    along = np.clip((piece.positions - origin) @ direction, 0.0, None)
    reach = float(along.max())
    fraction = rule_fraction(piece, "fit_length", rule)
    factor = core.chain_length(fraction, limb, after) / max(reach, 1e-9)
    # a number only ever shortens the piece; read from the asset, the rim goes where it was drawn, either way
    factor = float(np.clip(factor, LENGTH_DEFAULTS["min_factor"], LENGTH_DEFAULTS["max_factor"])) if rule.get("fraction") == DESIGN else min(1.0, factor)
    piece.positions = piece.positions + np.outer((factor - 1.0) * along, direction)
    return {"limb_m": limb, "fraction": fraction, "from_design": rule.get("fraction") == DESIGN, "design": piece.design.get("fit_length"),
            "reach_before_m": reach, "reach_after_m": reach * factor, "factor": factor}


def landmark_placement(piece: Piece, body: Body, roll: float = 0.0) -> core.Placement:
    """First estimate of rotation, uniform scale and position from the slot's landmark rule."""
    rule = piece.slot["place"]
    centre = (piece.low + piece.high) / 2
    size = piece.high - piece.low
    if rule["type"] == "bbox":
        ratio = rule["size"].get("set_height_ratio", piece.uniform_ratio)
        if ratio is not None:
            scale = set_scale(piece, body, ratio)
        else:
            axis = "xyz".index(rule["size"]["axis"])
            if "between" in rule["size"]:
                a, b = (body.head(role) for role in rule["size"]["between"])
                reference = float(np.linalg.norm(b - a))
            else:
                low, high = body.region(rule["size"]["region"])
                reference = float(high[axis] - low[axis])
            scale = reference * rule["size"]["ratio"] / size[axis]
        target = np.zeros(3)
        for i, name in enumerate("xyz"):
            anchor = rule[name]
            if "bone" in anchor:
                value = body.head(anchor["bone"])[i]
            else:
                low, high = body.region(anchor["region"])
                value = low[i] + anchor["body"] * (high[i] - low[i])
            # where the piece's anchored fraction lands once scaled about its centre
            target[i] = value + anchor.get("offset_m", 0.0) - scale * (anchor["piece"] - 0.5) * size[i]
        rotation0 = foot_yaw(piece, body, rule["yaw_to"]) if "yaw_to" in rule else np.eye(3)
        return core.Placement(centre, rotation0, scale, target - centre)
    if rule["type"] == "axis":
        start, end = (body.head(role) for role in rule["bones"])
        direction = core.unit(end - start)
        long_axis, low, high = piece_axis(piece)
        length = high - low
        scale = set_scale(piece, body, rule["size"]["set_height_ratio"]) if "set_height_ratio" in rule["size"] \
            else float(np.linalg.norm(end - start)) * rule["size"]["ratio"] / length
        piece.wrist = wrist_fraction(piece, body, scale)
        # the point of the centre line at ``piece_fraction`` of the length, measured from the start of the axis
        station = low + piece.wrist * length
        local = centre + (station - centre @ long_axis) * long_axis
        piece.cuff_line = None
        if rule.get("piece_axis_from") == "mesh":
            # the tube that runs up the limb, from its rim to the joint, decides where the piece sits and
            # which way it points: its centre line goes on the bone and its end at the joint goes on the joint
            point, cuff = core.centre_line(piece.source, long_axis, low, station)
            local = point + (station - point @ long_axis) / (cuff @ long_axis) * cuff
            piece.cuff_line = {"tilt_from_long_axis_deg": float(np.degrees(np.arccos(np.clip(cuff @ long_axis, -1.0, 1.0)))),
                               "tilt_from_declared_axis_deg": float(np.degrees(np.arccos(np.clip(
                                   cuff @ core.unit(rule["piece_axis"]), -1.0, 1.0))))}
            long_axis = cuff
        if "palm" in rule:
            # back of the glove on the back of the hand, fingers along the hand
            hand = body.positions[np.isin(body.dominant, body.bones(rule["palm"]["hand"]))]
            thumb = body.positions[np.isin(body.dominant, body.bones(rule["palm"]["thumb"]))]
            body_frame = core.hand_frame(direction, hand, thumb.mean(axis=0) - hand.mean(axis=0))
            body_back = body_frame[:, 2] * (1.0 if rule["palm"]["handedness"] == "right" else -1.0)
            piece_frame, _ = glove_frame(piece)
            piece_back = piece_frame[:, 2] * np.sign(piece_frame[:, 2] @ np.asarray(rule["palm"]["piece_back"], dtype=float))
            piece_back = core.unit(piece_back - (piece_back @ long_axis) * long_axis)
            target = np.stack([direction, body_back, np.cross(direction, body_back)], axis=1)
            source = np.stack([long_axis, piece_back, np.cross(long_axis, piece_back)], axis=1)
            rotation0 = core.rotation(direction * roll) @ target @ source.T
        else:
            rotation0 = core.rotation(direction * roll) @ core.rotation_between(long_axis, direction)
        anchor = body.head(rule["at_bone"])
        return core.Placement(centre, rotation0, scale, anchor - scale * rotation0 @ (local - centre) - centre)
    raise ValueError(f"Unknown placement type: {rule['type']}")


def make_residual(piece: Piece, body: Body, placement: core.Placement, config: dict, centroids: np.ndarray | None = None):
    centroids = piece.centroids if centroids is None else centroids
    interior, exterior = centroids[piece.sample_interior], centroids[piece.sample_exterior]
    ignore = np.asarray(body.bones(piece.slot.get("ignore_bones", [])), dtype=np.int64)
    radius = float(np.linalg.norm(piece.high - piece.low)) / 2 * placement.scale0
    # each surface weighs as much as its share of the piece: a small cavity must not outvote the whole shell
    share = float(piece.areas[~piece.exterior].sum() / piece.areas.sum())
    wi = np.full(len(interior), share / max(len(interior), 1))
    we = np.full(len(exterior), (1.0 - share) / max(len(exterior), 1))

    def weights(base: np.ndarray) -> np.ndarray:
        if not len(ignore):
            return base
        return np.where(np.isin(body.face_bone[body.sdf.last_faces], ignore), 0.0, base)

    def residual(parameters: np.ndarray) -> np.ndarray:
        sd_interior = body.sdf(placement.apply(interior, parameters))
        w_interior = weights(wi)
        sd_exterior = body.sdf(placement.apply(exterior, parameters))
        w_exterior = weights(we)
        fit = core.fit_residual(sd_interior, w_interior, sd_exterior, w_exterior, config["clearance_m"],
                                piece.slot["target_gap_m"], config["gap_weight"])
        return np.concatenate([fit, core.regularisation(parameters, radius, config["regularisation"])])
    return residual


def place(piece: Piece, body: Body, config: dict) -> dict:
    """Put the piece on the body by its landmark rule (size, orientation and position)."""
    rule = piece.slot["place"]
    rolls = [2 * math.pi * k / rule["rolls"] for k in range(rule["rolls"])] if rule.get("rolls") else [0.0]
    candidates = []
    for roll in rolls:
        placement = landmark_placement(piece, body, roll)
        start = make_residual(piece, body, placement, config)(np.zeros(10))
        candidates.append((float(start @ start), roll, placement))
    cost, roll, placement = min(candidates, key=lambda item: item[0])
    piece.placement, piece.positions = placement, placement.apply(piece.source)
    return {"roll_deg": math.degrees(roll), "roll_candidates": len(rolls), "cost_landmark": cost, **placement.summary()}


def seat(piece: Piece, body: Body, config: dict) -> dict:
    """Small rigid correction once the piece has the proportions of the body: it only finds its seat."""
    own = piece.slot.get("seat", {})                     # a slot may tighten the seat (boots stand on the ground)
    limits = core.Limits(**{**config["limits"], **{k: v for k, v in own.items() if k not in ("lock_z", "midline")}})
    low, high = piece.positions.min(axis=0), piece.positions.max(axis=0)
    placement = core.Placement((low + high) / 2, np.eye(3), 1.0, np.zeros(3))
    residual = make_residual(piece, body, placement, config, piece.positions[piece.triangles].mean(axis=1))
    coarse = np.array([1, 1, 1, 0, 0, 0, 0, 0, 0, 0], dtype=bool)
    full = np.ones(10, dtype=bool) if config["refine_scale"] else np.array([1, 1, 1, 1, 1, 1, 0, 0, 0, 0], dtype=bool)
    if own.get("lock_z"):
        coarse[2] = full[2] = False
    if own.get("midline"):
        # a piece that sits on the middle of the body (a suit, a helmet) only moves in that plane: forwards,
        # up, and nodding. Sideways, tilting or turning would leave it different on the two sides
        coarse[0] = full[0] = False
        full[4:6] = False
    if limits.rotation_deg <= 0:
        full[3:6] = False
    if limits.translation_m <= 0:
        # a piece already put where it belongs by its own steps (a gauntlet on the wrist, its cuff centred
        # on the forearm) keeps that place
        coarse[:3] = full[:3] = False
    parameters, first = core.gauss_newton(residual, np.zeros(10), limits, iterations=config["iterations"], active=coarse)
    parameters, second = core.gauss_newton(residual, parameters, limits, iterations=config["iterations"], active=full)
    placement.parameters = parameters
    piece.positions, piece.seat = placement.apply(piece.positions), placement
    at_limit = {"translation": bool(limits.translation_m > 0 and np.linalg.norm(parameters[:3]) >= limits.translation_m * 0.999),
                "rotation": bool(limits.rotation_deg > 0
                                 and np.linalg.norm(parameters[3:6]) >= math.radians(limits.rotation_deg) * 0.999),
                "scale": bool(config["refine_scale"] and abs(parameters[6]) >= math.log1p(limits.scale) * 0.999),
                "anisotropy": bool(config["refine_scale"]
                                   and np.any(np.abs(parameters[7:10]) >= math.log1p(limits.anisotropy) * 0.999))}
    summary = placement.summary()
    return {"translation_m": summary["translation_m"], "rotation_deg": summary["rotation_deg"],
            "scale": summary["scale"], "axis_scale": summary["axis_scale"],
            "coarse": first, "refine": second, "at_limit": at_limit}


def measure(piece: Piece, body: Body, covered: np.ndarray, config: dict) -> dict:
    """Distance of every face of the placed piece to the exposed body, split into cavity and outer surface.

    Faces whose nearest body triangle touches a covered vertex are left out: what happens there cannot be seen.
    """
    areas, _ = core.triangle_areas_normals(piece.positions, piece.triangles)
    sd = body.sdf(piece.positions[piece.triangles].mean(axis=1))
    ignored = covered[body.triangles[body.sdf.last_faces]].any(axis=1)
    piece.face_sd = sd

    def stats(mask: np.ndarray, floor: float) -> dict:
        mask = mask & ~ignored
        total = float(areas[mask].sum())
        if total <= 0:
            return {"area_m2": 0.0}
        order = np.argsort(sd[mask])
        cumulative = np.cumsum(areas[mask][order]) / total
        quantile = lambda q: float(sd[mask][order][min(np.searchsorted(cumulative, q), len(order) - 1)])
        return {"area_m2": total, "below_floor_area_pct": float(areas[mask & (sd < floor)].sum() / total * 100),
                "inside_body_area_cm2": float(areas[mask & (sd < 0)].sum() * 1e4),
                "inside_body_area_pct": float(areas[mask & (sd < 0)].sum() / total * 100),
                "min_m": float(sd[mask].min()), "p05_m": quantile(0.05), "median_m": quantile(0.5), "p95_m": quantile(0.95)}
    return {"cavity": stats(~piece.exterior, config["clearance_m"]), "outer": stats(piece.exterior, 0.0),
            "cavity_area_fraction": float(areas[~piece.exterior].sum() / areas.sum()),
            "over_hidden_body_area_fraction": float(areas[ignored].sum() / areas.sum())}


def twin_name(name: str, config: dict) -> str | None:
    """Name of the reference-side twin of a part or slot of the other side ("sleeve.L" -> "sleeve.R"), if any."""
    reference = config.get("symmetry_reference")
    other = {"R": "L", "L": "R"}.get(reference)
    return name[:-1] + reference if other and name.endswith("." + other) else None


def reference_first(items: list, name_of, config: dict) -> list:
    return sorted(items, key=lambda item: twin_name(name_of(item), config) is not None)


def part_share(piece: Piece, body: Body, part: dict) -> np.ndarray:
    """How much of each vertex belongs to a sub-part: as far as the body next to it follows the part's bones.

    Smoothed over the mesh, so that the part fades into the piece and nothing tears. Read where the piece
    stands when it is called: the parts of a piece are read one after the other as each is turned to its limb.
    """
    base = piece.positions
    index, distance = core.nearest_k(base, body.positions, 4)
    kernel = 1.0 / np.maximum(distance, 1e-4) ** 2
    owned = body.dense[:, body.bones(part["bones"])].sum(axis=1)
    weight = (kernel * owned[index]).sum(axis=1) / kernel.sum(axis=1)
    edges = core.unique_edges(piece.triangles)
    degree = np.bincount(edges.ravel(), minlength=len(base)).astype(np.float64)
    for _ in range(part.get("smooth_iterations", 12)):
        total = np.zeros(len(base))
        np.add.at(total, edges[:, 0], weight[edges[:, 1]])
        np.add.at(total, edges[:, 1], weight[edges[:, 0]])
        weight = 0.5 * weight + 0.5 * total / np.maximum(degree, 1.0)
    return weight


def modelled_direction(body: Body, part: dict) -> np.ndarray:
    """Where a sub-part points as modelled: hanging down, unless the slot says along which bones it was placed."""
    if "from_bones" in part:
        return core.unit(np.subtract(*(body.head(role) for role in part["from_bones"][::-1])))
    return np.array([0.0, 0.0, -1.0])


def bend_parts(piece: Piece, body: Body, config: dict) -> list[dict]:
    """Rotate declared sub-parts (e.g. sleeves) about a joint so that the limb passes through them.

    A vertex belongs to the part as far as the body next to it follows the part's bones (smoothed over the
    mesh, so there is no tear). Sleeves are assumed to be modelled hanging down: the search starts from
    fractions of the rotation that takes "down" to the limb direction.
    """
    out, found = [], {}
    piece.part_weights = {}
    for part in reference_first(piece.slot.get("parts", []), lambda item: item["name"], config):
        pivot = body.head(part["pivot_bone"])
        base = piece.positions.copy()
        weight = part_share(piece, body, part)
        piece.part_weights[part["name"]] = weight
        face_weight = weight[piece.triangles].mean(axis=1)
        interior = np.intersect1d(piece.sample_interior, np.flatnonzero(face_weight > 0.5))
        exterior = np.intersect1d(piece.sample_exterior, np.flatnonzero(face_weight > 0.5))
        radius = float(np.sqrt(((base[weight > 0.5] - pivot) ** 2).sum(axis=1).mean())) if (weight > 0.5).any() else 0.1

        # a limb swings about its joint; turning about its own length is not a way to fit it
        # where the part points as modelled: hanging down, unless the slot says along which bones it was placed
        modelled = modelled_direction(body, part)
        limb = core.unit(core.unit(body.head(part["toward_bone"]) - pivot) + modelled)
        twist_weight = float(part.get("twist_weight", 1.0))

        def bent(points: np.ndarray, w: np.ndarray, parameters: np.ndarray) -> np.ndarray:
            linear = np.exp(parameters[6]) * core.rotation(parameters[3:6])
            return points + w[:, None] * ((points - pivot) @ linear.T + pivot - points)

        def residual(parameters: np.ndarray) -> np.ndarray:
            moved = bent(base, weight, parameters)
            centroids = moved[piece.triangles].mean(axis=1)
            count = max(len(interior) + len(exterior), 1)
            fit = core.fit_residual(body.sdf(centroids[interior]), np.full(len(interior), 1.0 / count),
                                    body.sdf(centroids[exterior]), np.full(len(exterior), 1.0 / count),
                                    config["clearance_m"], piece.slot["target_gap_m"], config["gap_weight"])
            return np.concatenate([fit, np.sqrt(config["regularisation"] * 0.1) * radius * parameters[3:7],
                                   [twist_weight * radius * (parameters[3:6] @ limb)]])
        limits = core.Limits(rotation_deg=part["max_rotation_deg"],
                             scale=0.0 if config["shape_locked"] else part.get("max_scale", 0.0))
        active = np.array([0, 0, 0, 1, 1, 1, 1, 0, 0, 0], dtype=bool)
        # a sleeve hanging beside a raised limb is a local minimum: optimise from several swings towards the limb
        full = core.rotation_between(modelled, body.head(part["toward_bone"]) - pivot)
        angle = np.arccos(np.clip((np.trace(full) - 1) / 2, -1.0, 1.0))
        axis = np.array([full[2, 1] - full[1, 2], full[0, 2] - full[2, 0], full[1, 0] - full[0, 1]])
        swing = axis / max(np.linalg.norm(axis), 1e-12) * min(angle, np.radians(part["max_rotation_deg"]) * 0.95)
        if twin_name(part["name"], config) in found:
            # the other side repeats the turn of the reference side, reflected across the middle of the body
            # a shift reflects like a point, a turn like its opposite
            parameters = found[twin_name(part["name"], config)] * np.concatenate([body.mirror_signs, -body.mirror_signs, np.ones(4)])
            log = {"mirrored_from": twin_name(part["name"], config)}
        elif part.get("fit", True):
            runs = [core.gauss_newton(residual, np.concatenate([np.zeros(3), swing * fraction, np.zeros(4)]),
                                      limits, iterations=config["iterations"], active=active)
                    for fraction in (0.0, 1 / 3, 2 / 3, 1.0)]
            parameters, log = min(runs, key=lambda run: run[1]["cost_final"])
            log = {**log, "start_costs": [run[1]["cost_final"] for run in runs]}
        else:
            # a part with no room inside (the hand of a gauntlet) has nothing to fit: it takes the bone's direction
            parameters, log = np.concatenate([np.zeros(3), swing, np.zeros(4)]), {"aligned_to_bone": True}
        found[part["name"]] = parameters
        piece.positions = bent(base, weight, parameters)
        out.append({"name": part["name"], "pivot_bone": part["pivot_bone"], "vertices": int((weight > 0.5).sum()),
                    "rotation_vector_deg": np.degrees(parameters[3:6]).tolist(),
                    "rotation_deg": float(np.degrees(np.linalg.norm(parameters[3:6]))),
                    "scale": float(np.exp(parameters[6])),
                    "at_limit": bool(np.linalg.norm(parameters[3:6]) >= np.radians(part["max_rotation_deg"]) * 0.999), **log})
    return out


# how the mouth of a tube is read and centred; a part overrides any of these with ``"centre": {...}``
CENTRE_DEFAULTS = {"mouth": 0.25,        # share of the tube, from its far end, read as the mouth
                   "stations": 3, "sectors": 16, "closed": 0.75,     # heights and directions the cavity is read in
                   "slab_m": 0.03,       # thickness of the slice of limb whose middle is taken
                   "rounds": 4, "tolerance_m": 0.001}
# how a collar is read and closed on the neck; the slot rule ``collar`` overrides any of these
COLLAR_DEFAULTS = {"gap_m": 0.012, "reach_m": 0.2, "blend_m": 0.04, "max_in": 0.3, "max_out": 0.5,
                   "base_blend_m": 0.015,    # depth below the base of the neck over which the collar leaves the fit of the trunk
                   "max_shift_m": 0.03,      # furthest the collar is moved to put the neck in its middle
                   "centre_front": True,     # also put the neck in the middle of the collar front to back
                   "widest_percentile": 85,  # the flanks: the part of a ring this far out across the body
                   "rim_from": 0.6,      # the rim is the collar above this share of its height
                   "top_percentile": 98, "sectors": 12,
                   "neck_percentile": 90, "collar_percentile": 10,     # outer skin of the neck, inner wall of the collar
                   "side_percentiles": [5, 95]}                        # the two flanks of the neck


def centre_parts(piece: Piece, body: Body) -> list[dict]:
    """Put the limb through the middle of the mouth of the tubes of a piece (the legs of a suit): parts with ``"centre": true``.

    Where the limb comes out of the tube is where an off-centre leg shows, and it is the one place where the
    tube is a clean ring: higher up its inner wall jumps from one overlapping plate to the next. The middle
    of the cavity at the mouth is measured against the middle of the flesh of the limb (not of its bone),
    and the tube swings about its joint, as the limb itself does, until the two coincide. Done last, after
    the piece is sized and seated, so that nothing moves it off again. Each side is centred on its own: a
    generated suit is not symmetric, and the mirror of one side's swing would leave the other leg off-centre.
    """
    out = []
    for part in piece.slot.get("parts", []):
        if not part.get("centre"):
            continue
        weight = piece.part_weights[part["name"]]
        tube = weight > 0.5
        pivot = body.head(part["pivot_bone"])
        along = core.unit(body.head(part["toward_bone"]) - pivot)
        limb = body.positions[np.isin(body.dominant, body.bones(part["bones"][:1]))]
        settings = {**CENTRE_DEFAULTS, **(part["centre"] if isinstance(part["centre"], dict) else {})}
        mouth = settings["mouth"]

        def measure() -> tuple[np.ndarray, float] | None:
            piece_h, limb_h = piece.positions[tube] @ along, limb @ along
            low, high = max(piece_h.min(), limb_h.min()), min(piece_h.max(), limb_h.max())
            heights, offsets = core.cavity_offsets(piece.positions[tube], limb, along, high - mouth * (high - low), high,
                                                    stations=settings["stations"], sectors=settings["sectors"], closed=settings["closed"])
            return (offsets.mean(axis=0), float(heights.mean())) if len(heights) else None
        found = measure()
        if found is None:
            out.append({"name": part["name"], "centred": False, "reason": "the tube does not close around the limb at its mouth"})
            continue
        before, swung = float(np.linalg.norm(found[0])), np.eye(3)
        for _ in range(settings["rounds"]):                  # the part blends into the piece: a few rounds settle it
            offset, height = found
            flesh = limb[np.abs(limb @ along - height) < settings["slab_m"]]
            middle = np.array([(flesh[:, k].min() + flesh[:, k].max()) / 2 for k in range(3)])
            middle = middle + (height - middle @ along) * along
            turn = core.rotation_between(middle + offset - pivot, middle - pivot)
            moved = (piece.positions - pivot) @ turn.T + pivot
            piece.positions = piece.positions + weight[:, None] * (moved - piece.positions)
            swung = turn @ swung
            found = measure()
            if found is None or np.linalg.norm(found[0]) < settings["tolerance_m"]:
                break
        angle = float(np.degrees(np.arccos(np.clip((np.trace(swung) - 1) / 2, -1.0, 1.0))))
        out.append({"name": part["name"], "centred": True, "swing_deg": angle, "off_centre_before_m": before,
                    "off_centre_after_m": float(np.linalg.norm(found[0])) if found is not None else None})
    return out


def collar_share(piece: Piece, body: Body, standing: bool = False) -> np.ndarray | None:
    """How much of each vertex is collar (slot rule ``collar``): 1 above the base of the neck, 0 a blend below it.

    ``standing`` reads only the wall that stands on the trunk: it fades over ``base_blend_m`` below the base
    of the neck instead of ``blend_m``, so that the plates the collar stands on stay with the trunk.
    """
    rule = piece.slot.get("collar")
    if rule is None:
        return None
    rule = {**COLLAR_DEFAULTS, **rule}
    base = body.head(rule["bone"])
    axis = core.unit(body.head(rule["toward"]) - base)
    relative = piece.positions - base
    height = relative @ axis
    radius = np.linalg.norm(relative - np.outer(height, axis), axis=1)
    blend = rule["blend_m"]
    along = np.clip(height / (rule["base_blend_m"] if standing else blend) + 1.0, 0.0, 1.0)
    return along * np.clip((rule["reach_m"] + blend - radius) / blend, 0.0, 1.0)


def fit_collar(piece: Piece, body: Body) -> dict | None:
    """Close the collar of a suit on the neck (slot rule ``collar``) and put it in the middle of it.

    The collar is what stands above the base of the neck within ``reach_m`` of its axis. Its rim is measured
    against the neck in sectors, each with its mirror across the body, and the collar is narrowed about the
    axis of the neck: nothing at its base, and at the rim as much as leaves ``gap_m`` where it is tightest.
    One factor at each height, so the collar keeps its outline; it tapers towards the neck like a funnel.
    Then it is moved sideways until the neck is in the middle of it: the same on the left and on the right.
    """
    rule = piece.slot.get("collar")
    if rule is None:
        return None
    rule = {**COLLAR_DEFAULTS, **rule}
    base = body.head(rule["bone"])
    axis = core.unit(body.head(rule["toward"]) - base)
    side = np.eye(3)[body.mirror_axis]                     # across the body; the axis of a neck lies in its middle plane
    side = core.unit(side - (side @ axis) * axis)
    front = np.cross(axis, side)
    neck = body.positions[np.isin(body.dominant, body.bones(rule["body"]))]

    def polar(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        relative = points - base
        return relative @ axis, relative @ side, relative @ front
    height, x, y = polar(piece.positions)
    radius = np.hypot(x, y)
    blend = rule["blend_m"]
    inside = np.clip((rule["reach_m"] + blend - radius) / blend, 0.0, 1.0)      # not the pauldrons beside it
    collar = (height > 0) & (inside > 0)
    if collar.sum() < 30:
        return {"fitted": False, "reason": "nothing of the piece stands above the base of the neck"}
    top = float(np.percentile(height[collar & (inside >= 1.0)], rule["top_percentile"])) if (collar & (inside >= 1.0)).any() \
        else float(height[collar].max())
    rim = collar & (height > rule["rim_from"] * top)
    neck_h, neck_x, neck_y = polar(neck)
    neck_rim = (neck_h > rule["rim_from"] * top) & (neck_h < top)
    sectors = rule["sectors"]
    # the whole collar takes the change, fading into the trunk below the base of the neck
    # narrowed and centred like a funnel: nothing at its base, where it stands on the trunk, all of it at the rim.
    # Widened, the whole collar grows, fading into the trunk below the base of the neck
    funnel, whole = np.clip(height / max(top, 1e-9), 0.0, 1.0) * inside, collar_share(piece, body)
    ramp = funnel
    # sized where it shows, at the rim: lower down a neck widens into the shoulders, which a ring cannot follow
    zone, neck_zone = rim, neck_rim
    before = piece.positions
    inner = rule["collar_percentile"]

    def centre() -> np.ndarray:
        """Move the collar until the neck is in the middle of it: left to right, and front to back.

        The middle of a ring is read where it is widest: across the body at its two flanks, and front to back
        at the height of those flanks (a collar open at the front has no front to read).
        """
        _, x, y = polar(piece.positions)
        shift = np.zeros(2)
        flank = rim & (np.abs(y) < np.abs(x))
        if (flank & (x > 0)).sum() >= 4 and (flank & (x < 0)).sum() >= 4:
            low, high = rule["side_percentiles"]
            shift[0] = (np.percentile(x[flank & (x > 0)], inner) + np.percentile(x[flank & (x < 0)], 100 - inner)) / 2 \
                - (np.percentile(neck_x[neck_rim], low) + np.percentile(neck_x[neck_rim], high)) / 2
        if rule["centre_front"] and rim.sum() >= 8 and neck_rim.sum() >= 8:
            widest = lambda px, py: float(py[np.abs(px) >= np.percentile(np.abs(px), rule["widest_percentile"])].mean())
            shift[1] = widest(x[rim], y[rim]) - widest(neck_x[neck_rim], neck_y[neck_rim])
        shift = np.clip(shift, -rule["max_shift_m"], rule["max_shift_m"])
        piece.positions = piece.positions - np.outer(ramp * shift[0], side) - np.outer(ramp * shift[1], front)
        return shift
    first = centre()
    # mirrored across the body: sector k and its mirror are read together
    fold = lambda px, py: np.floor(np.arctan2(py, np.abs(px)) % (2 * np.pi) / (2 * np.pi) * sectors).astype(int) % sectors
    height, x, y = polar(piece.positions)
    radius = np.hypot(x, y)
    ratios = []
    for k in range(sectors):
        own, flesh = zone & (fold(x, y) == k), neck_zone & (fold(neck_x, neck_y) == k)
        if own.sum() >= 4 and flesh.sum() >= 4:
            ratios.append((np.percentile(np.hypot(neck_x, neck_y)[flesh], rule["neck_percentile"]) + rule["gap_m"])
                          / np.percentile(radius[own], inner))
    if not ratios:
        piece.positions = before
        return {"fitted": False, "reason": "collar and neck share no height"}
    wanted = float(max(ratios))
    factor = float(np.clip(wanted, 1.0 - rule["max_in"], 1.0 + rule["max_out"]))
    scale = 1.0 + (whole if factor > 1.0 else funnel) * (factor - 1.0)
    piece.positions = base + np.outer(height, axis) + np.outer(x * scale, side) + np.outer(y * scale, front)
    second = centre()
    moved = np.linalg.norm(piece.positions - before, axis=1)
    return {"fitted": True, "vertices": int(collar.sum()), "rim_height_m": top, "factor": factor, "wanted": wanted,
            "at_limit": bool(abs(wanted - factor) > 1e-9), "sideways_shift_m": float(first[0] + second[0]),
            "forward_shift_m": float(first[1] + second[1]), "vertex_shift_max_m": float(moved.max())}


def articulate_fingers(piece: Piece, body: Body, keep_shape: bool = False) -> dict | None:
    """Lay each finger of a gauntlet along the finger of the body and give its phalanges the finger bones.

    Only for gauntlets whose fingers are separate in the mesh for most of their length. With fused fingers
    the piece is left as it is (it still inherits finger weights from the body) and the report says why.
    """
    rule = piece.slot.get("fingers")
    if rule is None:
        return None
    wrist = body.head(rule["wrist"])
    forward = core.unit(body.head(rule["toward"]) - wrist)
    edges = core.unique_edges(piece.triangles)
    tubes = core.finger_tubes((piece.positions - wrist) @ forward, edges, rule.get("min_vertices", 15),
                              rule.get("min_height_m", 0.02))
    names = list(rule["chains"])                         # thumb first, then from the thumb side outwards
    out = {"tubes_found": len(tubes), "articulated": False}
    if len(tubes) != len(names):
        out["reason"] = f"{len(tubes)} separate tubes in the mesh, {len(names)} fingers expected"
        return out
    tubes.sort(key=lambda tube: tube[1])                 # the thumb leaves the hand lowest
    chains = {}
    for name in names:
        ids = sorted(body.bones([rule["chains"][name]]), key=lambda i: body.bone_names[i])
        heads = [body.heads[body.bone_names[i]] for i in ids]
        flesh = body.positions[np.isin(body.dominant, ids[-1:])]
        last = core.unit(heads[-1] - heads[-2])
        tip = heads[-1] + last * (float(((flesh - heads[-1]) @ last).max()) + rule.get("tip_margin_m", 0.004))
        chains[name] = (ids, np.asarray(heads + [tip]))
    across = chains[names[1]][1][0] - chains[names[-1]][1][0]
    across = core.unit(across - (across @ forward) * forward)
    rest = sorted(tubes[1:], key=lambda tube: -float(piece.positions[tube[0]].mean(axis=0) @ across))
    ordered = list(zip(names, [tubes[0]] + rest))
    borders = {}
    for name, (members, _) in ordered:
        inside = np.zeros(len(piece.positions), dtype=bool)
        inside[members] = True
        borders[name] = np.intersect1d(np.unique(edges[inside[edges[:, 0]] != inside[edges[:, 1]]]), members)
    # a finger leaves the palm a little past its first joint; the thumb only past its first bone, which lies
    # inside the palm
    start_fraction = {name: rule.get("start_fraction", 0.3) for name in names}
    start_fraction[names[0]] = rule.get("thumb_start_fraction", 0.9)
    start_at = lambda name: start_fraction[name] * float(np.linalg.norm(chains[name][1][1] - chains[name][1][0]))
    # palm first: the roots of the four fingers go onto the roots of the fingers of the body (length and
    # spread of the palm, about the wrist), so that every finger starts where its bones are
    frame = np.stack([forward, across, np.cross(forward, across)], axis=1)
    own_roots = np.stack([piece.positions[borders[name]].mean(axis=0) for name in names[1:]])
    body_roots = np.stack([core.along_polyline(chains[name][1], np.array([start_at(name)]))[0] for name in names[1:]])
    own_local, body_local = (own_roots - wrist) @ frame, (body_roots - wrist) @ frame
    limit = 0.0 if keep_shape else rule.get("max_palm_scale", 0.15)
    length = float(np.clip(body_local[:, 0].mean() / own_local[:, 0].mean(), 1 / (1 + limit), 1 + limit))
    spread = float(np.clip(np.ptp(body_local[:, 1]) / max(np.ptp(own_local[:, 1]), 1e-6), 1 / (1 + limit), 1 + limit))
    shift = float(body_local[:, 1].mean() - spread * own_local[:, 1].mean())
    part_weight = next(iter(getattr(piece, "part_weights", {}).values()), None)
    grip = np.ones(len(piece.positions)) if part_weight is None else part_weight
    local = (piece.positions - wrist) @ frame
    fitted = wrist + (local * [length, spread, 1.0] + [0.0, shift, 0.0]) @ frame.T
    piece.positions = piece.positions + grip[:, None] * (fitted - piece.positions)
    out["palm"] = {"length_scale": length, "spread_scale": spread, "sideways_shift_m": shift,
                   "roots_before_m": own_local[:, :2].tolist(), "roots_of_body_m": body_local[:, :2].tolist()}
    plans = []
    for name, (members, joined_at) in ordered:
        ids, chain = chains[name]
        border = borders[name]
        base = piece.positions[border].mean(axis=0)
        points = piece.positions[members]
        tip = points[np.linalg.norm(points - base, axis=1).argmax()]
        segments = np.linalg.norm(np.diff(chain, axis=0), axis=1)
        plans.append((name, members, joined_at, ids, chain, base, tip, segments, border))
    out["fingers"] = {name: {"vertices": len(members), "joined_at_m": joined_at,
                             "length_m": float(np.linalg.norm(tip - base)),
                             "body_length_m": float(segments.sum() - start_at(name))}
                      for name, members, joined_at, _, _, base, tip, segments, _ in plans}
    # the thumb is always free only past the web it shares with the forefinger; the others must be whole
    short = [name for name, _, _, _, _, base, tip, segments, _ in plans[1:]
             if np.linalg.norm(tip - base) < rule.get("min_length_fraction", 0.55) * (segments.sum() - start_at(name))]
    if short:
        out["reason"] = f"fingers fused in the mesh: only the tips of {short} are separate"
        return out
    palm = piece.slot["place"]["palm"]
    hand = body.positions[np.isin(body.dominant, body.bones(palm["hand"]))]
    thumb = body.positions[np.isin(body.dominant, body.bones(palm["thumb"]))]
    back = core.hand_frame(forward, hand, thumb.mean(axis=0) - hand.mean(axis=0))[:, 2] \
        * (1.0 if palm["handedness"] == "right" else -1.0)
    hand_bone = body.bones([rule["wrist"]])[0]
    piece.finger_skin, moved = [], piece.positions.copy()
    roots = np.unique(np.concatenate([plan[-1] for plan in plans]))
    from_root = core.mesh_distance(piece.positions, edges, roots, 4 * rule.get("blend_m", 0.015))
    piece.finger_roots = from_root <= 2 * rule.get("blend_m", 0.015)
    for name, members, joined_at, ids, chain, base, tip, segments, border in plans:
        points = piece.positions[members]
        axis = core.unit(tip - base)
        own_radius = float(np.median(np.linalg.norm((points - base) - np.outer((points - base) @ axis, axis), axis=1)))
        flesh = body.positions[np.isin(body.dominant, ids)]
        chord = core.unit(chain[-1] - chain[0])
        on_chain = core.along_polyline(chain, np.clip((flesh - chain[0]) @ chord, 0.0, float(segments.sum())))
        body_radius = float(np.median(np.linalg.norm(flesh - on_chain, axis=1)))
        # an armoured finger may be thicker than the finger in it, not thinner
        scale = float(np.clip((body_radius + rule.get("gap_m", 0.003)) / max(own_radius, 1e-6),
                              1.0 if keep_shape else rule.get("min_scale", 0.9),
                              1.0 if keep_shape else rule.get("max_scale", 1.6)))
        # tip on tip: a free part shorter than the finger of the body covers the end of it
        start = max(start_at(name), float(segments.sum() - np.linalg.norm(tip - base)))
        laid, arc = core.lay_along_chain(points, base, tip, chain, back, start, scale=scale)
        # the finger keeps its own root on the gauntlet and takes the direction and the bends of the finger of
        # the body from there: moving it onto the bones would tear it off a palm that is thicker than the hand
        laid = laid + (base - core.along_polyline(chain, np.array([start]))[0])
        # the root of the finger stays with the palm: the change fades in with the distance, along the mesh,
        # from where the finger leaves the hand
        fade = np.clip(from_root[members] / rule.get("blend_m", 0.015), 0.0, 1.0)
        fade = (fade * fade * (3 - 2 * fade))[:, None]
        moved[members] = (1 - fade) * points + fade * laid
        joints = [0.0, float(segments[0]), float(segments[0] + segments[1])]
        piece.finger_skin.append((members, np.asarray([hand_bone] + ids),
                                  core.chain_weights(arc, joints, rule.get("joint_blend_m", 0.006))))
        out["fingers"][name].update(section_scale=scale, bones=[body.bone_names[i] for i in ids])
    piece.positions, out["articulated"] = moved, True
    return out


# how a part is given the length of the limb it covers; ``"lengthen": {...}`` overrides any of these
LENGTH_DEFAULTS = {"fraction": 1.0,          # where the rim lands along the limb: 1 = on the next joint, 1.5 = half the bone
                                             # after it; "design" = where the asset drew it (see ``read_design``)
                   "rim_percentile": 100,    # which point of the rim is read as its end (100 = the furthest)
                   "min_factor": 0.5, "max_factor": 1.5,
                   "snap": 0.15,             # drawn this close to a joint (in bone lengths), the rim belongs on it
                   "touch_share": 0.03}      # two pieces meet in the asset when this close, as a share of its height
DESIGN = "design"


def limb_lengths(body: Body, first: str, second: str, beyond: str | None) -> tuple[float, float]:
    """Length of the bone a rule runs along and of the one after it (the same again when the rule names none)."""
    one = float(np.linalg.norm(body.head(second) - body.head(first)))
    return one, one if beyond is None else float(np.linalg.norm(body.head(beyond) - body.head(second)))


def length_rules(piece: Piece) -> list[dict]:
    """Every rule of a slot that says how far the piece runs along a limb: of the piece and of its parts."""
    out = []
    if "fit_length" in piece.slot:
        rule = piece.slot["fit_length"]
        out.append({"key": "fit_length", "part": None, "bones": [rule["from_bone"], rule["to_bone"]], "rule": rule})
    for part in piece.slot.get("parts", []):
        rule = part.get("proportion", {}).get("lengthen")
        if isinstance(rule, dict):
            out.append({"key": f"lengthen {part['name']}", "part": part, "bones": part["proportion"]["axis"]["bones"], "rule": rule})
    return out


def read_design(pieces: list[Piece], body: Body) -> None:
    """Read on the asset, as it came, how far each piece runs along its limb, and decide where that puts its rim.

    The body is the ruler, the asset is the drawing. A set drawn with sleeves to the elbow and one drawn with
    sleeves to the middle of the forearm must not both be cut at the elbow. Right after the pieces are placed
    (one scale for the set, nothing resized yet) the asset stands on the body in its own proportions, which
    are human ones: the rim of each piece is read there in chain coordinates of the body (``core.chain_fraction``)
    and resolved with the piece it meets on the same limb (``core.design_reach``). The result is kept in
    ``piece.design[rule key]`` and used by every rule whose ``fraction`` is ``"design"``; it is reported for the others.
    """
    height = max(piece.high[2] for piece in pieces) - min(piece.low[2] for piece in pieces)
    drawn = {}
    for piece in pieces:
        piece.design = {}
        for item in length_rules(piece):
            settings = {**LENGTH_DEFAULTS, **{key: value for key, value in item["rule"].items() if key in LENGTH_DEFAULTS}}
            first, second = limb_lengths(body, *item["bones"], item["rule"].get("beyond_bone"))
            start = body.head(item["bones"][0])
            if item["part"] is None:
                members = np.ones(len(piece.positions), dtype=bool)
                along = (piece.positions - start) @ core.unit(body.head(item["bones"][1]) - start)
            else:
                # a part is read along the way it was modelled (a sleeve hanging down), before it is turned to the limb
                members = part_share(piece, body, item["part"]) > 0.5
                along = (piece.positions - body.head(item["part"]["pivot_bone"])) @ modelled_direction(body, item["part"])
            if not members.any():
                continue
            reach = float(np.percentile(np.clip(along[members], 0.0, None), settings["rim_percentile"]))
            entry = {"drawn_m": reach, "drawn": core.chain_fraction(reach, first, second), "bones": item["bones"],
                     "beyond_bone": item["rule"].get("beyond_bone"), "snap": settings["snap"], "meets": item["rule"].get("meets")}
            piece.design[item["key"]] = entry
            drawn[(piece.slot_name, item["key"])] = (piece, members, settings)
    for (slot, key), (piece, members, settings) in drawn.items():
        entry, other = piece.design[key], None
        meets = entry["meets"]
        if meets is not None:
            other_key = (meets["slot"], f"lengthen {meets['part']}" if "part" in meets else "fit_length")
            if other_key in drawn:
                neighbour, theirs, _ = drawn[other_key]
                # do the two touch in the asset as it came? measured there, in its own pose and units
                tree = KDTree(int(theirs.sum()))
                for index, point in enumerate(neighbour.source[theirs]):
                    tree.insert(Vector(point), index)
                tree.balance()
                step = max(1, int(members.sum()) // 2000)
                gap = min(tree.find(Vector(point))[2] for point in piece.source[members][::step])
                entry["gap_in_asset_m"], entry["touches"] = float(gap), bool(gap <= settings["touch_share"] * height)
                if entry["touches"]:
                    other = neighbour.design[other_key[1]]["drawn"]
                    entry["neighbour_drawn"] = other
        entry["fraction"], entry["why"] = core.design_reach(entry["drawn"], other, entry["snap"])


def rule_fraction(piece: Piece, key: str, rule: dict) -> float:
    """The fraction a length rule asks for: its own number, or the one read from the asset."""
    wanted = rule.get("fraction", LENGTH_DEFAULTS["fraction"])
    return piece.design[key]["fraction"] if wanted == DESIGN else float(wanted)


def length_from_body(piece: Piece, body: Body, rule: dict, share: np.ndarray, known: float | None, key: str) -> dict:
    """Give a part the length of the limb under it: its rim lands on ``fraction`` of the bone it runs along.

    The body is the measure: the bone from one joint to the next is how long a sleeve or a thigh plate may
    be, whatever the girth of the limb asks for. What lies beyond the first joint is stretched or compressed
    along the bone by one factor; what lies before it (a pauldron over the shoulder) stays as it is. ``known``
    is the factor of the reference side, which the other side repeats.
    """
    settings = {**LENGTH_DEFAULTS, **rule["lengthen"]}
    start, end = (body.head(role) for role in rule["axis"]["bones"])
    direction, (limb, after) = core.unit(end - start), limb_lengths(body, *rule["axis"]["bones"], rule["lengthen"].get("beyond_bone"))
    along = np.clip((piece.positions - start) @ direction, 0.0, None)
    reach = float(np.percentile(along[share > 0.5], settings["rim_percentile"]))
    fraction = rule_fraction(piece, key, rule["lengthen"])
    wanted = core.chain_length(fraction, limb, after) / max(reach, 1e-9)
    factor = float(np.clip(wanted, settings["min_factor"], settings["max_factor"])) if known is None else known
    piece.positions = piece.positions + np.outer(share * (factor - 1.0) * along, direction)
    return {"by": "body", "bones": rule["axis"]["bones"], "limb_m": limb, "fraction": fraction,
            "from_design": rule["lengthen"].get("fraction") == DESIGN, "design": getattr(piece, "design", {}).get(key),
            "reach_before_m": reach, "reach_after_m": reach * factor, "factor": factor, "wanted": float(wanted),
            "mirrored": known is not None, "at_limit": bool(known is None and abs(wanted - factor) > 1e-9)}


def relative_factor(piece: Piece, body: Body, rule: dict) -> dict:
    """Factor that gives a piece the size planned for it against another piece of the set.

    ``"relative_to": {"slot": "suit", "width": 0.287, "height": 0.295}`` reads the proportions of the design
    (a helmet 28.7% as wide and 29.5% as tall as the body armour) and applies them to the reference piece as
    it stands after its own fit. A piece of plate takes one factor: ``"by"`` picks the width, the height, or
    (default) the mean of the two, and the report keeps both so that the difference shows.
    """
    reference = piece.fitted[rule["slot"]]
    across, up = body.mirror_axis, 2
    wanted = {}
    for key, axis in (("width", across), ("height", up)):
        if key in rule:
            wanted[key] = float(rule[key] * np.ptp(reference.positions[:, axis]) / np.ptp(piece.positions[:, axis]))
    by = rule.get("by", "mean")
    factor = float(np.exp(np.mean(np.log(list(wanted.values()))))) if by == "mean" else wanted[by]
    return {"slot": rule["slot"], "by": by, "factor": factor, "factor_for": wanted,
            "reference_size_m": {"width": float(np.ptp(reference.positions[:, across])), "height": float(np.ptp(reference.positions[:, up]))},
            "size_after_m": {"width": float(np.ptp(piece.positions[:, across]) * factor), "height": float(np.ptp(piece.positions[:, up]) * factor)}}


def body_points(body: Body, roles: list[str]) -> np.ndarray:
    """Vertices and face centres of the body owned by the given bones."""
    owned = np.isin(body.dominant, body.bones(roles))
    faces = body.triangles[owned[body.triangles].all(axis=1)]
    return np.concatenate([body.positions[owned], body.positions[faces].mean(axis=1)])


def proportion_pivot(piece: Piece, body: Body, rule: dict, origin: np.ndarray) -> np.ndarray:
    """Point a piece grows about when it is resized by one factor: the place where it hangs on the body.

    ``"pivot": {"bone": role}`` is a joint (a gauntlet grows from the wrist, a suit from the neck);
    ``"pivot": "bottom"`` is the middle of the lowest part of the piece (a boot keeps its sole on the ground);
    ``"pivot": "body_top"`` is the top of the body under the piece (a helmet grows down from the crown).
    Without it: the first bone of a limb axis (shoulder, hip), or the middle of the body under the piece.
    """
    pivot = rule.get("pivot")
    if isinstance(pivot, dict):
        return body.head(pivot["bone"])
    if pivot == "bottom":
        low, high = piece.positions.min(axis=0), piece.positions.max(axis=0)
        return np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2]])
    if isinstance(rule["axis"], dict):
        return np.asarray(origin, dtype=np.float64)
    low, high = bbox_of(body_points(body, rule["body"]))
    if pivot == "body_top":
        return np.array([(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, high[2]])
    return (low + high) / 2


def proportion(piece: Piece, body: Body, config: dict) -> list[dict]:
    """Bring each cross-section of the piece (and of its parts) to the girth of the body under it."""
    if config["style"] not in config["styles"]:
        raise ValueError(f"Unknown style: {config['style']} (available: {sorted(config['styles'])})")
    style = config["styles"][config["style"]]
    jobs = []
    if "proportion" in piece.slot:
        rest = 1.0 - sum(piece.part_weights.values()) if piece.part_weights else None
        jobs.append(("piece", piece.slot["proportion"], None if rest is None else np.clip(rest, 0.0, 1.0)))
    for part in piece.slot.get("parts", []):
        if "proportion" in part:
            jobs.append((part["name"], part["proportion"], piece.part_weights[part["name"]]))
    out, factors, lengths = [], {}, {}
    for name, rule, weight in reference_first(jobs, lambda job: job[0], config):
        if isinstance(rule["axis"], dict):
            start, end = (body.head(role) for role in rule["axis"]["bones"])
            origin, axis = start, end - start
        else:
            origin, axis = np.zeros(3), np.eye(3)["xyz".index(rule["axis"])]
        # a slot may ask for the full fit whatever the style: section by section, as leather is fitted. For a
        # piece of plate that has to follow a limb closely (a boot on a shin thick at the calf)
        flexible = rule.get("shape") == "flexible"
        share = np.ones(len(piece.positions)) if weight is None else weight

        def sections(rigid: bool):
            # lids across an opening and soles are not walls: they would read as a piece with no room inside
            walls = (np.abs(core.vertex_normals(piece.positions, piece.triangles) @ core.unit(axis)) < style["wall_max_axis_dot"]) \
                & ~piece.recess_vertex
            return core.section_field(
                piece.positions[walls], body_points(body, rule["body"]), origin, axis, rule["gap_m"] + style["extra_gap_m"],
                step=config["proportion"]["step_m"], sectors=config["proportion"]["sectors"],
                harmonics=config["proportion"]["harmonics"], expectile=config["proportion"]["expectile"],
                max_in=rule["max_in_m"], max_out=rule["max_out_m"], piece_weight=None if weight is None else weight[walls],
                rigid=rigid, rigid_quantile=style["quantile"], rigid_taper=style["taper"])

        def one_factor(measured: dict) -> dict:
            """The factor a piece grows by under one factor per piece, with the side it copies and its limits."""
            # the two widths the rigid fit asks for, each averaged over the length; the larger one decides
            wanted = np.mean([measured["scale_across_axis"]["at_start"], measured["scale_across_axis"]["at_end"]], axis=0)
            low = 1.0 if rule["max_in_m"] == 0 else 1.0 / (1.0 + style["isotropic_max"])
            # a slot may cap its own growth: a gauntlet is sized by the hand in it, not by the forearm
            high = 1.0 + rule.get("isotropic_max", style["isotropic_max"])
            factor, copied = float(np.clip(wanted.max(), min(low, high), high)), None
            planned = relative_factor(piece, body, rule["relative_to"]) if "relative_to" in rule else None
            if planned is not None:
                # the size the design gives the piece against another one wins over what the body under it asks for
                factor = planned["factor"]
            if twin_name(name, config) in factors:
                factor, copied = factors[twin_name(name, config)], twin_name(name, config)   # the two sides grow alike
            factors[name] = factor
            return {"factor": factor, "wanted": float(wanted.max()), "pivot": proportion_pivot(piece, body, rule, origin).tolist(),
                    "capped_by_slot": "isotropic_max" in rule, "mirrored_from": copied, "relative_to": planned,
                    "at_limit": bool("isotropic_max" not in rule and planned is None and copied is None
                                     and abs(wanted.max() - factor) > 1e-9)}
        before, lengthened = piece.positions, None
        if isinstance(rule.get("lengthen"), dict):
            # the length of a part on a limb is the length of that limb, measured on the body
            lengthened = length_from_body(piece, body, rule, share, lengths.get(twin_name(name, config)), f"lengthen {name}")
            lengths[name] = lengthened["factor"]
        elif flexible and rule.get("lengthen") and style["rigid"] and style.get("isotropic"):
            # the full fit only widens a piece around the limb. Its length still has to follow the body (a
            # thigh plate reaches the knee, a sleeve the elbow): along the axis it grows by the one factor
            _, measured = sections(True)
            if "scale_across_axis" in measured:
                lengthened = one_factor(measured)
                direction, pivot = core.unit(axis), np.asarray(lengthened["pivot"])
                piece.positions = before + np.outer(share * (lengthened["factor"] - 1.0) * ((before - pivot) @ direction), direction)
        field, stats = sections(style["rigid"] and not flexible)
        if lengthened is not None:
            stats["lengthened"] = lengthened
        if field is not None:
            if not flexible and style.get("isotropic") and "scale_across_axis" in stats:
                stats["isotropic"] = one_factor(stats)
                if stats["isotropic"]["mirrored_from"]:
                    stats["mirrored_from"] = stats["isotropic"]["mirrored_from"]
                pivot = np.asarray(stats["isotropic"]["pivot"])
                piece.positions = piece.positions + share[:, None] * (stats["isotropic"]["factor"] - 1.0) * (piece.positions - pivot)
            else:
                # a collar is a ring around the neck, not a section of the trunk: drawn in to the back under it, it
                # would take the hollow between the shoulder blades. The trunk may push it out, never pull it in;
                # its own step closes it on the neck
                ring = collar_share(piece, body, standing=True) if name == "piece" else None
                shifted = field.apply(piece.positions, weight)
                if ring is not None and ring.any():
                    out_only = dataclasses.replace(field, max_in=0.0).apply(piece.positions, weight)
                    shifted = shifted + ring[:, None] * (out_only - shifted)
                piece.positions = shifted
        moved = np.linalg.norm(piece.positions - before, axis=1)
        stats["vertex_shift_m"] = {"median": float(np.median(moved)), "max": float(moved.max())}
        out.append({"name": name, **stats})
    return out


# --------------------------------------------------------------------------- mask and poke-through

def armour_tree(pieces: list[Piece], positions: dict[str, np.ndarray] | None = None) -> BVHTree:
    points, triangles, offset = [], [], 0
    for piece in pieces:
        current = piece.positions if positions is None else positions[piece.name]
        points.append(current)
        triangles.append(piece.triangles + offset)
        offset += len(current)
    return BVHTree.FromPolygons(np.concatenate(points).tolist(), np.concatenate(triangles).tolist(), all_triangles=True)


def body_mask(body: Body, pieces: list[Piece], tree: BVHTree, config: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    """Vertices hidden under the armour: enclosed by it (occlusion) or owned by a covered bone (hands, feet, head).

    Returns the mask to display (its border eroded, so the cut stays under the armour), the full covered set
    and statistics.
    """
    settings = config["mask"]
    near = np.asarray([tree.find_nearest(Vector(p))[3] or np.inf for p in body.positions]) <= settings["max_cover_distance_m"]
    local = core.hemisphere(settings["rays"])
    occluded = np.zeros(len(body.positions), dtype=bool)
    ids = np.flatnonzero(near)
    fans = core.orient(local, body.normals[ids])
    for vertex, fan in zip(ids, fans):
        origin = Vector(body.positions[vertex] + 1e-4 * body.normals[vertex])
        hits = sum(tree.ray_cast(origin, Vector(d), 1.0)[0] is not None for d in fan)
        occluded[vertex] = hits >= settings["occluded_fraction"] * settings["rays"]
    by_bone = np.zeros(len(body.positions), dtype=bool)
    for piece in pieces:
        by_bone |= np.isin(body.dominant, body.bones(piece.slot.get("mask_bones", [])))
    masked = core.erode(occluded | by_bone, body.positions, body.triangles, settings["erode_rings"]) | \
        core.erode(by_bone, body.positions, body.triangles, 0)
    return masked, occluded | by_bone, {"vertices": int(masked.sum()), "by_occlusion": int(occluded.sum()), "by_bone": int(by_bone.sum()),
                    "area_m2": float(body.vertex_area[masked].sum()),
                    "body_area_fraction": float(body.vertex_area[masked].sum() / body.vertex_area.sum())}


def poke_through(positions: np.ndarray, normals: np.ndarray, areas: np.ndarray, candidates: np.ndarray,
                 tree: BVHTree, depth: float) -> np.ndarray:
    """Visible vertices with armour just under the skin: there the body comes through the armour."""
    poking = np.zeros(len(positions), dtype=bool)
    for vertex in candidates:
        origin = Vector(positions[vertex] - 1e-4 * normals[vertex])
        poking[vertex] = tree.ray_cast(origin, Vector(-normals[vertex]), depth)[0] is not None
    return poking


def exposed(positions: np.ndarray, normals: np.ndarray, vertices: np.ndarray, tree: BVHTree, settings: dict) -> np.ndarray:
    """Which of ``vertices`` can be seen from outside: the armour does not close the view over them.

    Same test and threshold as the occlusion that builds the mask.
    """
    local = core.hemisphere(settings["rays"])
    out = np.zeros(len(vertices), dtype=bool)
    for k, (vertex, fan) in enumerate(zip(vertices, core.orient(local, normals[vertices]))):
        origin = Vector(positions[vertex] + 1e-4 * normals[vertex])
        hits = sum(tree.ray_cast(origin, Vector(d), 1.0)[0] is not None for d in fan)
        out[k] = hits < settings["occluded_fraction"] * settings["rays"]
    return out


# --------------------------------------------------------------------------- scene, skin, render, export

def build_body_object(body: Body, masked: np.ndarray):
    mesh = bpy.data.meshes.new("Body_Mesh")
    mesh.from_pydata(body.positions.tolist(), [], body.triangles.tolist())
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    mesh.update()
    obj = bpy.data.objects.new("Body", mesh)
    bpy.context.scene.collection.objects.link(obj)
    group = obj.vertex_groups.new(name="BodyMask")
    group.add(np.flatnonzero(masked).tolist(), 1.0, "REPLACE")
    modifier = obj.modifiers.new("BodyMask", "MASK")
    modifier.vertex_group, modifier.invert_vertex_group = "BodyMask", True
    return obj


def build_armature(body: Body, min_length: float = 0.03):
    armature = bpy.data.armatures.new("HostSkeleton")
    obj = bpy.data.objects.new("HostSkeleton", armature)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    created = {}
    children = {b["index"]: [c for c in body.rig_bones if c["parent"] == b["index"]] for b in body.rig_bones}
    for bone in body.rig_bones:
        matrix = body.rest[bone["name"]]
        kids = children[bone["index"]]
        length = float(np.mean([np.linalg.norm(body.rest[k["name"]][:3, 3] - matrix[:3, 3]) for k in kids])) if kids else min_length
        edit = armature.edit_bones.new(bone["name"])
        edit.head = Vector(matrix[:3, 3])
        edit.tail = Vector(matrix[:3, 3] + matrix[:3, 1] * max(length, min_length))
        edit.matrix = Matrix(matrix.tolist())
        edit.length = max(length, min_length)
        created[bone["index"]] = edit
    for bone in body.rig_bones:
        if bone["parent"] >= 0:
            created[bone["index"]].parent = created[bone["parent"]]
    bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def bind(obj, names: list[str], ids: np.ndarray, weights: np.ndarray, armature) -> None:
    for group in list(obj.vertex_groups):
        obj.vertex_groups.remove(group)
    groups = {int(bone): obj.vertex_groups.new(name=names[int(bone)]) for bone in np.unique(ids[weights > 0])}
    for vertex in range(len(ids)):
        for bone, weight in zip(ids[vertex], weights[vertex]):
            if weight > 0:
                groups[int(bone)].add([vertex], float(weight), "ADD")
    obj.parent = armature
    obj.matrix_parent_inverse = armature.matrix_world.inverted()
    obj.modifiers.new("Armature", "ARMATURE").object = armature


def setup_render(scene, resolution: int) -> None:
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.film_transparent = False
    scene.render.resolution_x = scene.render.resolution_y = resolution
    scene.render.image_settings.file_format = "PNG"
    shading = scene.display.shading
    shading.light, shading.color_type = "STUDIO", "OBJECT"
    shading.show_cavity, shading.cavity_type = True, "BOTH"
    scene.world = scene.world or bpy.data.worlds.new("World")
    scene.world.color = (0.14, 0.14, 0.15)
    camera = bpy.data.objects.new("FitCamera", bpy.data.cameras.new("FitCamera"))
    scene.collection.objects.link(camera)
    camera.data.type = "ORTHO"
    scene.camera = camera


def render_views(scene, low: np.ndarray, high: np.ndarray, directory: Path, prefix: str) -> list[np.ndarray]:
    centre, radius = (low + high) / 2, float(np.linalg.norm(high - low)) / 2
    images = []
    for name, direction in VIEWS.items():
        axis = Vector(direction).normalized()
        scene.camera.location = Vector(centre) + axis * radius * 4
        scene.camera.rotation_euler = axis.to_track_quat("Z", "Y").to_euler()
        scene.camera.data.ortho_scale, scene.camera.data.clip_end = radius * 2.05, radius * 10
        path = directory / f"{prefix}_{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(str(path))
        pixels = np.empty(image.size[0] * image.size[1] * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        images.append(pixels.reshape(image.size[1], image.size[0], 4))
        bpy.data.images.remove(image)
    return images


def save_image(pixels: np.ndarray, path: Path) -> None:
    image = bpy.data.images.new("sheet", pixels.shape[1], pixels.shape[0], alpha=True)
    image.pixels.foreach_set(np.ascontiguousarray(pixels, dtype=np.float32).ravel())
    image.filepath_raw, image.file_format = str(path), "PNG"
    image.save()
    bpy.data.images.remove(image)


def pose_matrices(poses_path: Path, body: Body, fractions: list[float]) -> list[tuple[str, float, np.ndarray]]:
    data = json.loads(poses_path.read_text(encoding="utf-8"))
    frame = (lambda m: core.godot_matrix(m)) if body.profile["rig_frame"] == "godot" else np.asarray
    out = []
    for pose in data["poses"]:
        if not any(abs(pose["fraction"] - f) < 1e-6 for f in fractions):
            continue
        by_name = {bone["name"]: bone for bone in pose["bones"]}
        matrices = np.stack([frame(by_name[name]["global_pose"]) @ np.linalg.inv(frame(by_name[name]["global_rest"]))
                             for name in body.bone_names])
        out.append((pose["clip"], float(pose["fraction"]), matrices))
    return out


def export_glb(path: Path, objects: list) -> dict:
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True, export_apply=False,
                              export_skins=True, export_yup=True)
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}


# --------------------------------------------------------------------------- pipeline

def execute(request: dict) -> int:
    out = Path(request["out"])
    job, body_profile, slot_profile = request["job"], request["body_profile"], request["slot_profile"]
    config = merge(DEFAULTS, job.get("config", {}))
    inputs = {name: Path(path) for name, path in request["inputs"].items()}
    hashes = {name: sha256(path) for name, path in inputs.items()}
    if hashes != request["input_sha256"]:
        raise ValueError("An input file changed after the launcher hashed it")
    (out / "renders").mkdir(exist_ok=True)

    bpy.ops.wm.open_mainfile(filepath=str(inputs["armour"]))
    scene = bpy.context.scene
    body = Body(inputs["rig"], body_profile)
    pieces = []
    for object_name, slot_name in job["pieces"].items():
        obj = scene.objects.get(object_name)
        if obj is None or obj.type != "MESH":
            raise ValueError(f"Armour object not found: {object_name}")
        if slot_name not in slot_profile["slots"]:
            raise ValueError(f"Unknown slot: {slot_name}")
        pieces.append(Piece(obj, slot_name, slot_profile["slots"][slot_name]))
    rigid = bool(config["styles"][config["style"]]["rigid"])
    config["keep_shape"], config["uniform_set_scale"] = config["keep_shape"] and rigid, config["uniform_set_scale"] and rigid
    # with one factor per piece nothing else may change a shape either: no squeezed palm, no shortened cuff
    config["symmetry_reference"] = config["symmetry_reference"] if rigid else None
    config["shape_locked"] = bool(config["keep_shape"] or (rigid and config["styles"][config["style"]].get("isotropic")))
    set_height = float(max(p.high[2] for p in pieces) - min(p.low[2] for p in pieces))
    k = body.mirror_axis
    set_centre = float(max(p.high[k] for p in pieces) + min(p.low[k] for p in pieces)) / 2
    for piece in pieces:
        piece.set_height, piece.set_centre, piece.left_sign, piece.mirror_axis = set_height, set_centre, body.left_sign, k
        if config["uniform_set_scale"] or config["keep_shape"]:
            piece.uniform_ratio = next((p.slot["place"]["size"]["set_height_ratio"] for p in pieces
                                        if "set_height_ratio" in p.slot["place"]["size"]), None)
    # gloves go to the hand they were modelled for, whatever the catalogue column was called
    handed = [piece for piece in pieces if "palm" in piece.slot["place"]]
    handedness = {piece.name: {"slot": piece.slot_name, "wanted": piece.slot["place"]["palm"]["handedness"],
                               "detected": glove_frame(piece)[1]} for piece in handed}
    wrong = [piece for piece in handed if handedness[piece.name]["wanted"] != handedness[piece.name]["detected"]]
    swapped = len(wrong) == 2 and handedness[wrong[0].name]["wanted"] != handedness[wrong[1].name]["wanted"]
    if swapped:
        a, b = wrong
        a.slot_name, b.slot_name, a.slot, b.slot = b.slot_name, a.slot_name, b.slot, a.slot
    for piece in handed:
        handedness[piece.name].update(final_slot=piece.slot_name,
                                      matches=piece.slot["place"]["palm"]["handedness"] == handedness[piece.name]["detected"])
    for obj in [o for o in scene.objects if o not in [p.obj for p in pieces]]:
        bpy.data.objects.remove(obj, do_unlink=True)

    report = {"name": job["name"], "style": config["style"], "blender_version": bpy.app.version_string, "config": config,
              "input_sha256": hashes, "body": {"vertices": len(body.positions), "triangles": len(body.triangles),
                                              "height_m": float(np.ptp(body.positions[:, 2])), "winding_flipped": body.flipped},
              "handedness": {"swapped": swapped, "pieces": handedness}, "pieces": {}}
    print(f"[fit] handedness {handedness} swapped {swapped}", flush=True)
    # a piece sized against another one (a helmet against the body armour) waits for that one to be fitted
    fitted, placements = {}, {}
    # first every piece is put on the body, with the one scale of the set and nothing resized: there the asset
    # stands in its own proportions, and how far each piece was drawn to run along its limb can be read
    for piece in pieces:
        piece.classify(config["exterior_rays"], config["exterior_escape_fraction"])
        piece.choose_samples(config["samples_interior"], config["samples_exterior"])
        placements[piece.name] = place(piece, body, config)
    read_design(pieces, body)
    for piece in sorted(pieces, key=lambda item: "relative_to" in item.slot.get("proportion", {})):
        piece.fitted = fitted
        entry = {"slot": piece.slot_name, "triangles": len(piece.triangles), **piece.faces, "placement": placements[piece.name],
                 "design": {key: {name: value for name, value in item.items() if name != "meets"} for key, item in piece.design.items()}}
        if hasattr(piece, "wrist"):
            entry["placement"]["wrist_fraction"] = piece.wrist
            entry["placement"]["cuff_line"] = piece.cuff_line
        # under one factor per piece nothing else changes a shape, unless the slot says its length always follows
        # the limb (a boot ends at the knee: past it, it would swing out of the thigh when the leg bends)
        always = (piece.slot.get("fit_length") or {}).get("always", False)
        entry["fit_length"] = fit_length(piece, body) if always or not config["shape_locked"] else None
        entry["lean"] = lean(piece, body)
        entry["parts"] = bend_parts(piece, body, config)
        entry["proportion"] = [] if config["keep_shape"] else proportion(piece, body, config)
        entry["seat"] = seat(piece, body, config)
        entry["centred_parts"] = centre_parts(piece, body)
        entry["collar"] = fit_collar(piece, body)
        entry["fingers"] = articulate_fingers(piece, body, config["shape_locked"])
        report["pieces"][piece.name] = entry
        fitted[piece.slot_name] = piece
        print(f"[fit] {piece.name}: scale {entry['placement']['scale']:.3f} "
              f"parts {[(part['name'], round(part['rotation_deg'], 1)) for part in entry['parts']]} "
              f"proportion {[(item['name'], item.get('scale_across_axis', item.get('mean_offset_m'))) for item in entry['proportion']]}",
              flush=True)

    # a piece that is the mirror image of its reference-side twin is not fitted twice: it is the mirror of the fit
    for piece in pieces:
        twin = next((p for p in pieces if p.slot_name == twin_name(piece.slot_name, config)), None)
        if twin is None or len(twin.source) != len(piece.source):
            continue
        reflected = twin.source * body.mirror_signs
        reflected[:, k] += piece.source[:, k].mean() - reflected[:, k].mean()
        if not np.allclose(reflected, piece.source, atol=config["mirror_tolerance_m"]):
            continue                                         # two different meshes: each keeps its own fit
        piece.positions = body.mirrored(twin.positions)
        if getattr(twin, "finger_skin", None):
            piece.finger_skin = [(members, np.asarray([body.twin_bone(b) for b in bones]), weights)
                                 for members, bones, weights in twin.finger_skin]
            piece.finger_roots = twin.finger_roots
        report["pieces"][piece.name]["mirrored_from"] = twin.name
        print(f"[fit] {piece.name}: mirror of the fit of {twin.name}", flush=True)

    tree = armour_tree(pieces)
    masked, covered, report["mask"] = body_mask(body, pieces, tree, config)
    visible = np.flatnonzero(~masked)
    poking = poke_through(body.positions, body.normals, body.vertex_area, visible, tree, config["poke_depth_m"])
    # skin with armour right under it is hidden too: what shows there is the armour, cut along the intersection
    masked = masked | poking
    report["mask"].update(by_poke_through=int(poking.sum()), vertices=int(masked.sum()),
                          area_m2=float(body.vertex_area[masked].sum()),
                          body_area_fraction=float(body.vertex_area[masked].sum() / body.vertex_area.sum()))
    covered = covered | poking
    # the set is delivered as separate assets: a piece, or the pieces a slot groups with ``"asset"`` (the two
    # gauntlets, the two boots). Each asset hides the skin it covers when it is worn alone, by the same rules
    assets, alone = {}, {}
    for piece in pieces:
        assets.setdefault(piece.slot.get("asset", piece.name), []).append(piece)
    for asset, members in assets.items():
        own_tree = armour_tree(members)
        own, _, _ = body_mask(body, members, own_tree, config)
        own |= poke_through(body.positions, body.normals, body.vertex_area, np.flatnonzero(~own), own_tree, config["poke_depth_m"])
        alone[asset] = own
    for piece in pieces:
        report["pieces"][piece.name]["fit"] = measure(piece, body, covered, config)
    by_bone = {}
    for vertex in np.flatnonzero(poking):
        name = body.bone_names[body.dominant[vertex]]
        by_bone[name] = by_bone.get(name, 0.0) + float(body.vertex_area[vertex] * 1e4)
    report["poke"] = {"note": "skin that came through the armour at rest and was hidden by the mask",
                      "vertices": int(poking.sum()), "area_cm2": float(body.vertex_area[poking].sum() * 1e4),
                      "body_area_pct": float(body.vertex_area[poking].sum() / body.vertex_area.sum() * 100),
                      "area_cm2_by_bone": dict(sorted(by_bone.items(), key=lambda item: -item[1]))}

    # write the fitted geometry back, skin it and add the body
    armature = build_armature(body)
    skins = {}
    for piece in pieces:
        obj = piece.obj
        obj.matrix_world = Matrix.Identity(4)
        obj.data.vertices.foreach_set("co", piece.positions.ravel())
        obj.data.update()
        ids, weights = core.skin_weights(piece.positions, piece.triangles, body.positions, body.dense,
                                         body.bones(piece.slot["skin_bones"]), **config["skin"])
        if getattr(piece, "finger_skin", None):
            # phalanges take their finger bones; around the roots of the fingers the weights are evened out
            # over the mesh, so that the knuckles bend instead of tearing
            dense = np.zeros((len(piece.positions), len(body.bone_names)))
            np.add.at(dense, (np.repeat(np.arange(len(ids)), ids.shape[1]), ids.ravel()), weights.ravel())
            for members, bones, finger_weights in piece.finger_skin:
                dense[members] = 0.0
                dense[np.ix_(members, bones)] = finger_weights
            edges = core.unique_edges(piece.triangles)
            degree = np.bincount(edges.ravel(), minlength=len(dense)).astype(np.float64)[:, None]
            near = piece.finger_roots[:, None]
            for _ in range(piece.slot["fingers"].get("root_smooth_iterations", 12)):
                total = np.zeros_like(dense)
                np.add.at(total, edges[:, 0], dense[edges[:, 1]])
                np.add.at(total, edges[:, 1], dense[edges[:, 0]])
                dense = np.where(near, 0.5 * dense + 0.5 * total / np.maximum(degree, 1.0), dense)
            ids = np.argsort(-dense, axis=1)[:, :config["skin"]["max_influences"]]
            weights = np.take_along_axis(dense, ids, axis=1)
            weights = weights / np.maximum(weights.sum(axis=1, keepdims=True), 1e-12)
        skins[piece.name] = (ids, weights)
        bind(obj, body.bone_names, ids, weights, armature)
        obj.color = ARMOUR_COLOR
        report["pieces"][piece.name]["skin"] = {
            "bones": sorted({body.bone_names[int(b)] for b in np.unique(ids[weights > 0])}),
            "weight_sum_error": float(np.abs(weights.sum(axis=1) - 1).max())}
    body_obj = build_body_object(body, masked)
    body_obj.color = BODY_COLOR
    colours = body_obj.data.color_attributes.new("Poke", "FLOAT_COLOR", "POINT")
    colours.data.foreach_set("color", np.where(poking[:, None], (1.0, 0.05, 0.05, 1.0), BODY_COLOR).astype(np.float32).ravel())

    report["poses"], worst_geometry = None, None
    if "poses" in inputs:
        near = np.asarray([tree.find_nearest(Vector(p))[3] or np.inf for p in body.positions]) <= 0.15
        candidates = np.flatnonzero(~masked & near)
        face_ends = np.cumsum([len(piece.triangles) for piece in pieces])      # armour_tree stacks the pieces in order
        rows = []
        for clip, fraction, matrices in pose_matrices(inputs["poses"], body, config["pose_fractions"]):
            if config["pose_clips"] and clip not in config["pose_clips"]:
                continue
            posed_body = core.skin(body.positions, body.bone_ids, body.weights, matrices)
            posed = {p.name: core.skin(p.positions, *skins[p.name], matrices) for p in pieces}
            normals = core.vertex_normals(posed_body, body.triangles)
            posed_tree = armour_tree(pieces, posed)
            poke = poke_through(posed_body, normals, body.vertex_area, candidates, posed_tree, config["poke_depth_m"])
            under_armour = float(body.vertex_area[poke].sum() * 1e4)
            # skin with armour under it only shows if no other plate lies over it
            ids = np.flatnonzero(poke)
            poke[ids[~exposed(posed_body, normals, ids, posed_tree, config["mask"])]] = False
            by_pose_bone, by_pose_piece = {}, {}
            for vertex in np.flatnonzero(poke):
                name = body.bone_names[body.dominant[vertex]]
                by_pose_bone[name] = by_pose_bone.get(name, 0.0) + float(body.vertex_area[vertex] * 1e4)
                face = posed_tree.ray_cast(Vector(posed_body[vertex] - 1e-4 * normals[vertex]), Vector(-normals[vertex]),
                                           config["poke_depth_m"])[2]
                k = int(np.searchsorted(face_ends, face, side="right"))
                corner = pieces[k].triangles[face - (face_ends[k - 1] if k else 0)][0]
                ids, weights = skins[pieces[k].name]
                # the piece under the skin and the bone that moves it there: a torso plate or the tip of a sleeve
                under = f"{pieces[k].name}:{body.bone_names[int(ids[corner][np.argmax(weights[corner])])]}"
                by_pose_piece[under] = by_pose_piece.get(under, 0.0) + float(body.vertex_area[vertex] * 1e4)
            rows.append({"clip": clip, "fraction": fraction, "poke_area_cm2": float(body.vertex_area[poke].sum() * 1e4),
                         "poke_area_incl_covered_cm2": under_armour,
                         "area_cm2_by_bone": dict(sorted(by_pose_bone.items(), key=lambda item: -item[1])),
                         "area_cm2_by_piece": dict(sorted(by_pose_piece.items(), key=lambda item: -item[1]))})
            if rows[-1]["poke_area_cm2"] >= max(row["poke_area_cm2"] for row in rows):
                worst_geometry = (posed_body, posed, poke)
        worst = max(rows, key=lambda row: row["poke_area_cm2"]) if rows else None
        report["poses"] = {"tested": len(rows), "worst": worst, "rows": rows,
                           "mean_poke_area_cm2": float(np.mean([row["poke_area_cm2"] for row in rows])) if rows else None}

    setup_render(scene, config["render_resolution"])
    low, high = body.positions.min(axis=0), body.positions.max(axis=0)
    masked_views = render_views(scene, low, high, out / "renders", "fit")
    body_obj.modifiers["BodyMask"].show_render = False
    unmasked_views = render_views(scene, low, high, out / "renders", "fit_unmasked")
    body_obj.modifiers["BodyMask"].show_render = True
    # same views with poke-through painted red on the body
    scene.display.shading.color_type = "VERTEX"
    for piece in pieces:
        attribute = piece.obj.data.color_attributes.new("Poke", "FLOAT_COLOR", "POINT")
        attribute.data.foreach_set("color", np.tile(np.asarray(ARMOUR_COLOR, dtype=np.float32), len(piece.positions)))
    body_obj.modifiers["BodyMask"].show_render = False
    poke_views = render_views(scene, low, high, out / "renders", "poke")
    body_obj.modifiers["BodyMask"].show_render = True
    save_image(np.concatenate(poke_views, axis=1), out / "poke_sheet.png")
    if worst_geometry is not None:
        # the worst tested pose, with its poke-through in red (geometry is restored afterwards)
        posed_body, posed, poke = worst_geometry
        body_obj.data.vertices.foreach_set("co", posed_body.ravel())
        colours.data.foreach_set("color", np.where(poke[:, None], (1.0, 0.05, 0.05, 1.0), BODY_COLOR).astype(np.float32).ravel())
        for piece in pieces:
            piece.obj.data.vertices.foreach_set("co", posed[piece.name].ravel())
            piece.obj.data.update()
            piece.obj.modifiers["Armature"].show_render = False
        body_obj.data.update()
        pose_views = render_views(scene, posed_body.min(axis=0), posed_body.max(axis=0), out / "renders", "worst_pose")
        save_image(np.concatenate(pose_views, axis=1), out / "worst_pose_sheet.png")
        body_obj.data.vertices.foreach_set("co", body.positions.ravel())
        body_obj.data.update()
        for piece in pieces:
            piece.obj.data.vertices.foreach_set("co", piece.positions.ravel())
            piece.obj.data.update()
            piece.obj.modifiers["Armature"].show_render = True
    scene.display.shading.color_type = "OBJECT"
    body_obj.data.color_attributes.remove(colours)
    for piece in pieces:
        piece.obj.data.color_attributes.remove(piece.obj.data.color_attributes["Poke"])
    save_image(np.concatenate([np.concatenate(unmasked_views, axis=1), np.concatenate(masked_views, axis=1)], axis=0),
               out / "fit_sheet.png")

    gates = config["gates"]
    hidden_limit = config["styles"][config["style"]]["max_poke_masked_body_pct"]
    # an area, not a share: once the mask is on, little exposed body is left near a piece to take a share of
    outer_total = sum(entry["fit"]["outer"].get("inside_body_area_cm2", 0.0) for entry in report["pieces"].values())
    report["gates"] = {
        "outer_surface_clear_of_body": {"value": outer_total, "limit": gates["max_exterior_penetration_area_cm2"],
                                        "pass": outer_total <= gates["max_exterior_penetration_area_cm2"]},
        "skin_hidden_for_coming_through": {"value": report["poke"]["body_area_pct"], "limit": hidden_limit,
                                           "pass": report["poke"]["body_area_pct"] <= hidden_limit},
        "no_parameter_at_limit": {"value": [name for name, entry in report["pieces"].items()
                                            if any(entry["seat"]["at_limit"].values())], "limit": [],
                                  "pass": not any(any(entry["seat"]["at_limit"].values())
                                                  for entry in report["pieces"].values())},
    }
    if report["poses"] and report["poses"]["worst"]:
        value = report["poses"]["worst"]["poke_area_cm2"]
        report["gates"]["pose_poke_through"] = {"value": value, "limit": gates["max_pose_poke_area_cm2"],
                                                "pass": value <= gates["max_pose_poke_area_cm2"]}

    np.savez_compressed(out / "body_mask.npz", masked_vertices=np.flatnonzero(masked),
                        masked_faces=np.flatnonzero(masked[body.triangles].all(axis=1)))
    (out / "placements.json").write_text(json.dumps(
        {piece.name: {"slot": piece.slot_name, "matrix_world": (piece.seat.matrix() @ piece.placement.matrix()).tolist()}
         for piece in pieces},
        indent=2), encoding="utf-8")
    blend = out / "armour_fitted.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    report["exports"] = {"blend": {"file": blend.name, "sha256": sha256(blend)},
                         "glb": export_glb(out / "armour_fitted.glb", [armature] + [piece.obj for piece in pieces])}
    # one GLB per asset, with the skeleton, and the body vertices it hides when worn alone. Worn together,
    # assets also hide skin that only shows between them: the whole set has a mask of its own
    (out / "pieces").mkdir()
    together = masked & ~np.any(list(alone.values()), axis=0)
    manifest = {"name": job["name"], "body_mesh": body.profile["mesh"], "body_vertices": len(body.positions),
                "body_triangles": len(body.triangles), "frame": "glTF, +Y up; the body faces +Z",
                "set_mask": {"file": "set.mask.json", "only_when_all_worn_vertices": int(together.sum())}, "assets": {}}
    (out / "pieces" / "set.mask.json").write_text(json.dumps({"masked_vertices": np.flatnonzero(masked).tolist()}), encoding="utf-8")
    for asset, members in assets.items():
        entry = export_glb(out / "pieces" / f"{asset}.glb", [armature] + [piece.obj for piece in members])
        (out / "pieces" / f"{asset}.mask.json").write_text(
            json.dumps({"masked_vertices": np.flatnonzero(alone[asset]).tolist()}), encoding="utf-8")
        manifest["assets"][asset] = {"pieces": [piece.name for piece in members], "slots": [piece.slot_name for piece in members],
                                     "glb": entry["file"], "sha256": entry["sha256"], "mask": f"{asset}.mask.json",
                                     "triangles": int(sum(len(piece.triangles) for piece in members)),
                                     "masked_body_vertices": int(alone[asset].sum())}
    (out / "pieces" / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report["exports"]["pieces"] = manifest
    # the GLB must come back with the same pieces, triangle counts, skeleton and skin
    expected = {piece.name: len(piece.triangles) for piece in pieces}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(out / "armour_fitted.glb"))
    found = {}
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            obj.data.calc_loop_triangles()
            found[obj.name] = {"triangles": len(obj.data.loop_triangles), "vertex_groups": len(obj.vertex_groups),
                               "armature": any(m.type == "ARMATURE" for m in obj.modifiers)}
    skeletons = [len(obj.data.bones) for obj in bpy.data.objects if obj.type == "ARMATURE"]
    # the importer adds its own bone-shape mesh; only the armour pieces are compared
    intact = (set(expected) <= set(found) and skeletons == [len(body.rig_bones)]
              and all(found[name]["triangles"] == count and found[name]["armature"] and found[name]["vertex_groups"] > 0
                      for name, count in expected.items()))
    report["exports"]["glb_reimport"] = {"meshes": found, "skeleton_bones": skeletons}
    separate = {}
    for asset, entry in manifest["assets"].items():
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(out / "pieces" / entry["glb"]))
        meshes = {obj.name: obj for obj in bpy.data.objects if obj.type == "MESH" and obj.name in expected}
        for obj in meshes.values():
            obj.data.calc_loop_triangles()
        separate[asset] = bool(sorted(meshes) == sorted(entry["pieces"])
                               and all(len(obj.data.loop_triangles) == expected[name] and len(obj.vertex_groups) > 0
                                       for name, obj in meshes.items())
                               and [len(obj.data.bones) for obj in bpy.data.objects if obj.type == "ARMATURE"] == [len(body.rig_bones)])
    report["exports"]["pieces_reimport"] = separate
    intact = bool(intact and all(separate.values()))
    report["gates"]["glb_reimports_intact"] = {"value": intact, "limit": True, "pass": bool(intact)}
    if {name: sha256(path) for name, path in inputs.items()} != hashes:
        raise ValueError("An input file changed during the run")
    report["inputs_unchanged"] = True
    report["status"] = "PASS" if all(gate["pass"] for gate in report["gates"].values()) else "FAIL"
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[fit] {report['status']}", flush=True)
    return 0 if report["status"] == "PASS" else 1
