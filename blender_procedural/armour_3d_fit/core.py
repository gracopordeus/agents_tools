"""Pure numpy geometry for armour_3d_fit: no bpy, testable with the system Python.

A piece keeps its shape. It is placed on the body by one bounded affine transform

    x' = R(w) R0 diag(s * a) (x - c) + c + t0 + t

where ``R0``, ``s0`` and ``t0`` come from the slot's landmark rule and the optimiser only refines
``t`` (translation), ``w`` (small rotation), ``s`` (uniform scale) and ``a`` (per-axis scale).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

GODOT_TO_BLENDER = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])


def unit(vector) -> np.ndarray:
    vector = np.asarray(vector, dtype=np.float64)
    length = np.linalg.norm(vector)
    if not np.isfinite(length) or length < 1e-12:
        raise ValueError("Undefined direction")
    return vector / length


def godot_matrix(columns) -> np.ndarray:
    """Host transform (three basis vectors + origin, Godot frame) as a 4x4 matrix in the Blender frame."""
    columns = np.asarray(columns, dtype=np.float64)
    out = np.eye(4)
    out[:3, :3] = GODOT_TO_BLENDER @ columns[:3].T @ GODOT_TO_BLENDER.T
    out[:3, 3] = GODOT_TO_BLENDER @ columns[3]
    return out


def rotation(vector) -> np.ndarray:
    """Rodrigues: rotation matrix of a rotation vector."""
    vector = np.asarray(vector, dtype=np.float64)
    angle = np.linalg.norm(vector)
    if angle < 1e-12:
        return np.eye(3)
    x, y, z = vector / angle
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return np.eye(3) + np.sin(angle) * k + (1 - np.cos(angle)) * (k @ k)


def rotation_between(source, target) -> np.ndarray:
    """Smallest rotation taking direction ``source`` to ``target``."""
    a, b = unit(source), unit(target)
    cross = np.cross(a, b)
    sine, cosine = np.linalg.norm(cross), float(np.clip(a @ b, -1.0, 1.0))
    if sine < 1e-10:
        if cosine > 0:
            return np.eye(3)
        return rotation(unit(np.cross(a, np.eye(3)[np.argmin(np.abs(a))])) * np.pi)
    return rotation(cross / sine * np.arctan2(sine, cosine))


def triangle_areas_normals(positions: np.ndarray, triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a, b, c = (positions[triangles[:, i]] for i in range(3))
    cross = np.cross(b - a, c - a)
    length = np.linalg.norm(cross, axis=1)
    return 0.5 * length, cross / np.maximum(length, 1e-30)[:, None]


def signed_volume(positions: np.ndarray, triangles: np.ndarray) -> float:
    a, b, c = (positions[triangles[:, i]] for i in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def vertex_normals(positions: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    a, b, c = (positions[triangles[:, i]] for i in range(3))
    cross = np.cross(b - a, c - a)
    out = np.zeros_like(positions)
    for corner in range(3):
        np.add.at(out, triangles[:, corner], cross)
    return out / np.maximum(np.linalg.norm(out, axis=1), 1e-30)[:, None]


def weld_ids(positions: np.ndarray, decimals: int = 6) -> np.ndarray:
    """Vertex -> id shared by coincident vertices (UV seams duplicate vertices in exported meshes)."""
    _, inverse = np.unique(np.round(positions, decimals), axis=0, return_inverse=True)
    return inverse.ravel()


def hemisphere(count: int, min_cos: float = 0.2) -> np.ndarray:
    """Fibonacci directions on the +Z hemisphere, at most ``acos(min_cos)`` from the pole."""
    i = np.arange(count) + 0.5
    z = min_cos + (1.0 - min_cos) * (1.0 - i / count)
    phi = np.pi * (1.0 + 5.0 ** 0.5) * i
    r = np.sqrt(np.maximum(1.0 - z * z, 0.0))
    return np.stack([r * np.cos(phi), r * np.sin(phi), z], axis=1)


def orient(local: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """(N, D, 3): ``local`` directions (+Z = normal) expressed around each of the ``normals``."""
    helper = np.where(np.abs(normals[:, 2:3]) < 0.9, np.array([[0.0, 0.0, 1.0]]), np.array([[1.0, 0.0, 0.0]]))
    tangent = np.cross(helper, normals)
    tangent /= np.maximum(np.linalg.norm(tangent, axis=1), 1e-12)[:, None]
    bitangent = np.cross(normals, tangent)
    return (local[None, :, 0:1] * tangent[:, None, :] + local[None, :, 1:2] * bitangent[:, None, :]
            + local[None, :, 2:3] * normals[:, None, :])


# --------------------------------------------------------------------------- placement

@dataclass(frozen=True)
class Limits:
    translation_m: float = 0.15
    rotation_deg: float = 15.0
    scale: float = 0.08          # uniform scale may leave the landmark estimate by this fraction
    anisotropy: float = 0.05     # each axis may differ from the uniform scale by this fraction


@dataclass
class Placement:
    """Landmark estimate of one piece, plus the refinement found by the optimiser."""
    centre: np.ndarray           # c: bounding-box centre of the source piece
    rotation0: np.ndarray        # R0
    scale0: float                # s0
    offset0: np.ndarray          # t0
    parameters: np.ndarray = None  # (10,) t, w, log(s / s0), log(a)

    def __post_init__(self):
        if self.parameters is None:
            self.parameters = np.zeros(10)

    def linear(self, parameters=None) -> np.ndarray:
        p = self.parameters if parameters is None else parameters
        return rotation(p[3:6]) @ self.rotation0 @ np.diag(self.scale0 * np.exp(p[6]) * np.exp(p[7:10]))

    def apply(self, points: np.ndarray, parameters=None) -> np.ndarray:
        p = self.parameters if parameters is None else parameters
        return (points - self.centre) @ self.linear(p).T + self.centre + self.offset0 + p[:3]

    def matrix(self) -> np.ndarray:
        out = np.eye(4)
        out[:3, :3] = self.linear()
        out[:3, 3] = self.centre + self.offset0 + self.parameters[:3] - out[:3, :3] @ self.centre
        return out

    def summary(self) -> dict:
        p = self.parameters
        return {"translation_m": p[:3].tolist(), "rotation_deg": float(np.degrees(np.linalg.norm(p[3:6]))),
                "landmark_scale": self.scale0, "scale": float(self.scale0 * np.exp(p[6])),
                "axis_scale": np.exp(p[7:10]).tolist()}


def clamp(parameters: np.ndarray, limits: Limits) -> np.ndarray:
    p = np.asarray(parameters, dtype=np.float64).copy()
    length = np.linalg.norm(p[:3])
    if length > limits.translation_m:
        p[:3] *= limits.translation_m / length
    angle, cap = np.linalg.norm(p[3:6]), np.radians(limits.rotation_deg)
    if angle > cap:
        p[3:6] *= cap / angle
    p[6] = np.clip(p[6], -np.log1p(limits.scale), np.log1p(limits.scale))
    p[7:10] = np.clip(p[7:10], -np.log1p(limits.anisotropy), np.log1p(limits.anisotropy))
    return p


def fit_residual(interior_sd: np.ndarray, interior_w: np.ndarray, exterior_sd: np.ndarray, exterior_w: np.ndarray,
                 clearance: float, target_gap: float, gap_weight: float, attraction_scale: float = 0.03) -> np.ndarray:
    """Residuals in metres. ``*_w`` are area fractions (each set sums to at most 1).

    Cavity surface: must stay ``clearance`` outside the body, and is pulled (robustly) to within ``target_gap``.
    Outer surface: the body must not come through it, with the same clearance.
    """
    low = np.minimum(interior_sd - clearance, 0.0)
    excess = np.maximum(interior_sd - target_gap, 0.0)
    pull = attraction_scale * np.sqrt(np.log1p((excess / attraction_scale) ** 2))   # Cauchy: far plates do not dominate
    return np.concatenate([np.sqrt(interior_w) * low, np.sqrt(gap_weight * interior_w) * pull,
                           np.sqrt(exterior_w) * np.minimum(exterior_sd - clearance, 0.0)])


def regularisation(parameters: np.ndarray, radius: float, weight: float) -> np.ndarray:
    """Cost of leaving the landmark estimate, in metres: translation, rotation x radius, scale x radius."""
    p = parameters
    return np.sqrt(weight) * np.concatenate([p[:3], radius * p[3:6], [radius * p[6]], 2.0 * radius * p[7:10]])


def gauss_newton(residual: Callable[[np.ndarray], np.ndarray], start: np.ndarray, limits: Limits, *,
                 iterations: int = 30, active: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Finite-difference Levenberg-Marquardt inside ``limits``; a step is kept only if the cost drops."""
    steps = np.array([1e-3] * 3 + [2e-3] * 3 + [2e-3] * 4)
    active = np.ones(10, dtype=bool) if active is None else np.asarray(active, dtype=bool)
    p = clamp(start, limits)
    r = residual(p)
    cost = float(r @ r)
    history, damping, stop = [cost], 1e-3, "iterations"
    for _ in range(iterations):
        jacobian = np.zeros((len(r), 10))
        for j in np.flatnonzero(active):
            probe = p.copy()
            probe[j] += steps[j]
            jacobian[:, j] = (residual(probe) - r) / steps[j]
        normal, gradient = jacobian.T @ jacobian, jacobian.T @ r
        accepted = False
        while damping < 1e6:
            delta = np.linalg.lstsq(normal + damping * np.diag(np.maximum(np.diag(normal), 1e-9)), -gradient, rcond=None)[0]
            delta[~active] = 0.0
            candidate = clamp(p + delta, limits)
            trial = residual(candidate)
            trial_cost = float(trial @ trial)
            if np.isfinite(trial_cost) and trial_cost < cost - 1e-14:
                gain = cost - trial_cost
                p, r, cost, accepted = candidate, trial, trial_cost, True
                damping = max(damping * 0.3, 1e-6)
                break
            damping *= 5.0
        history.append(cost)
        if not accepted:
            stop = "no_descent"
            break
        if gain < 1e-9 * max(cost, 1e-12) or gain < 1e-13:
            stop = "converged"
            break
    return p, {"stop": stop, "iterations": len(history) - 1, "cost_initial": history[0], "cost_final": cost}


