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
    if rigid:
        centres = np.tile(np.median(centres[has_body], axis=0), (count, 1))
    else:
        centres = _smooth_rows(_fill_nearest(centres, has_body), smooth_passes)

    def radii(points_h, px, py, section, reducer) -> np.ndarray:
        dx, dy = px - centres[section, 0], py - centres[section, 1]
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
    piece_radius = radii(piece_h, piece_rel @ u, piece_rel @ v, section_of(piece_h),
                         lambda r: r[int(0.1 * (len(r) - 1))] if len(r) >= 10 else r[0])

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
            if offsets:
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
