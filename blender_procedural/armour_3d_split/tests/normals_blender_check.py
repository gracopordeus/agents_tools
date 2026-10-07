"""Check diagnostic-only handling of zero and nonfinite imported normals, and folded-fan passthrough."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mesh_split import fan_shapes, normal_diagnostics, world_normals

SQUARE = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]


def mesh(name, faces, vertices=SQUARE):
    data = bpy.data.meshes.new(name)
    data.from_pydata(vertices, [], faces)
    data.update()
    return data


def source(vectors, data):
    """Real topology with injected corner vectors: Blender itself never evaluates these values."""
    return SimpleNamespace(name="NormalFixture", matrix_world=Matrix.Identity(4),
                           data=SimpleNamespace(corner_normals=[SimpleNamespace(vector=Vector(value)) for value in vectors],
                                                loops=data.loops, vertices=data.vertices, polygons=data.polygons))


flat = mesh("Flat", [(0, 1, 2), (0, 2, 3)])
invalid = source([(0, 0, 0), (float("nan"), 0, 1)] + [(0, 0, 1)] * 4, flat)
before = [tuple(item.vector) for item in invalid.data.corner_normals]
diagnostic = normal_diagnostics(invalid)
assert not diagnostic["valid"] and not diagnostic["splittable"]
assert (diagnostic["zero_length_count"], diagnostic["nonfinite_count"]) == (1, 1)
assert diagnostic["first_invalid_loop_ids"] == diagnostic["first_unexplained_loop_ids"] == [0, 1]
assert diagnostic["folded_fans"]["corner_count"] == 0 and not diagnostic["folded_fan_corner_mask"].any()
for allowed in (None, diagnostic["folded_fan_corner_mask"]):
    try:
        world_normals(invalid, allowed)
    except ValueError as error:
        assert "count=2" in str(error) and "No automatic recalculation" in str(error)
    else:
        raise AssertionError("Invalid imported normals must stop the split")
after = [tuple(item.vector) for item in invalid.data.corner_normals]
assert np.array_equal(np.asarray(before), np.asarray(after), equal_nan=True)
valid = source([(0, 0, 1)] * 5 + [(1, 0, 0)], flat)
assert normal_diagnostics(valid)["valid"] and normal_diagnostics(valid)["splittable"]
assert np.array_equal(world_normals(valid)[-2:], np.asarray([(0, 0, 1), (1, 0, 0)]))

# Two exactly reversed copies of one triangle joined through coincident vertices 0 and 3: the
# fans of vertices 1 and 2 cancel bit-exactly, which is when Blender evaluates zero normals.
FOLD = [(0, 0, 0), (1, 0, 0), (0.3, 0.9, 0), (0, 0, 0), (2, 0, 0), (2, 1, 0)]
folded_mesh = mesh("Folded", [(0, 1, 2), (1, 3, 2), (1, 4, 5)], FOLD)
for polygon in folded_mesh.polygons:
    polygon.use_smooth = True
folded_object = bpy.data.objects.new("Folded", folded_mesh)
bpy.context.scene.collection.objects.link(folded_object)
bpy.context.view_layer.update()
polygons = [tuple(polygon.vertices) for polygon in folded_mesh.polygons]
shapes = fan_shapes(np.asarray(FOLD, dtype=float), polygons, {2, 5})
assert shapes[2]["folded"] and shapes[2]["residual"] < 1e-9 and not shapes[5]["folded"], shapes
# A needle tip also has a short mean normal, but its faces are not coplanar: never a pocket.
NEEDLE = np.asarray([(0, 0, 1), (0.01, 0, 0), (-0.005, 0.00866, 0), (-0.005, -0.00866, 0)], dtype=float)
needle = fan_shapes(NEEDLE, [(0, 1, 2), (0, 2, 3), (0, 3, 1)], {0})[0]
assert needle["residual"] < 0.05 and not needle["folded"], needle
evaluated = np.asarray([normal.vector[:] for normal in folded_mesh.corner_normals])
corner_vertex = np.asarray([loop.vertex_index for loop in folded_mesh.loops])
is_fold = corner_vertex == 2
assert not evaluated[is_fold].any(), "Blender is expected to evaluate zero normals on a folded fan"
diagnostic = normal_diagnostics(folded_object)
assert not diagnostic["valid"] and diagnostic["splittable"] and diagnostic["unexplained_invalid_count"] == 0
assert diagnostic["folded_fans"]["vertices"] == [2] and diagnostic["folded_fans"]["faces"] == [0, 1]
assert diagnostic["folded_fans"]["corner_count"] == 2 == int(diagnostic["folded_fan_corner_mask"].sum())
try:
    world_normals(folded_object)
except ValueError:
    pass
else:
    raise AssertionError("Zero normals stay rejected unless explicitly classified")
kept = world_normals(folded_object, diagnostic["folded_fan_corner_mask"])
assert not kept[is_fold].any() and np.allclose(np.linalg.norm(kept[~is_fold], axis=1), 1.0)
# A zero vector on a fan that is not folded is never accepted, even next to accepted ones.
mixed = source([(0, 0, 0)] * 9, folded_mesh)
diagnostic = normal_diagnostics(mixed)
assert not diagnostic["splittable"] and diagnostic["folded_fans"]["vertices"] == [2]
assert diagnostic["unexplained_invalid_count"] == 7
print("PASS: zero/nonfinite normal diagnostics, rejection, folded-fan passthrough, and no mutation")