def hand_frame(axis, points: np.ndarray, thumb_hint) -> np.ndarray:
    """Right-handed frame of a hand or glove, columns = (long axis, thumb side, palm normal).

    The palm normal is the thinnest direction across the long axis. The thumb side comes from ``thumb_hint``
    (a vector towards the thumb) or, without one, from the skew of the points: the thumb is the mass that
    sticks out to one side.
    """
    axis = unit(axis)
    centred = points - points.mean(axis=0)
    across = centred - np.outer(centred @ axis, axis)
    values, vectors = np.linalg.eigh(across.T @ across)
    normal = unit(vectors[:, 1])                      # index 0 is the long axis itself (zero spread)
    lateral = np.cross(normal, axis)
    if thumb_hint is None:
        coordinate = centred @ lateral
        sign = np.sign(((coordinate - np.median(coordinate)) ** 3).sum())
    else:
        sign = np.sign(np.asarray(thumb_hint) @ lateral)
    lateral = lateral * (sign if sign else 1.0)
    return np.stack([axis, lateral, np.cross(axis, lateral)], axis=1)


def cavity_offsets(points: np.ndarray, limb: np.ndarray, along: np.ndarray, low: float, high: float,
                   stations: int = 8, sectors: int = 16, closed: float = 0.75) -> tuple[np.ndarray, np.ndarray]:
    """How far off the middle of a limb the cavity of a tube around it is, station by station.

    At each height the tube is read from the middle of the limb outwards: the nearest point of it in each
    direction is its inner wall there. The middle of that outline is the middle of the cavity; flaps and
    plates that stand out on one side of the tube do not move it. Only stations where the tube closes around
    the limb count. Returns the heights (along ``along``) and the offsets, cavity minus limb (K, 3).
    """
    along = unit(along)
    u = unit(np.cross(along, [1.0, 0.0, 0.0] if abs(along[0]) < 0.9 else [0.0, 1.0, 0.0]))
    v = np.cross(along, u)
    tube_h, limb_h = points @ along, limb @ along
    edges = np.linspace(low, high, stations + 1)
    heights, offsets = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        ring, flesh = points[(tube_h >= a) & (tube_h <= b)], limb[(limb_h >= a) & (limb_h <= b)]
        if len(ring) < sectors or len(flesh) < 6:
            continue
        fu, fv = flesh @ u, flesh @ v
        middle = np.array([(fu.min() + fu.max()) / 2, (fv.min() + fv.max()) / 2])
        du, dv = ring @ u - middle[0], ring @ v - middle[1]
        radius = np.hypot(du, dv)
        sector = np.floor((np.arctan2(dv, du) % (2 * np.pi)) / (2 * np.pi) * sectors).astype(int) % sectors
        inner = np.full(sectors, np.inf)
        np.minimum.at(inner, sector, radius)
        filled = np.isfinite(inner)
        if filled.mean() < closed:
            continue
        # middle of the inner outline: opposite walls in pairs, so that a missing sector does not pull it
        angles = (np.arange(sectors) + 0.5) / sectors * 2 * np.pi
        half = sectors // 2
        pairs = filled[:half] & filled[half:]
        if pairs.sum() < 3:
            continue
        across = (inner[:half] - inner[half:])[pairs] / 2
        design = np.stack([np.cos(angles[:half][pairs]), np.sin(angles[:half][pairs])], axis=1)
        centre = np.linalg.lstsq(design, across, rcond=None)[0]
        heights.append((a + b) / 2)
        offsets.append(centre[0] * u + centre[1] * v)
    return np.asarray(heights), np.asarray(offsets).reshape(-1, 3)


