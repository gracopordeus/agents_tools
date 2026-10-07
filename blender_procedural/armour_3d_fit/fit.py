"""Blender pipeline: place, refine, mask the body, skin, test poses, render and export."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

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
    "proportion": {"step_m": 0.03, "sectors": 24, "harmonics": 2, "expectile": 0.9},
    # how a piece takes the proportions of the body:
    #   plate   - rigid: one scale in depth and one in width per piece, sized by its tightest sections;
    #             silhouettes stay straight and the piece stands off the body
    #   leather - flexible: every cross-section follows the girth of the body under it
    "style": "plate",
    # a straight plate does not follow the body, so more skin ends up against it and is hidden by the mask:
    # the limit on hidden skin is per style
    "styles": {"plate": {"rigid": True, "quantile": 0.85, "taper": 0.2, "extra_gap_m": 0.004,
                         "max_poke_masked_body_pct": 8.0},
               "leather": {"rigid": False, "quantile": 0.9, "taper": 0.0, "extra_gap_m": 0.0,
                           "max_poke_masked_body_pct": 5.0}},
    "exterior_rays": 12, "exterior_escape_fraction": 0.5,
    "mask": {"max_cover_distance_m": 0.12, "rays": 16, "occluded_fraction": 0.85, "erode_rings": 1},
    "poke_depth_m": 0.03,              # armour found this close under visible skin counts as poke-through
    "skin": {"neighbours": 6, "smooth_iterations": 8, "max_influences": 4},
    "pose_fractions": [0.0, 0.25, 0.5, 0.75],
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


class Piece:
    def __init__(self, obj, slot_name: str, slot: dict):
        self.obj, self.name, self.slot_name, self.slot = obj, obj.name, slot_name, slot
        mesh = obj.data
        self.source = np.asarray([obj.matrix_world @ vertex.co for vertex in mesh.vertices], dtype=np.float64)
        mesh.calc_loop_triangles()
        self.triangles = np.asarray([tuple(t.vertices) for t in mesh.loop_triangles], dtype=np.int64)
        self.areas, self.face_normals = core.triangle_areas_normals(self.source, self.triangles)
        self.low, self.high = self.source.min(axis=0), self.source.max(axis=0)
        self.placement = None
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
            ids = np.flatnonzero(mask & (self.areas > 0))
            if len(ids) <= count:
                return ids
            return rng.choice(ids, count, replace=False, p=self.areas[ids] / self.areas[ids].sum())
        self.sample_interior, self.sample_exterior = pick(~self.exterior, interior), pick(self.exterior, exterior)


# --------------------------------------------------------------------------- placement

def bbox_of(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return points.min(axis=0), points.max(axis=0)


def glove_frame(piece: Piece) -> tuple[np.ndarray, str]:
    """Hand frame of a glove piece and the hand it was modelled for (from thumb side and declared back)."""
    rule = piece.slot["place"]
    axis = core.unit(rule["piece_axis"])
    centre, size = (piece.low + piece.high) / 2, piece.high - piece.low
    along = (piece.source - centre) @ axis / float(np.abs(axis @ size)) + 0.5
    frame = core.hand_frame(axis, piece.source[along > rule["piece_fraction"]], None)
    # right hand: back of the hand = long axis x thumb side
    right = frame[:, 2] @ np.asarray(rule["palm"]["piece_back"], dtype=float) > 0
    return frame, "right" if right else "left"


def landmark_placement(piece: Piece, body: Body, roll: float = 0.0) -> core.Placement:
    """First estimate of rotation, uniform scale and position from the slot's landmark rule."""
    rule = piece.slot["place"]
    centre = (piece.low + piece.high) / 2
    size = piece.high - piece.low
    if rule["type"] == "bbox":
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
        return core.Placement(centre, np.eye(3), scale, target - centre)
    if rule["type"] == "axis":
        start, end = (body.head(role) for role in rule["bones"])
        direction = core.unit(end - start)
        piece_axis = core.unit(rule["piece_axis"])
        if "palm" in rule:
            # back of the glove on the back of the hand, fingers along the hand
            hand = body.positions[np.isin(body.dominant, body.bones(rule["palm"]["hand"]))]
            thumb = body.positions[np.isin(body.dominant, body.bones(rule["palm"]["thumb"]))]
            body_frame = core.hand_frame(direction, hand, thumb.mean(axis=0) - hand.mean(axis=0))
            body_back = body_frame[:, 2] * (1.0 if rule["palm"]["handedness"] == "right" else -1.0)
            piece_frame, _ = glove_frame(piece)
            piece_back = piece_frame[:, 2] * np.sign(piece_frame[:, 2] @ np.asarray(rule["palm"]["piece_back"], dtype=float))
            target = np.stack([direction, body_back, np.cross(direction, body_back)], axis=1)
            source = np.stack([piece_axis, piece_back, np.cross(piece_axis, piece_back)], axis=1)
            rotation0 = core.rotation(direction * roll) @ target @ source.T
        else:
            rotation0 = core.rotation(direction * roll) @ core.rotation_between(piece_axis, direction)
        length = float(np.abs(piece_axis @ size))
        scale = float(np.linalg.norm(end - start)) * rule["size"]["ratio"] / length
        # the point of the centre line at ``piece_fraction`` of the length, measured from the start of the axis
        local = (rule["piece_fraction"] - 0.5) * length * piece_axis
        anchor = body.head(rule["at_bone"])
        return core.Placement(centre, rotation0, scale, anchor - scale * rotation0 @ local - centre)
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
    limits = core.Limits(**config["limits"])
    low, high = piece.positions.min(axis=0), piece.positions.max(axis=0)
    placement = core.Placement((low + high) / 2, np.eye(3), 1.0, np.zeros(3))
    residual = make_residual(piece, body, placement, config, piece.positions[piece.triangles].mean(axis=1))
    coarse = np.array([1, 1, 1, 0, 0, 0, 0, 0, 0, 0], dtype=bool)
    full = np.ones(10, dtype=bool) if config["refine_scale"] else np.array([1, 1, 1, 1, 1, 1, 0, 0, 0, 0], dtype=bool)
    parameters, first = core.gauss_newton(residual, np.zeros(10), limits, iterations=config["iterations"], active=coarse)
    parameters, second = core.gauss_newton(residual, parameters, limits, iterations=config["iterations"], active=full)
    placement.parameters = parameters
    piece.positions, piece.seat = placement.apply(piece.positions), placement
    at_limit = {"translation": bool(np.linalg.norm(parameters[:3]) >= limits.translation_m * 0.999),
                "rotation": bool(np.linalg.norm(parameters[3:6]) >= math.radians(limits.rotation_deg) * 0.999),
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


def bend_parts(piece: Piece, body: Body, config: dict) -> list[dict]:
    """Rotate declared sub-parts (e.g. sleeves) about a joint so that the limb passes through them.

    A vertex belongs to the part as far as the body next to it follows the part's bones (smoothed over the
    mesh, so there is no tear). Sleeves are assumed to be modelled hanging down: the search starts from
    fractions of the rotation that takes "down" to the limb direction.
    """
    out = []
    piece.part_weights = {}
    for part in piece.slot.get("parts", []):
        pivot = body.head(part["pivot_bone"])
        base = piece.positions.copy()
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
        piece.part_weights[part["name"]] = weight
        face_weight = weight[piece.triangles].mean(axis=1)
        interior = np.intersect1d(piece.sample_interior, np.flatnonzero(face_weight > 0.5))
        exterior = np.intersect1d(piece.sample_exterior, np.flatnonzero(face_weight > 0.5))
        radius = float(np.sqrt(((base[weight > 0.5] - pivot) ** 2).sum(axis=1).mean())) if (weight > 0.5).any() else 0.1

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
            return np.concatenate([fit, np.sqrt(config["regularisation"] * 0.1) * radius * parameters[3:7]])
        limits = core.Limits(rotation_deg=part["max_rotation_deg"], scale=part.get("max_scale", 0.0))
        active = np.array([0, 0, 0, 1, 1, 1, 1, 0, 0, 0], dtype=bool)
        # a sleeve hanging beside a raised limb is a local minimum: optimise from several swings towards the limb
        full = core.rotation_between([0.0, 0.0, -1.0], body.head(part["toward_bone"]) - pivot)
        angle = np.arccos(np.clip((np.trace(full) - 1) / 2, -1.0, 1.0))
        axis = np.array([full[2, 1] - full[1, 2], full[0, 2] - full[2, 0], full[1, 0] - full[0, 1]])
        swing = axis / max(np.linalg.norm(axis), 1e-12) * min(angle, np.radians(part["max_rotation_deg"]) * 0.95)
        runs = [core.gauss_newton(residual, np.concatenate([np.zeros(3), swing * fraction, np.zeros(4)]),
                                  limits, iterations=config["iterations"], active=active)
                for fraction in (0.0, 1 / 3, 2 / 3, 1.0)]
        parameters, log = min(runs, key=lambda run: run[1]["cost_final"])
        log = {**log, "start_costs": [run[1]["cost_final"] for run in runs]}
        piece.positions = bent(base, weight, parameters)
        out.append({"name": part["name"], "pivot_bone": part["pivot_bone"], "vertices": int((weight > 0.5).sum()),
                    "rotation_vector_deg": np.degrees(parameters[3:6]).tolist(),
                    "rotation_deg": float(np.degrees(np.linalg.norm(parameters[3:6]))),
                    "scale": float(np.exp(parameters[6])),
                    "at_limit": bool(np.linalg.norm(parameters[3:6]) >= np.radians(part["max_rotation_deg"]) * 0.999), **log})
    return out


def body_points(body: Body, roles: list[str]) -> np.ndarray:
    """Vertices and face centres of the body owned by the given bones."""
    owned = np.isin(body.dominant, body.bones(roles))
    faces = body.triangles[owned[body.triangles].all(axis=1)]
    return np.concatenate([body.positions[owned], body.positions[faces].mean(axis=1)])


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
    out = []
    for name, rule, weight in jobs:
        if isinstance(rule["axis"], dict):
            start, end = (body.head(role) for role in rule["axis"]["bones"])
            origin, axis = start, end - start
        else:
            origin, axis = np.zeros(3), np.eye(3)["xyz".index(rule["axis"])]
        field, stats = core.section_field(
            piece.positions, body_points(body, rule["body"]), origin, axis, rule["gap_m"] + style["extra_gap_m"],
            step=config["proportion"]["step_m"], sectors=config["proportion"]["sectors"],
            harmonics=config["proportion"]["harmonics"], expectile=config["proportion"]["expectile"],
            max_in=rule["max_in_m"], max_out=rule["max_out_m"], piece_weight=weight,
            rigid=style["rigid"], rigid_quantile=style["quantile"], rigid_taper=style["taper"])
        if field is not None:
            before = piece.positions
            piece.positions = field.apply(before, weight)
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
    for piece in pieces:
        piece.classify(config["exterior_rays"], config["exterior_escape_fraction"])
        piece.choose_samples(config["samples_interior"], config["samples_exterior"])
        entry = {"slot": piece.slot_name, "triangles": len(piece.triangles), "placement": place(piece, body, config)}
        entry["parts"] = bend_parts(piece, body, config)
        entry["proportion"] = proportion(piece, body, config)
        entry["seat"] = seat(piece, body, config)
        report["pieces"][piece.name] = entry
        print(f"[fit] {piece.name}: scale {entry['placement']['scale']:.3f} "
              f"parts {[(part['name'], round(part['rotation_deg'], 1)) for part in entry['parts']]} "
              f"proportion {[(item['name'], item.get('scale_across_axis', item.get('mean_offset_m'))) for item in entry['proportion']]}",
              flush=True)

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
        rows = []
        for clip, fraction, matrices in pose_matrices(inputs["poses"], body, config["pose_fractions"]):
            posed_body = core.skin(body.positions, body.bone_ids, body.weights, matrices)
            posed = {p.name: core.skin(p.positions, *skins[p.name], matrices) for p in pieces}
            normals = core.vertex_normals(posed_body, body.triangles)
            poke = poke_through(posed_body, normals, body.vertex_area, candidates, armour_tree(pieces, posed), config["poke_depth_m"])
            rows.append({"clip": clip, "fraction": fraction, "poke_area_cm2": float(body.vertex_area[poke].sum() * 1e4)})
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
    report["gates"]["glb_reimports_intact"] = {"value": intact, "limit": True, "pass": bool(intact)}
    if {name: sha256(path) for name, path in inputs.items()} != hashes:
        raise ValueError("An input file changed during the run")
    report["inputs_unchanged"] = True
    report["status"] = "PASS" if all(gate["pass"] for gate in report["gates"].values()) else "FAIL"
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[fit] {report['status']}", flush=True)
    return 0 if report["status"] == "PASS" else 1
