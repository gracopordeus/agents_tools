"""Signed distance to a closed surface (Blender BVH + angle-weighted pseudo-normals)."""
from __future__ import annotations

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from core import weld_ids


class SurfaceSDF:
    """Positive outside, negative inside. ``triangles`` must have outward normals.

    The sign uses the pseudo-normal of the closest feature (face, edge or vertex; Baerentzen & Aanaes):
    the plain face normal gives the wrong sign next to sharp edges such as nose, lips and ears.
    """

    BARY_EPS = 1e-4

    def __init__(self, positions: np.ndarray, triangles: np.ndarray):
        self.positions, self.triangles = positions, triangles
        self.last_faces = np.zeros(0, dtype=np.int64)
        self.tree = BVHTree.FromPolygons(positions.tolist(), triangles.tolist(), all_triangles=True)
        a, b, c = (positions[triangles[:, i]] for i in range(3))
        cross = np.cross(b - a, c - a)
        self._face_normal = cross / np.maximum(np.linalg.norm(cross, axis=1), 1e-30)[:, None]
        welded = weld_ids(positions)[triangles]
        vertex = np.zeros((int(welded.max()) + 1, 3))
        for i, (p, q, r) in enumerate(((a, b, c), (b, c, a), (c, a, b))):
            u, v = q - p, r - p
            cosine = np.einsum("ij,ij->i", u, v) / np.maximum(np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1), 1e-30)
            np.add.at(vertex, welded[:, i], np.arccos(np.clip(cosine, -1.0, 1.0))[:, None] * self._face_normal)
        edges = np.sort(np.stack([welded[:, [0, 1]], welded[:, [1, 2]], welded[:, [2, 0]]], axis=1), axis=2).reshape(-1, 2)
        unique, inverse = np.unique(edges, axis=0, return_inverse=True)
        edge = np.zeros((len(unique), 3))
        np.add.at(edge, inverse.ravel(), np.repeat(self._face_normal, 3, axis=0))
        normalise = lambda v: v / np.maximum(np.linalg.norm(v, axis=1), 1e-30)[:, None]
        self._vertex_normal, self._edge_normal = normalise(vertex), normalise(edge)
        self._welded, self._face_edge = welded, inverse.reshape(-1, 3)

    def _feature_normals(self, hits: np.ndarray, faces: np.ndarray) -> np.ndarray:
        triangle = self.triangles[faces]
        a, b, c = (self.positions[triangle[:, i]] for i in range(3))
        v0, v1, v2 = b - a, c - a, hits - a
        d00, d01, d11 = (v0 * v0).sum(1), (v0 * v1).sum(1), (v1 * v1).sum(1)
        d20, d21 = (v2 * v0).sum(1), (v2 * v1).sum(1)
        denominator = np.maximum(d00 * d11 - d01 * d01, 1e-30)
        v = (d11 * d20 - d01 * d21) / denominator
        w = (d00 * d21 - d01 * d20) / denominator
        bary = np.stack([1.0 - v - w, v, w], axis=1)
        zero = bary < self.BARY_EPS
        count = zero.sum(axis=1)
        normals = self._face_normal[faces].copy()
        on_vertex = count >= 2
        corner = bary.argmax(axis=1)
        normals[on_vertex] = self._vertex_normal[self._welded[faces[on_vertex], corner[on_vertex]]]
        on_edge = count == 1
        opposite = np.array([1, 2, 0])[zero[on_edge].argmax(axis=1)]   # null u, v, w -> edge (1,2), (2,0), (0,1)
        normals[on_edge] = self._edge_normal[self._face_edge[faces[on_edge], opposite]]
        return normals

    def __call__(self, points: np.ndarray) -> np.ndarray:
        count = len(points)
        hits, faces = np.empty((count, 3)), np.empty(count, dtype=np.int64)
        find = self.tree.find_nearest
        for i in range(count):
            location, _, index, _ = find(Vector(points[i]))
            hits[i], faces[i] = location, index
        self.last_faces = faces
        offset = points - hits
        distance = np.linalg.norm(offset, axis=1)
        return np.where(np.einsum("ij,ij->i", offset, self._feature_normals(hits, faces)) >= 0.0, distance, -distance)