def long_axis(points: np.ndarray, hint) -> np.ndarray:
    """Long axis of an elongated piece: its first principal direction, pointing the way of ``hint``."""
    centred = points - points.mean(axis=0)
    axis = np.linalg.eigh(centred.T @ centred)[1][:, -1]
    return axis * (np.sign(axis @ np.asarray(hint, dtype=np.float64)) or 1.0)


def centre_line(points: np.ndarray, axis: np.ndarray, low: float, high: float, stations: int = 8,
                min_points: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """Line through the middles of the cross-sections of a tube between two heights along ``axis``.

    Returns a point of the line and its direction (pointing the way of ``axis``). The middle of a section is
    the middle of its extent, not its mean: vertices are never spread evenly around a plate. With fewer than
    two usable sections the line is ``axis`` through the mean of the points.
    """
    axis = unit(axis)
    u = unit(np.cross(axis, [1.0, 0.0, 0.0] if abs(axis[0]) < 0.9 else [0.0, 1.0, 0.0]))
    v = np.cross(axis, u)
    height = points @ axis
    edges = np.linspace(low, high, stations + 1)
    centres = []
    for a, b in zip(edges[:-1], edges[1:]):
        members = points[(height >= a) & (height <= b)]
        if len(members) < min_points:
            continue
        cu, cv = members @ u, members @ v
        centres.append((a + b) / 2 * axis + (cu.min() + cu.max()) / 2 * u + (cv.min() + cv.max()) / 2 * v)
    if len(centres) < 2:
        return points.mean(axis=0), axis
    centres = np.asarray(centres)
    middle = centres.mean(axis=0)
    direction = np.linalg.svd(centres - middle)[2][0]
    return middle, direction * (np.sign(direction @ axis) or 1.0)


# --------------------------------------------------------------------------- body regions and landmarks

def dominant_bone(bone_ids: np.ndarray, weights: np.ndarray) -> np.ndarray:
    return bone_ids[np.arange(len(bone_ids)), weights.argmax(axis=1)]


def region_bounds(positions: np.ndarray, dominant: np.ndarray, bones: list[int]) -> tuple[np.ndarray, np.ndarray]:
    selected = positions[np.isin(dominant, bones)]
    if not len(selected):
        raise ValueError("Body region has no vertices")
    return selected.min(axis=0), selected.max(axis=0)


def dense_weights(bone_ids: np.ndarray, weights: np.ndarray, bone_count: int) -> np.ndarray:
    dense = np.zeros((len(bone_ids), bone_count))
    np.add.at(dense, (np.repeat(np.arange(len(bone_ids)), bone_ids.shape[1]), bone_ids.ravel()), weights.ravel())
    return dense / np.maximum(dense.sum(axis=1, keepdims=True), 1e-12)


# --------------------------------------------------------------------------- skin

def nearest_k(points: np.ndarray, cloud: np.ndarray, k: int, chunk: int = 512) -> tuple[np.ndarray, np.ndarray]:
    k = min(k, len(cloud))
    index, distance = np.empty((len(points), k), dtype=np.int64), np.empty((len(points), k))
    cloud_sq = np.einsum("ij,ij->i", cloud, cloud)
    for start in range(0, len(points), chunk):
        block = points[start:start + chunk]
        sq = np.einsum("ij,ij->i", block, block)[:, None] - 2.0 * block @ cloud.T + cloud_sq[None, :]
        part = np.argpartition(sq, k - 1, axis=1)[:, :k]
        rows = np.arange(len(block))[:, None]
        order = np.argsort(sq[rows, part], axis=1)
        part = part[rows, order]
        index[start:start + chunk] = part
        distance[start:start + chunk] = np.sqrt(np.maximum(sq[rows, part], 0.0))
    return index, distance


def unique_edges(triangles: np.ndarray) -> np.ndarray:
    edges = np.sort(np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]]), axis=1)
    return np.unique(edges, axis=0)


