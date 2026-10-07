"""Unit tests of the numpy core (system Python, no Blender)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core


def sphere_points(count: int, radius: float, seed: int = 0) -> np.ndarray:
    points = np.random.default_rng(seed).normal(size=(count, 3))
    return points / np.linalg.norm(points, axis=1, keepdims=True) * radius


class Transforms(unittest.TestCase):
    def test_rotation_between_takes_source_to_target(self):
        for source, target in (([0, 0, -1], [0.19, 0, -0.21]), ([1, 0, 0], [-1, 0, 0]), ([0, 1, 0], [0, 1, 0])):
            matrix = core.rotation_between(source, target)
            np.testing.assert_allclose(matrix @ core.unit(source), core.unit(target), atol=1e-12)
            np.testing.assert_allclose(matrix @ matrix.T, np.eye(3), atol=1e-12)
            self.assertAlmostEqual(np.linalg.det(matrix), 1.0)

    def test_placement_matrix_matches_apply(self):
        placement = core.Placement(np.array([0.1, 0.2, 0.3]), core.rotation([0.1, -0.2, 0.3]), 1.4, np.array([0.5, 0.0, -0.2]),
                                   np.array([0.01, 0.02, -0.03, 0.05, 0.0, -0.04, 0.1, 0.05, -0.05, 0.0]))
        points = sphere_points(20, 0.3)
        matrix = placement.matrix()
        np.testing.assert_allclose(points @ matrix[:3, :3].T + matrix[:3, 3], placement.apply(points), atol=1e-12)

    def test_clamp_enforces_every_limit(self):
        limits = core.Limits(translation_m=0.1, rotation_deg=10, scale=0.2, anisotropy=0.1)
        p = core.clamp(np.array([1, 0, 0, 0, 2, 0, 3, 1, -1, 0.01]), limits)
        self.assertAlmostEqual(np.linalg.norm(p[:3]), 0.1)
        self.assertAlmostEqual(np.degrees(np.linalg.norm(p[3:6])), 10)
        self.assertAlmostEqual(np.exp(p[6]), 1.2)
        np.testing.assert_allclose(np.exp(p[7:10]), [1.1, 1 / 1.1, np.exp(0.01)])


class Fit(unittest.TestCase):
    def test_residual_signs(self):
        r = core.fit_residual(np.array([-0.01, 0.01, 0.10]), np.full(3, 1 / 3), np.array([-0.02, 0.05]), np.full(2, 0.5),
                              clearance=0.003, target_gap=0.02, gap_weight=1.0)
        penetration, pull, outer = r[:3], r[3:6], r[6:]
        self.assertLess(penetration[0], 0)
        self.assertEqual(penetration[1], 0)
        self.assertEqual(pull[1], 0)
        self.assertGreater(pull[2], 0)
        self.assertLess(outer[0], 0)
        self.assertEqual(outer[1], 0)

    def test_optimiser_recentres_and_resizes_a_shell_around_a_ball(self):
        """Body = ball of radius 0.10 at the origin; piece = shell of radius 0.09 placed 3 cm off-centre."""
        cavity = sphere_points(300, 0.09)
        placement = core.Placement(np.zeros(3), np.eye(3), 1.0, np.array([0.03, -0.01, 0.0]))
        ball = lambda points: np.linalg.norm(points, axis=1) - 0.10
        weights = np.full(len(cavity), 1 / len(cavity))

        def residual(p):
            return np.concatenate([core.fit_residual(ball(placement.apply(cavity, p)), weights, np.zeros(0), np.zeros(0),
                                                     0.003, 0.01, 0.3), core.regularisation(p, 0.09, 1e-4)])
        p, log = core.gauss_newton(residual, np.zeros(10), core.Limits())
        sd = ball(placement.apply(cavity, p))
        self.assertLess(log["cost_final"], log["cost_initial"] * 0.05)
        self.assertGreater(sd.min(), 0.0)                     # the ball no longer comes through
        self.assertLess(np.linalg.norm(placement.offset0 + p[:3]), 0.006)


class Hands(unittest.TestCase):
    def glove(self, thumb_side: float) -> np.ndarray:
        rng = np.random.default_rng(1)
        palm = rng.uniform([-0.04, -0.01, 0.0], [0.04, 0.01, 0.18], size=(600, 3))      # long z, wide x, thin y
        thumb = rng.uniform([0.04, -0.01, 0.02], [0.09, 0.01, 0.06], size=(80, 3)) * [thumb_side, 1, 1]
        return np.concatenate([palm, thumb])

    def test_frame_finds_palm_normal_and_thumb_side(self):
        frame = core.hand_frame([0, 0, 1], self.glove(+1), None)
        np.testing.assert_allclose(frame[:, 0], [0, 0, 1])
        np.testing.assert_allclose(frame[:, 1], [1, 0, 0], atol=0.05)
        np.testing.assert_allclose(np.abs(frame[:, 2]), [0, 1, 0], atol=0.05)
        self.assertAlmostEqual(np.linalg.det(frame), 1.0, places=6)

    def test_mirrored_glove_has_the_other_handedness(self):
        back = np.array([0.0, -1.0, 0.0])
        sides = [core.hand_frame([0, 0, 1], self.glove(side), None)[:, 2] @ back > 0 for side in (+1, -1)]
        self.assertNotEqual(*sides)

    def test_thumb_hint_overrides_the_skew(self):
        frame = core.hand_frame([0, 0, 1], self.glove(+1), [-1, 0, 0])
        np.testing.assert_allclose(frame[:, 1], [-1, 0, 0], atol=0.05)


class SkinAndMask(unittest.TestCase):
    def setUp(self):
        grid = np.stack(np.meshgrid(np.arange(6.0), np.arange(6.0), indexing="ij"), axis=-1).reshape(-1, 2)
        self.positions = np.concatenate([grid * 0.1, np.zeros((36, 1))], axis=1)
        index = lambda i, j: i * 6 + j
        self.triangles = np.array([t for i in range(5) for j in range(5)
                                   for t in ((index(i, j), index(i + 1, j), index(i + 1, j + 1)),
                                             (index(i, j), index(i + 1, j + 1), index(i, j + 1)))])

    def test_weights_sum_to_one_and_use_only_allowed_bones(self):
        body = np.array([[0.0, 0.0, 0.0], [0.5, 0.5, 0.0], [0.25, 0.25, 0.0]])
        dense = np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]])
        ids, weights = core.skin_weights(self.positions, self.triangles, body, dense, [0, 1], neighbours=3)
        np.testing.assert_allclose(weights.sum(axis=1), 1.0)
        self.assertFalse(np.isin(ids[weights > 0], [2]).any())

    def test_bones_missing_from_the_body_fall_back_to_the_first_allowed(self):
        dense = np.array([[0, 0, 1.0]])
        ids, weights = core.skin_weights(self.positions, self.triangles, np.zeros((1, 3)), dense, [1], neighbours=1)
        self.assertTrue(((ids[:, 0] == 1) & np.isclose(weights[:, 0], 1.0)).all())

    def test_skinning_with_identity_keeps_the_rest_pose(self):
        ids, weights = np.zeros((36, 2), dtype=int), np.full((36, 2), 0.5)
        np.testing.assert_allclose(core.skin(self.positions, ids, weights, np.eye(4)[None]), self.positions)

    def test_erode_removes_exactly_the_border_ring(self):
        masked = np.ones(36, dtype=bool)
        masked[:6] = False                                   # one visible row: its neighbours are the mask border
        eroded = core.erode(masked, self.positions, self.triangles, 1)
        self.assertFalse(eroded[6:12].any())
        self.assertTrue(eroded[12:].all())
        np.testing.assert_array_equal(core.dilate(eroded, self.positions, self.triangles, 1), masked)


class Proportion(unittest.TestCase):
    """Body: elliptical column (20 x 12 cm half-axes), 3 cm off-centre. Piece: round tube with a 2 cm wall."""

    def setUp(self):
        rng = np.random.default_rng(0)
        theta, z = rng.uniform(0, 2 * np.pi, 20000), rng.uniform(0, 0.6, 20000)
        self.body = np.stack([0.20 * np.cos(theta) + 0.03, 0.12 * np.sin(theta), z], axis=1)
        theta, z = rng.uniform(0, 2 * np.pi, 8000), rng.uniform(0.1, 0.5, 8000)
        self.inner = rng.random(8000) < 0.5
        radius = np.where(self.inner, 0.15, 0.17)
        self.piece = np.stack([radius * np.cos(theta), radius * np.sin(theta), z], axis=1)

    def ellipse(self, points):
        return np.sqrt(((points[:, 0] - 0.03) / 0.20) ** 2 + (points[:, 1] / 0.12) ** 2)

    def test_tube_takes_the_girth_of_the_body_and_keeps_its_wall(self):
        field, stats = core.section_field(self.piece, self.body, [0, 0, 0], [0, 0, 1], 0.01)
        self.assertEqual(stats["sections_solved"], stats["sections"])
        before, after = self.ellipse(self.piece[self.inner]), self.ellipse(field.apply(self.piece)[self.inner])
        self.assertLess(before.min(), 0.8)                    # the round tube cut through the wide side of the body
        self.assertGreater(after.min(), 1.0)                  # now it clears it all around...
        self.assertLess(np.median(after), 1.2)                # ...and stays close
        moved = field.apply(self.piece)
        centre = np.array([0.03, 0.0])
        wall = (np.median(np.linalg.norm(moved[~self.inner][:, :2] - centre, axis=1))
                - np.median(np.linalg.norm(moved[self.inner][:, :2] - centre, axis=1)))
        self.assertAlmostEqual(wall, 0.02, delta=0.004)
        np.testing.assert_allclose(moved[:, 2], self.piece[:, 2], atol=1e-12)     # nothing slides along the axis

    def test_limits_and_weights_bound_the_offset(self):
        field, _ = core.section_field(self.piece, self.body, [0, 0, 0], [0, 0, 1], 0.01, max_in=0.0, max_out=0.005)
        shift = np.linalg.norm(field.apply(self.piece) - self.piece, axis=1)
        self.assertLessEqual(shift.max(), 0.005 + 1e-9)
        np.testing.assert_allclose(field.apply(self.piece, np.zeros(len(self.piece))), self.piece, atol=1e-12)

    def test_rigid_mode_sizes_the_tube_and_keeps_it_straight(self):
        """Plate: the body bulges in the middle; the tube must clear it without following the bulge."""
        z = self.body[:, 2]
        bulge = 1 + 0.3 * np.sin(z / 0.6 * np.pi)
        body = np.stack([(self.body[:, 0] - 0.03) * bulge + 0.03, self.body[:, 1] * bulge, z], axis=1)
        tube = self.piece[self.inner]
        field, stats = core.section_field(tube, body, [0, 0, 0], [0, 0, 1], 0.01, rigid=True, rigid_quantile=0.9,
                                          rigid_taper=0.0, max_in=0.1, max_out=0.2)
        self.assertEqual(stats["mode"], "rigid")
        moved = field.apply(tube)
        widths = [np.ptp(moved[(tube[:, 2] > low) & (tube[:, 2] < low + 0.05)][:, :2], axis=0)
                  for low in (0.10, 0.28, 0.45)]
        np.testing.assert_allclose(widths[0], widths[1], atol=0.004)       # same width at every height: straight
        np.testing.assert_allclose(widths[1], widths[2], atol=0.004)
        # sized by the widest part of the body (half-axes 26 and 15.6 cm), not by its average
        self.assertGreater(widths[1][0], 2 * 0.25)
        self.assertGreater(widths[1][1], 2 * 0.15)
        np.testing.assert_allclose(moved[:, 2], tube[:, 2], atol=1e-12)

    def test_rigid_taper_is_bounded(self):
        z = self.body[:, 2]
        cone = 0.6 + z                                                  # body twice as wide at the top as at the bottom
        body = np.stack([(self.body[:, 0] - 0.03) * cone + 0.03, self.body[:, 1] * cone, z], axis=1)
        field, _ = core.section_field(self.piece[self.inner], body, [0, 0, 0], [0, 0, 1], 0.01, rigid=True,
                                      rigid_taper=0.1, max_in=0.2, max_out=0.4)
        ends = field.scale_at(np.array([0.1, 0.5]))
        self.assertLessEqual(np.abs(ends[1] - ends[0]).max(), 0.1 * 0.4 / 0.37 + 1e-6)

    def test_no_field_when_the_body_is_elsewhere(self):
        field, stats = core.section_field(self.piece, self.body + [0, 0, 5.0], [0, 0, 0], [0, 0, 1], 0.01)
        self.assertIsNone(field)
        self.assertIn("reason", stats)


if __name__ == "__main__":
    unittest.main()