def skin_weights(positions: np.ndarray, triangles: np.ndarray, body_positions: np.ndarray, body_dense: np.ndarray,
                 allowed: list[int], *, neighbours: int = 6, smooth_iterations: int = 8,
                 max_influences: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Armour weights inherited from the nearest body vertices, restricted to the slot's bones.

    Returns (ids (N, K), weights (N, K)); every row sums to 1.
    """
    index, distance = nearest_k(positions, body_positions, neighbours)
    kernel = 1.0 / np.maximum(distance, 1e-4) ** 2
    dense = np.einsum("nk,nkb->nb", kernel, body_dense[index])
    mask = np.zeros(body_dense.shape[1], dtype=bool)
    mask[allowed] = True
    dense[:, ~mask] = 0.0
    empty = dense.sum(axis=1) <= 1e-12
    dense[empty, allowed[0]] = 1.0                   # nothing inherited: follow the slot's first bone
    dense /= dense.sum(axis=1, keepdims=True)
    edges = unique_edges(triangles)
    degree = np.bincount(edges.ravel(), minlength=len(positions)).astype(np.float64)
    for _ in range(smooth_iterations):
        total = np.zeros_like(dense)
        np.add.at(total, edges[:, 0], dense[edges[:, 1]])
        np.add.at(total, edges[:, 1], dense[edges[:, 0]])
        dense = 0.5 * dense + 0.5 * total / np.maximum(degree, 1.0)[:, None]
    ids = np.argsort(-dense, axis=1)[:, :max_influences]
    kept = np.take_along_axis(dense, ids, axis=1)
    return ids, kept / np.maximum(kept.sum(axis=1, keepdims=True), 1e-12)


def skin(positions: np.ndarray, ids: np.ndarray, weights: np.ndarray, matrices: np.ndarray) -> np.ndarray:
    """Linear blend skinning: ``matrices`` (B, 4, 4) take the rest pose to the pose."""
    homogeneous = np.concatenate([positions, np.ones((len(positions), 1))], axis=1)
    out = np.zeros_like(positions)
    for k in range(ids.shape[1]):
        out += weights[:, k:k + 1] * np.einsum("nij,nj->ni", matrices[ids[:, k]], homogeneous)[:, :3]
    return out


# --------------------------------------------------------------------------- fingers

def finger_tubes(height: np.ndarray, edges: np.ndarray, min_size: int, floor: float) -> list[tuple[np.ndarray, float]]:
    """Vertex sets that stand apart as tubes at the far end of a hand: (members, height where the tube joins).

    Vertices enter from the highest down and are joined along mesh edges. A tube is a group that is still on
    its own when it meets another group of at least ``min_size`` vertices. On a gauntlet whose fingers are
    fused side by side only the fingertips come out; the caller has to check that the tubes are whole fingers.
    """
    count = len(height)
    neighbours = [[] for _ in range(count)]
    for a, b in edges:
        neighbours[a].append(int(b))
        neighbours[b].append(int(a))
    parent, members = list(range(count)), {}
    pure, active, tubes = {}, np.zeros(count, dtype=bool), []

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for vertex in np.argsort(-height):
        if height[vertex] < floor:
            break
        vertex = int(vertex)
        active[vertex], members[vertex], pure[vertex] = True, [vertex], True
        for other in neighbours[vertex]:
            if not active[other]:
                continue
            a, b = find(vertex), find(other)
            if a == b:
                continue
            if len(members[a]) < len(members[b]):
                a, b = b, a
            if len(members[b]) >= min_size:
                for group in (a, b):
                    if pure[group]:
                        tubes.append((np.asarray(members[group], dtype=np.int64), float(height[vertex])))
                pure[a] = False
            parent[b] = a
            members[a] += members.pop(b)
    return tubes


def mesh_distance(positions: np.ndarray, edges: np.ndarray, sources: np.ndarray, reach: float) -> np.ndarray:
    """Distance along mesh edges from ``sources`` to every vertex, followed up to ``reach`` (inf beyond)."""
    import heapq
    neighbours = [[] for _ in positions]
    lengths = np.linalg.norm(positions[edges[:, 0]] - positions[edges[:, 1]], axis=1)
    for (a, b), length in zip(edges, lengths):
        neighbours[a].append((int(b), float(length)))
        neighbours[b].append((int(a), float(length)))
    distance = np.full(len(positions), np.inf)
    distance[sources] = 0.0
    heap = [(0.0, int(v)) for v in sources]
    heapq.heapify(heap)
    while heap:
        d, v = heapq.heappop(heap)
        if d > distance[v] or d > reach:
            continue
        for other, length in neighbours[v]:
            if d + length < distance[other]:
                distance[other] = d + length
                heapq.heappush(heap, (d + length, other))
    return distance


def along_polyline(line: np.ndarray, arc: np.ndarray) -> np.ndarray:
    """Points at arc lengths ``arc`` of a polyline, continued straight before its first and past its last point."""
    lengths = np.linalg.norm(np.diff(line, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(lengths)])
    segment = np.clip(np.searchsorted(cumulative, arc, side="right") - 1, 0, len(lengths) - 1)
    t = (arc - cumulative[segment]) / np.maximum(lengths[segment], 1e-12)
    return line[segment] + t[:, None] * (line[segment + 1] - line[segment])


def _across(normal, tangent: np.ndarray) -> np.ndarray:
    """``normal`` made perpendicular to ``tangent``; any perpendicular when the two are parallel."""
    normal = np.asarray(normal, dtype=np.float64)
    out = normal - (normal @ tangent) * tangent
    if np.linalg.norm(out) < 1e-6 * max(np.linalg.norm(normal), 1e-12):
        out = np.cross(tangent, np.eye(3)[np.argmin(np.abs(tangent))])
    return unit(out)


def lay_along_chain(points: np.ndarray, base, tip, chain: np.ndarray, normal, start_arc: float, *,
                    scale: float = 1.0, samples: int = 12) -> tuple[np.ndarray, np.ndarray]:
    """Lay a straight tube (a gauntlet finger, from ``base`` to ``tip``) along a bone chain.

    The axis of the tube goes onto ``chain`` from ``start_arc`` to its end; every cross-section is carried
    over rigidly, turned with the chain by frames transported without twist from ``normal`` (the back of the
    hand), and widened by ``scale``. Returns the moved points and their arc position on the chain.
    """
    base, tip = np.asarray(base, dtype=np.float64), np.asarray(tip, dtype=np.float64)
    axis, length = unit(tip - base), float(np.linalg.norm(tip - base))
    t = (points - base) @ axis
    u = t / max(length, 1e-9)
    chain_length = float(np.linalg.norm(np.diff(chain, axis=0), axis=1).sum())
    arc = start_arc + u * (chain_length - start_arc)
    own_normal = _across(normal, axis)
    own = np.stack([axis, own_normal, np.cross(axis, own_normal)], axis=1)
    line = along_polyline(chain, start_arc + np.linspace(0.0, 1.0, samples + 1) * (chain_length - start_arc))
    tangents = np.gradient(line, axis=0)
    tangents /= np.maximum(np.linalg.norm(tangents, axis=1, keepdims=True), 1e-12)
    current = _across(normal, tangents[0])
    turns = []
    for k, tangent in enumerate(tangents):
        if k:
            current = _across(rotation_between(tangents[k - 1], tangent) @ current, tangent)
        turns.append(np.stack([tangent, current, np.cross(tangent, current)], axis=1) @ own.T)
    turns = np.stack(turns)
    position = np.clip(u, 0.0, 1.0) * samples
    first = np.clip(np.floor(position).astype(int), 0, samples - 1)
    fraction = (position - first)[:, None]
    offset = points - (base + t[:, None] * axis)
    carried = (1 - fraction) * np.einsum("nij,nj->ni", turns[first], offset) \
        + fraction * np.einsum("nij,nj->ni", turns[first + 1], offset)
    return along_polyline(chain, arc) + scale * carried, arc


def chain_weights(arc: np.ndarray, joints: list[float], blend: float) -> np.ndarray:
    """Weights (N, len(joints) + 1) of a point at ``arc`` along a chain: bone k starts at ``joints[k - 1]``."""
    ramps = [np.clip((arc - joint) / (2 * blend) + 0.5, 0.0, 1.0) for joint in joints]
    ramps = [ramps[0]] + [np.minimum(ramps[k], ramps[k - 1]) for k in range(1, len(ramps))]
    columns = [1.0 - ramps[0]] + [ramps[k - 1] - ramps[k] for k in range(1, len(ramps))] + [ramps[-1]]
    return np.stack(columns, axis=1)


# --------------------------------------------------------------------------- body mask

def erode(masked: np.ndarray, positions: np.ndarray, triangles: np.ndarray, rings: int) -> np.ndarray:
    """Remove ``rings`` vertex rings from the border of the mask, so the cut stays hidden under the armour."""
    if rings <= 0 or not masked.any():
        return masked.copy()
    welded = weld_ids(positions)
    state = np.zeros(int(welded.max()) + 1, dtype=bool)
    state[welded[masked]] = True
    edges = unique_edges(welded[triangles])
    for _ in range(rings):
        border = np.zeros_like(state)
        border[edges[~state[edges[:, 1]], 0]] = True
        border[edges[~state[edges[:, 0]], 1]] = True
        state &= ~border
    return state[welded] & masked


def dilate(masked: np.ndarray, positions: np.ndarray, triangles: np.ndarray, rings: int) -> np.ndarray:
    if rings <= 0:
        return masked.copy()
    welded = weld_ids(positions)
    state = np.zeros(int(welded.max()) + 1, dtype=bool)
    state[welded[masked]] = True
    edges = unique_edges(welded[triangles])
    for _ in range(rings):
        grown = state.copy()
        grown[edges[state[edges[:, 1]], 0]] = True
        grown[edges[state[edges[:, 0]], 1]] = True
        state = grown
    return state[welded]


def vertex_areas(positions: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    areas, _ = triangle_areas_normals(positions, triangles)
    out = np.zeros(len(positions))
    for corner in range(3):
        np.add.at(out, triangles[:, corner], areas / 3.0)
    return out


# --------------------------------------------------------------------------- proportion (cross-sections)

@dataclass
class SectionField:
    """Radial offset of a piece around a limb or torso axis, by cross-section.

    The body and the piece are read as stacks of cross-sections along ``axis`` (a centre and a radius per
    angular sector, as a tailor measures girths). ``coefficients`` hold, per section, a low-order Fourier
    series of the offset that brings the innermost surface of the piece to ``gap`` from the body. Being
    smooth along and around the axis, it changes the proportions of the piece and keeps its plates and relief.
    """
    origin: np.ndarray
    axis: np.ndarray
    u: np.ndarray
    v: np.ndarray
    start: float                 # axial coordinate of the centre of section 0
    step: float
    centres: np.ndarray          # (S, 2) body centre of each section, in (u, v)
    coefficients: np.ndarray     # (S, 1 + 2H)
    max_in: float
    max_out: float
    # rigid mode: scale across the axis, linear along it, instead of the offsets: rows (u, v), columns (at h = 0, per metre)
    scale: np.ndarray | None = None
    scale_limits: tuple[float, float] = (0.0, np.inf)

    def scale_at(self, h: np.ndarray) -> np.ndarray:
        """(N, 2) scale in u and v at axial coordinate ``h``."""
        return np.clip(self.scale[:, 0][None, :] + np.outer(h, self.scale[:, 1]), *self.scale_limits)

    def coordinates(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        relative = points - self.origin
        return relative @ self.axis, relative @ self.u, relative @ self.v

    def _sample(self, table: np.ndarray, h: np.ndarray) -> np.ndarray:
        t = np.clip((h - self.start) / self.step, 0.0, len(table) - 1.0)
        low = np.minimum(np.floor(t).astype(int), len(table) - 1)
        high = np.minimum(low + 1, len(table) - 1)
        fraction = (t - low)[:, None]
        return table[low] * (1 - fraction) + table[high] * fraction

    def offset(self, h: np.ndarray, theta: np.ndarray) -> np.ndarray:
        c = self._sample(self.coefficients, h)
        harmonics = (c.shape[1] - 1) // 2
        value = c[:, 0].copy()
        for k in range(1, harmonics + 1):
            value += c[:, 2 * k - 1] * np.cos(k * theta) + c[:, 2 * k] * np.sin(k * theta)
        return np.clip(value, -self.max_in, self.max_out)

    def apply(self, points: np.ndarray, weight: np.ndarray | None = None) -> np.ndarray:
        h, x, y = self.coordinates(points)
        centre = self._sample(self.centres, h)
        dx, dy = x - centre[:, 0], y - centre[:, 1]
        if self.scale is not None:
            # a scale that only tapers linearly along the axis: straight plates stay straight
            w = np.ones(len(points)) if weight is None else weight
            factor = 1 + w[:, None] * (self.scale_at(h) - 1)
            return (self.origin + np.outer(h, self.axis) + np.outer(centre[:, 0] + dx * factor[:, 0], self.u)
                    + np.outer(centre[:, 1] + dy * factor[:, 1], self.v))
        radius = np.hypot(dx, dy)
        shift = self.offset(h, np.arctan2(dy, dx))
        if weight is not None:
            shift = shift * weight
        scale = np.maximum(radius + shift, 0.25 * radius) / np.maximum(radius, 1e-9)
        return (self.origin + np.outer(h, self.axis) + np.outer(centre[:, 0] + dx * scale, self.u)
                + np.outer(centre[:, 1] + dy * scale, self.v))


def _fill_nearest(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Rows without data take the nearest row that has it."""
    known = np.flatnonzero(valid)
    nearest = known[np.abs(np.arange(len(values))[:, None] - known[None, :]).argmin(axis=1)]
    return values[nearest]


def _smooth_rows(values: np.ndarray, passes: int) -> np.ndarray:
    out = values.astype(np.float64).copy()
    for _ in range(passes):
        padded = np.concatenate([out[:1], out, out[-1:]])
        out = 0.25 * padded[:-2] + 0.5 * padded[1:-1] + 0.25 * padded[2:]
    return out


def section_field(piece_points: np.ndarray, body_points: np.ndarray, origin, axis, gap: float, *,
                  step: float = 0.03, sectors: int = 24, harmonics: int = 2, expectile: float = 0.9,
                  max_in: float = 0.10, max_out: float = 0.10, smooth_passes: int = 2,
                  piece_weight: np.ndarray | None = None, rigid: bool = False,
                  rigid_quantile: float = 0.9, rigid_taper: float = 0.2) -> tuple[SectionField | None, dict]:
    """Measure body and piece section by section and solve the change that proportions the piece to the body.

    Flexible (leather, cloth): per section, the low-order offset closest to "innermost piece radius = body
    radius + gap", fitted as an upper expectile: the tight spots decide, loose plates do not pull a section in.

    Rigid (plate): a scale across the axis, in depth and in width, that may only taper linearly along the
    axis, about a straight centre line. The piece takes the size its tight sections need and every straight
    line of it stays straight.
    """
    axis = unit(axis)
    u = unit(np.cross(axis, np.eye(3)[np.argmin(np.abs(axis))]))
    v = np.cross(axis, u)
    origin = np.asarray(origin, dtype=np.float64)
    used = piece_points if piece_weight is None else piece_points[piece_weight > 0.5]
    if len(used) < 16 or len(body_points) < 16:
        return None, {"sections": 0, "reason": "too few points"}
    piece_h = (used - origin) @ axis
    start = float(piece_h.min()) + step / 2
    count = max(int(np.ceil((piece_h.max() - piece_h.min()) / step)), 1)
    section_of = lambda h: np.clip(np.floor((h - piece_h.min()) / step).astype(int), 0, count - 1)

    body_rel = body_points - origin
    body_h = body_rel @ axis
    inside = (body_h >= piece_h.min()) & (body_h <= piece_h.max())
    body_section, bx, by = section_of(body_h[inside]), body_rel[inside] @ u, body_rel[inside] @ v
    centres, has_body = np.zeros((count, 2)), np.zeros(count, dtype=bool)
    for s in range(count):
        members = body_section == s
        if members.sum() >= 12:
            centres[s] = [(bx[members].min() + bx[members].max()) / 2, (by[members].min() + by[members].max()) / 2]
            has_body[s] = True
    if not has_body.any():
        return None, {"sections": count, "reason": "the body does not reach the piece"}
    centres = _smooth_rows(_fill_nearest(centres, has_body), smooth_passes)
    body_centres = centres

    def radii(points_h, px, py, section, reducer, about=None) -> np.ndarray:
        about = centres if about is None else about
        dx, dy = px - about[section, 0], py - about[section, 1]
        sector = np.floor((np.arctan2(dy, dx) % (2 * np.pi)) / (2 * np.pi) * sectors).astype(int) % sectors
        out = np.full((count, sectors), np.nan)
        order = np.lexsort((np.hypot(dx, dy), sector, section))
        keys = section[order] * sectors + sector[order]
        sorted_radius = np.hypot(dx, dy)[order]
        boundaries = np.flatnonzero(np.diff(keys)) + 1
        for chunk, key in zip(np.split(sorted_radius, boundaries), keys[np.concatenate([[0], boundaries])]):
            out[key // sectors, key % sectors] = reducer(chunk)
        return out
    body_radius = radii(body_h[inside], bx, by, body_section, lambda r: r[int(0.9 * (len(r) - 1))])
    piece_rel = used - origin
    inner = lambda r: r[int(0.1 * (len(r) - 1))] if len(r) >= 10 else r[0]
    piece_section, px, py = section_of(piece_h), piece_rel @ u, piece_rel @ v
    piece_radius = radii(piece_h, px, py, piece_section, inner)
    own_offsets = []
    if rigid:
        # a ring that closes around the limb is measured about its own middle: the chord through a point
        # near its wall (a leaning shin inside an upright boot) is much shorter than its width
        own = body_centres.copy()
        closed = np.isfinite(piece_radius).mean(axis=1) >= 0.9
        for k in np.flatnonzero(closed & has_body):
            members = piece_section == k
            own[k] = [(px[members].min() + px[members].max()) / 2, (py[members].min() + py[members].max()) / 2]
        piece_radius = radii(piece_h, px, py, piece_section, inner, own)
        own_offsets = (own - body_centres)[closed & has_body]
        # the piece is scaled about one straight line
        centres = np.tile(np.median(body_centres[has_body], axis=0), (count, 1))

    theta = (np.arange(sectors) + 0.5) / sectors * 2 * np.pi
    design = np.stack([np.ones(sectors)] + [f(k * theta) for k in range(1, harmonics + 1) for f in (np.cos, np.sin)], axis=1)
    coefficients, solved = np.zeros((count, design.shape[1])), np.zeros(count, dtype=bool)
    wanted = body_radius + gap - piece_radius
    girth = {"min": float(2 * np.pi * np.nanmean(body_radius[has_body], axis=1).min()),
             "max": float(2 * np.pi * np.nanmean(body_radius[has_body], axis=1).max())}
    if rigid:
        # widths, not radii: a width does not depend on where the centre was taken
        typical = float(np.nanmedian(piece_radius[has_body]))
        if not np.isfinite(typical):
            return None, {"sections": count, "reason": "piece and body share no section"}
        low, high = 1.0 - max_in / typical, 1.0 + max_out / typical
        scale, shift, used_sections = np.tile([1.0, 0.0], (2, 1)), np.zeros(2), 0
        heights = start + step * np.arange(count)
        for i, direction in enumerate((0.0, np.pi / 2)):
            ratios, offsets, at = [], [], []
            for s in np.flatnonzero(has_body):
                body_side, piece_side = [], []
                for side in (direction, direction + np.pi):
                    along = np.cos(theta - side)
                    wide, narrow = along >= np.cos(np.pi / 4), along >= np.cos(np.radians(20))
                    body_side.append(np.nanmax(np.where(wide, body_radius[s] * along, np.nan))
                                     if np.isfinite(body_radius[s][wide]).any() else np.nan)
                    piece_side.append(np.nanmin(piece_radius[s][narrow])
                                      if np.isfinite(piece_radius[s][narrow]).any() else np.nan)
                both = np.isfinite(body_side) & np.isfinite(piece_side)
                if both.all():
                    ratios.append((sum(body_side) + 2 * gap) / sum(piece_side))
                    if not closed[s]:
                        offsets.append((piece_side[0] - piece_side[1]) / 2)
                    at.append(heights[s])
                elif both.any():                              # open on one side (a pauldron): that side decides
                    k = int(np.argmax(both))
                    ratios.append((body_side[k] + gap) / piece_side[k])
                    at.append(heights[s])
            if ratios:
                # upper expectile line through (height, needed scale): the tight sections decide
                a = np.stack([np.ones(len(at)), np.asarray(at)], axis=1) if len(ratios) >= 4 else np.ones((len(at), 1))
                target = np.clip(np.asarray(ratios), low, high)
                weights, line = np.ones(len(target)), np.zeros(a.shape[1])
                for _ in range(25):
                    line = np.linalg.lstsq(a * np.sqrt(weights)[:, None], target * np.sqrt(weights), rcond=None)[0]
                    weights = np.where(target > a @ line, rigid_quantile, 1.0 - rigid_quantile)
                if len(line) == 2:
                    # bound the taper: the scale may change by at most ``rigid_taper`` from end to end
                    middle, length = (heights[0] + heights[-1]) / 2, max(heights[-1] - heights[0], 1e-9)
                    mean = line[0] + line[1] * middle
                    line[1] = np.clip(line[1], -rigid_taper / length, rigid_taper / length)
                    line[0] = mean - line[1] * middle
                scale[i, :len(line)] = line
                used_sections = max(used_sections, len(ratios))
            if len(own_offsets):
                # middle of the piece's own rings, relative to the line it is scaled about
                shift[i] = float(np.median(own_offsets[:, i] + (body_centres - centres)[closed & has_body][:, i]))
            elif offsets:
                shift[i] = np.median(offsets)
        if not used_sections:
            return None, {"sections": count, "reason": "piece and body share no section"}
        centres = centres + shift                             # scale about the middle of the piece's own cavity
        valid = np.isfinite(piece_radius) & has_body[:, None]
        field = SectionField(origin, axis, u, v, start, step, centres, coefficients, max_in, max_out,
                             scale=scale, scale_limits=(low, high))
        ends = field.scale_at(np.array([heights[0], heights[-1]]))
        return field, {"mode": "rigid", "sections": count, "sections_solved": int(valid.any(axis=1).sum()),
                       "scale_across_axis": {"at_start": ends[0].tolist(), "at_end": ends[1].tolist()},
                       "scale_limits": [low, high], "body_girth_m": girth}
    for s in range(count):
        valid = np.isfinite(wanted[s])
        if not has_body[s] or valid.sum() < 3:
            continue
        columns = design.shape[1] if valid.sum() >= design.shape[1] + 2 else 1
        a, target = design[valid][:, :columns], wanted[s, valid]
        ridge = 1e-3 * np.eye(columns)
        ridge[0, 0] = 0.0
        weights, c = np.ones(len(target)), np.zeros(columns)
        for _ in range(25):                               # asymmetric least squares = expectile regression
            c = np.linalg.solve(a.T @ (a * weights[:, None]) + ridge, a.T @ (weights * target))
            weights = np.where(target > a @ c, expectile, 1.0 - expectile)
        coefficients[s, :columns], solved[s] = c, True
    if not solved.any():
        return None, {"sections": count, "reason": "piece and body share no section"}
    coefficients = _smooth_rows(_fill_nearest(coefficients, solved), smooth_passes)
    field = SectionField(origin, axis, u, v, start, step, centres, coefficients, max_in, max_out)
    mean = np.clip(coefficients[solved, 0], -max_in, max_out)
    return field, {"mode": "flexible", "sections": count, "sections_solved": int(solved.sum()),
                   "mean_offset_m": {"min": float(mean.min()), "median": float(np.median(mean)), "max": float(mean.max())},
                   "body_girth_m": girth}


def chain_fraction(length: float, first: float, second: float) -> float:
    """A length along a limb as a coordinate on its two bones: 0.5 is half the first bone, 1 the joint
    between them, 1.5 half the second bone."""
    return length / first if length <= first else 1.0 + (length - first) / second


def chain_length(fraction: float, first: float, second: float) -> float:
    """Inverse of ``chain_fraction``."""
    return fraction * first if fraction <= 1.0 else first + (fraction - 1.0) * second


def design_reach(own: float, other: float | None, snap: float) -> tuple[float, str]:
    """Where the rim of a piece is to land on a limb, read from where the asset drew it.

    ``own`` is the rim as drawn, in chain coordinates counted from the far end of this piece; ``other`` is
    the rim of the piece it meets on the same limb, counted from *its* far end towards the same joint
    (``None`` when the two do not touch in the asset). Both read 1 on the joint between them.

    Drawn within ``snap`` of the joint, a rim belongs on the joint: a plate does not stop a little short of
    an elbow or run a little past it. Two pieces that meet keep meeting: at the joint when either was drawn
    near it, otherwise at the point where they were drawn to meet (a sleeve to the middle of the forearm and
    a short gauntlet), shared out so that nothing is left bare and nothing overlaps.
    """
    near = lambda value: abs(value - 1.0) <= snap
    if other is not None:
        if near(own) or near(other):
            return 1.0, "meets its neighbour: on the joint"
        if (own > 1.0) != (other > 1.0):                     # one runs past the joint, the other stops short of it
            past, short = max(own, other) - 1.0, min(own, other)
            total = past + short
            return (1.0 + past / total, "meets its neighbour past the joint") if own > 1.0 else (short / total, "meets its neighbour short of the joint")
        return own, "meets its neighbour, both on the same side of the joint: as drawn"
    if near(own):
        return 1.0, "drawn near the joint: on the joint"
    return own, "as drawn"
