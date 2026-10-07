"""Decimate separated armour parts to triangle budgets and measure what the reduction cost.

Runs inside Blender on the output of ``armour_3d_split``::

    blender -b --factory-startup --python-exit-code 2 -P armour_3d_decimate/decimate.py -- \
        --input split/armour_split.blend --budgets armour_3d_decimate/budgets.plate_quads.json --out NEW_DIR

The input file is never saved. Every part is brought to its budget with Blender's quadric Decimate and
compared with its source: two-way surface distance, silhouette IoU on four orthographic views and
manifoldness. Exit code 1 when a gate fails.

Quads (``"quads": {"enabled": true}`` in the budgets file): a GLB cannot store quads, so a quad mesh
arrives as pairs of triangles. The pairs are joined back first. A collapse destroys most of them, so
``"reduce": "over_budget"`` leaves a part alone when it already fits its budget.

Pairs (``"mirror_pairs"`` in the budgets file): a generated set never has two identical gauntlets or boots.
The better mesh of each pair is kept, the other is replaced by its mirror image across the middle of the
set, before anything else is done; after the reduction the two halves have the same topology, mirrored.

Halves (``"symmetrize"``): a generated torso or helmet is never the same on its two sides (one side of a collar
stands higher, one pauldron sits lower). One half is kept and mirrored onto the other across the middle of the
set, and the two are joined along it.

Lids (``"recess_lids"``): a generator closes every opening of a piece with a lid flush with its edge, so a
boot looks plugged. The lid is pushed down into the piece: a flat floor with a wall around it. The mesh stays
closed and keeps its faces; from outside the piece reads as hollow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from armour_3d_fit import core  # noqa: E402  (numpy only: finger separation of a gauntlet)

VIEWS = {"front": (0.0, -1.0, 0.0), "back": (0.0, 1.0, 0.0), "left": (-1.0, 0.0, 0.0), "right": (1.0, 0.0, 0.0)}
SHEET_VIEWS = {"front": (0.0, -1.0, 0.0), "three_quarter": (-0.65, -0.70, 0.30), "back": (0.0, 1.0, 0.0)}
MASK_RESOLUTION = 896
SHEET_RESOLUTION = 640


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def triangles(mesh) -> int:
    mesh.calc_loop_triangles()
    return len(mesh.loop_triangles)


def world_bounds(obj) -> tuple[np.ndarray, np.ndarray]:
    points = np.asarray([obj.matrix_world @ Vector(corner) for corner in obj.bound_box])
    return points.min(axis=0), points.max(axis=0)


def topology(mesh) -> dict:
    bm = bmesh.new()
    bm.from_mesh(mesh)
    uses = np.asarray([len(edge.link_faces) for edge in bm.edges])
    areas = np.asarray([face.calc_area() for face in bm.faces])
    bm.free()
    sides = np.asarray([polygon.loop_total for polygon in mesh.polygons])
    return {"vertices": len(mesh.vertices), "triangles": triangles(mesh), "faces": len(sides),
            "tri_faces": int((sides == 3).sum()), "quad_faces": int((sides == 4).sum()), "ngon_faces": int((sides > 4).sum()),
            "quad_face_share": float((sides == 4).mean()) if len(sides) else 0.0,
            "boundary_edges": int((uses == 1).sum()), "non_manifold_edges": int((uses > 2).sum()),
            "zero_area_faces": int((areas < 1e-14).sum())}


def world_points(obj) -> np.ndarray:
    points = np.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", points)
    matrix = np.asarray(obj.matrix_world)
    return points.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]


# the set stands facing -Y with the character's right on -X: pairs and halves are mirrored across this axis
# (``"mirror_axis"`` in the budgets file), through the middle of the set
MIRROR_AXIS = "x"
PAIR_DEFAULTS = {"min_finger_vertices": 15, "min_finger_share": 0.01}     # smallest tube that counts as a finger
SYMMETRIZE_DEFAULTS = {"keep": "R",                # side of the character that stays
                       "weld_m": 1e-6, "degenerate_m": 1e-7,
                       "attempts": 6, "margin_triangles": 40}     # how the reduction aims lower until the joined piece fits


def pair_quality(obj, rule: dict) -> dict:
    """What tells a good generated mesh from a bad one, measured on one half of a pair.

    Defects first: edges that are open or shared by more than two faces, faces without area, loose fragments
    and faces that cut through other faces of the same mesh. On a gauntlet (``"fingers": n``) also how many
    fingers stand apart in the mesh and for what length: fused fingers cannot be articulated.
    """
    mesh = obj.data
    counts = topology(mesh)
    points = world_points(obj)
    mesh.calc_loop_triangles()
    faces = np.asarray([tuple(t.vertices) for t in mesh.loop_triangles], dtype=np.int64)
    edges = core.unique_edges(faces)
    parent = np.arange(len(points))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in edges:
        a, b = find(int(a)), find(int(b))
        if a != b:
            parent[a] = b
    fragments = len({find(i) for i in range(len(points))})
    tree = BVHTree.FromPolygons(points.tolist(), faces.tolist(), all_triangles=True)
    crossing = sum(1 for a, b in tree.overlap(tree) if a < b and not set(faces[a]) & set(faces[b]))
    out = {"triangles": counts["triangles"], "open_edges": counts["boundary_edges"],
           "non_manifold_edges": counts["non_manifold_edges"], "zero_area_faces": counts["zero_area_faces"],
           "fragments": fragments, "self_intersecting_face_pairs": crossing}
    out["defects"] = out["open_edges"] + out["non_manifold_edges"] + out["zero_area_faces"] + (fragments - 1) + crossing
    if "fingers" in rule:
        height = points @ core.long_axis(points, [0.0, 0.0, -1.0])
        # a finger is a sizeable part of the mesh: knuckle plates and rivets also stand apart for a few vertices
        smallest = rule.get("min_vertices", max(PAIR_DEFAULTS["min_finger_vertices"],
                                                 int(len(points) * rule.get("min_finger_share", PAIR_DEFAULTS["min_finger_share"]))))
        tubes = core.finger_tubes(height - height.min(), edges, smallest, 0.0)
        out["separate_fingers"] = len(tubes)
        out["free_finger_length_m"] = float(sum(height[members].max() - height.min() - joined for members, joined in tubes))
        out["fingers_expected"] = rule["fingers"]
    return out


def better_of(first: dict, second: dict) -> tuple[int, str]:
    """0 or 1: which half to keep, and the measure that decided."""
    order = [("defects", 1, lambda q: q["defects"])]
    if "separate_fingers" in first:
        order += [("separate_fingers", 1, lambda q: abs(q["separate_fingers"] - q["fingers_expected"])),
                  ("free_finger_length_m", -1, lambda q: round(q["free_finger_length_m"], 3))]
    order.append(("triangles", -1, lambda q: q["triangles"]))        # more source detail
    for name, sign, value in order:
        a, b = sign * value(first), sign * value(second)
        if a != b:
            return (0 if a < b else 1), name
    return 0, "tie"


def mirrored_mesh(source_obj, target_obj, centre: float, axis: int = 0):
    """Mesh for ``target_obj``: the mesh of ``source_obj`` reflected across the plane at ``centre`` on world ``axis``."""
    mesh = source_obj.data.copy()
    points = world_points(source_obj)
    points[:, axis] = 2.0 * centre - points[:, axis]
    inverse = np.asarray(target_obj.matrix_world.inverted())
    mesh.vertices.foreach_set("co", (points @ inverse[:3, :3].T + inverse[:3, 3]).ravel())
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.reverse_faces(bm, faces=bm.faces)                      # a reflection turns every face inside out
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return mesh


def symmetrize(obj, rule: dict, centre: float, axis: int = 0) -> dict:
    """Make ``obj`` the same on both sides of the plane at ``centre`` on world ``axis``: one half kept, mirrored and joined.

    ``rule["keep"]`` is the side of the character that stays, "R" or "L"; the character's right is the
    negative side of the axis. Pieces are expected unrotated, as the split leaves them.
    """
    settings = {**SYMMETRIZE_DEFAULTS, **{key: value for key, value in rule.items() if key in SYMMETRIZE_DEFAULTS}}
    matrix = np.asarray(obj.matrix_world)
    if not np.allclose(matrix[:3, :3], np.eye(3), atol=1e-6):
        raise SystemExit(f"symmetrize: {obj.name} is rotated or scaled")
    shift = [0.0, 0.0, 0.0]
    shift[axis] = centre - matrix[axis, 3]
    offset = Vector(shift)                                           # the plane, in the piece's own frame
    before = topology(obj.data)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.translate(bm, verts=bm.verts, vec=-offset)
    # direction names the half that is overwritten: "X" copies -X onto +X
    letter = "XYZ"[axis]
    bmesh.ops.symmetrize(bm, input=bm.verts[:] + bm.edges[:] + bm.faces[:],
                         direction=letter if settings["keep"] == "R" else "-" + letter, dist=settings["weld_m"])
    bmesh.ops.translate(bm, verts=bm.verts, vec=offset)
    bmesh.ops.dissolve_degenerate(bm, dist=settings["degenerate_m"], edges=bm.edges)     # slivers left where the halves meet
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    points = world_points(obj)
    mirrored = points.copy()
    mirrored[:, axis] = 2.0 * centre - mirrored[:, axis]
    tree = BVHTree.FromPolygons(points.tolist(), [tuple(polygon.vertices) for polygon in obj.data.polygons])
    worst = max(tree.find_nearest(Vector(point))[3] for point in mirrored[:: max(1, len(mirrored) // 4000)])
    after = topology(obj.data)
    return {"kept": settings["keep"], "plane_axis": "xyz"[axis], "plane_at_m": centre,
            "triangles_before": before["triangles"], "triangles_after": after["triangles"],
            "open_edges": after["boundary_edges"], "non_manifold_edges": after["non_manifold_edges"],
            "mirror_error_m": float(worst)}


RECESS_GROUP = "a3d_recess"      # vertices of a recessed lid: the fit leaves them out, the limb passes through them
RECESS_DEFAULTS = {"zone": 0.12,        # share of the piece, at the end the opening is on, where the lid is looked for
                   "facing": 0.8,       # a face of the lid looks out of the opening at least this much (cosine)
                   "grow": 0.3,         # and the lid spreads over its slopes down to this
                   "enclose": 0.97,     # what lies nearer to the middle than this share of the lid's reach goes with it
                   "sectors": 24,
                   "depth": 0.5,        # how far down the floor goes, as a share of the length of the piece
                   "inside": 0.85,      # the floor is this share of the narrowest width of the piece at that level
                   "band": 0.06, "narrow_percentile": 10}     # how that width is read: slice of the piece, and its tight side


def recess_lid(obj, rule: dict) -> dict:
    """Push the lid over an opening of ``obj`` down into the piece, as a flat floor with a wall around it.

    ``rule["out"]`` is the direction the opening looks to (world), or ``"cuff"``: against the long axis of a
    gauntlet. The lid is the largest patch of faces at that end that face out, grown over its slopes until
    the surface turns into the wall of the piece, with everything it encloses (a lid is a lumpy plug, not a
    flat disc). Only vertices are moved: every vertex inside the lid goes to one level, ``depth`` of the
    length of the piece below the edge of the lid. A piece is narrower down there than at its mouth, so the
    floor is the outline of the lid centred on the piece at that level and shrunk to ``inside`` of its
    narrowest width there. The edge itself does not move: the faces that hang from it become the wall.
    """
    settings = {**RECESS_DEFAULTS, **{key: value for key, value in rule.items() if key in RECESS_DEFAULTS}}
    points = world_points(obj)
    out = -core.long_axis(points, [0.0, 0.0, -1.0]) if rule["out"] == "cuff" else core.unit(np.asarray(rule["out"], dtype=np.float64))
    matrix = np.asarray(obj.matrix_world)
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    centres = np.asarray([face.calc_center_median() for face in bm.faces]) @ matrix[:3, :3].T + matrix[:3, 3]
    normals = np.asarray([face.normal for face in bm.faces]) @ matrix[:3, :3].T
    areas = np.asarray([face.calc_area() for face in bm.faces])
    height = centres @ out
    zone = height > height.max() - settings["zone"] * np.ptp(height)
    facing = normals @ out

    def spread(lid: set, accept) -> None:
        stack = list(lid)
        while stack:
            face = bm.faces[stack.pop()]
            for edge in face.edges:
                for other in edge.link_faces:
                    if other.index not in lid and zone[other.index] and accept(other.index):
                        lid.add(other.index)
                        stack.append(other.index)
    patches, seen = [], set()
    for start in np.flatnonzero(zone & (facing > settings["facing"])).tolist():
        if start in seen:
            continue
        patch = {start}
        spread(patch, lambda index: facing[index] > settings["facing"])
        seen |= patch
        patches.append(patch)
    if not patches:
        bm.free()
        return {"recessed": False, "reason": "no faces look out of that end"}
    lid = max(patches, key=lambda patch: areas[list(patch)].sum())
    spread(lid, lambda index: facing[index] > settings["grow"])           # over the slopes, up to the wall
    members = np.asarray(sorted(lid))
    middle = (centres[members] * areas[members, None]).sum(axis=0) / areas[members].sum()
    u = core.unit(np.cross(out, [1.0, 0.0, 0.0] if abs(out[0]) < 0.9 else [0.0, 1.0, 0.0]))
    v = np.cross(out, u)

    def polar(positions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        offset = positions - middle
        return np.hypot(offset @ u, offset @ v), np.arctan2(offset @ v, offset @ u)
    radial, angle = polar(centres)
    sector = ((angle + np.pi) / (2 * np.pi) * settings["sectors"]).astype(int) % settings["sectors"]
    reach = np.full(settings["sectors"], float(np.median(radial[members])))
    np.maximum.at(reach, sector[members], radial[members])
    spread(lid, lambda index: radial[index] < settings["enclose"] * reach[sector[index]])   # and what the lid encloses
    lid_faces = [bm.faces[index] for index in lid]
    # the edge of the lid stays where it is; everything inside it moves along the opening
    inside = [vertex for vertex in {vertex for face in lid_faces for vertex in face.verts}
              if all(face.index in lid for face in vertex.link_faces)]
    edge = [vertex for vertex in {vertex for face in lid_faces for vertex in face.verts}
            if not all(face.index in lid for face in vertex.link_faces)]
    if not inside or not edge:
        bm.free()
        return {"recessed": False, "reason": "the lid has no inside"}
    edge_points = np.asarray([vertex.co for vertex in edge]) @ matrix[:3, :3].T + matrix[:3, 3]
    edge_radial, _ = polar(edge_points)
    radius = float(np.median(edge_radial))
    top = float(np.median(edge_points @ out))
    length = float(np.ptp(points @ out))
    level = top - settings["depth"] * length
    # the piece at the level of the floor: where its middle is and how narrow it gets
    lid_vertices = {vertex.index for face in lid_faces for vertex in face.verts}
    shell = np.asarray([point for index, point in enumerate(points) if index not in lid_vertices])
    band = shell[np.abs(shell @ out - level) < settings["band"] * length]
    inside_points = np.asarray([vertex.co for vertex in inside]) @ matrix[:3, :3].T + matrix[:3, 3]
    offsets = np.stack([(inside_points - middle) @ u, (inside_points - middle) @ v], axis=1)
    centre, shrink = np.zeros(2), 1.0
    if len(band) >= 8:
        across = np.stack([(band - middle) @ u, (band - middle) @ v], axis=1)
        centre = (across.min(axis=0) + across.max(axis=0)) / 2
        narrow = float(np.percentile(np.linalg.norm(across - centre, axis=1), settings["narrow_percentile"]))
        shrink = min(1.0, settings["inside"] * narrow / max(float(np.linalg.norm(offsets, axis=1).max()), 1e-9))
    inverse = np.linalg.inv(matrix[:3, :3])
    for vertex, offset in zip(inside, offsets):
        placed = centre + shrink * offset
        world = middle + placed[0] * u + placed[1] * v
        world = world + (level - world @ out) * out
        vertex.co = Vector((world - matrix[:3, 3]) @ inverse.T)
    moved = [vertex.index for vertex in inside]
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    (obj.vertex_groups.get(RECESS_GROUP) or obj.vertex_groups.new(name=RECESS_GROUP)).add(moved, 1.0, "REPLACE")
    return {"recessed": True, "direction": out.tolist(), "lid_faces": len(lid), "lid_area_m2": float(areas[list(lid)].sum()),
            "vertices_moved": len(inside), "opening_radius_m": radius, "piece_length_m": length,
            "floor_scale": shrink, "floor_shift_m": float(np.linalg.norm(centre)),
            "depth_m": settings["depth"] * length}


def weld(mesh, distance: float) -> dict:
    """Optional merge of coincident vertices, for inputs whose surface is split along seams.

    Off by default (distance 0): on Tripo parts the only coincident vertices sit on flat pockets,
    and merging them turns a closed manifold into one with boundary and non-manifold edges.
    """
    if distance <= 0:
        return {"distance": 0.0, "vertices_merged": 0}
    bm = bmesh.new()
    bm.from_mesh(mesh)
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=distance)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=distance)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return {"distance": distance, "vertices_merged": before - len(mesh.vertices)}


QUAD_DEFAULTS = {"enabled": False, "join_face_angle_deg": 40.0, "join_shape_angle_deg": 40.0, "min_quad_face_share": 0.0}


def join_quads(mesh, settings: dict) -> None:
    """Pair triangles back into quads where the pair is flat and well shaped enough."""
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.join_triangles(bm, faces=bm.faces, cmp_seam=False, cmp_sharp=False, cmp_uvs=False, cmp_vcols=False,
                             cmp_materials=False, angle_face_threshold=math.radians(settings["join_face_angle_deg"]),
                             angle_shape_threshold=math.radians(settings["join_shape_angle_deg"]))
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def collapsed(source_mesh, ratio: float, triangulate: bool = True, symmetric: bool = False):
    """A new mesh: ``source_mesh`` collapsed by ``ratio``; ``symmetric`` keeps the two sides of x = 0 alike."""
    holder = bpy.data.objects.new("__decimate_probe", source_mesh)
    bpy.context.scene.collection.objects.link(holder)
    modifier = holder.modifiers.new("Decimate", "DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = ratio
    modifier.use_collapse_triangulate = triangulate
    modifier.use_symmetry, modifier.symmetry_axis = symmetric, "X"
    result = bpy.data.meshes.new_from_object(holder.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    bpy.data.objects.remove(holder, do_unlink=True)
    return result


def collapse_to(source_mesh, target: int, tolerance: float, triangulate: bool = True, symmetric: bool = False):
    """Bisect the ratio until the triangle count is within ``tolerance`` of ``target``."""
    low, high = 0.0, 1.0
    best = None
    for _ in range(24):
        ratio = (low + high) / 2
        mesh = collapsed(source_mesh, ratio, triangulate, symmetric)
        count = triangles(mesh)
        if best is None or abs(count - target) < abs(best[1] - target):
            if best:
                bpy.data.meshes.remove(best[0])
            best = (mesh, count, ratio)
        else:
            bpy.data.meshes.remove(mesh)
        if abs(count - target) <= tolerance * target:
            break
        low, high = (ratio, high) if count < target else (low, ratio)
    return best


def shade(mesh, crease_deg: float) -> None:
    """Split normals from the real topology; the imported custom normals do not survive collapses."""
    if "custom_normal" in mesh.attributes:
        mesh.attributes.remove(mesh.attributes["custom_normal"])
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    mesh.set_sharp_from_angle(angle=math.radians(crease_deg))
    mesh.update()


def bvh(obj) -> BVHTree:
    mesh = obj.data
    mesh.calc_loop_triangles()
    points = [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
    return BVHTree.FromPolygons(points, [tuple(triangle.vertices) for triangle in mesh.loop_triangles])


def samples(obj) -> list[Vector]:
    mesh = obj.data
    points = [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
    mesh.calc_loop_triangles()
    return points + [(points[t.vertices[0]] + points[t.vertices[1]] + points[t.vertices[2]]) / 3 for t in mesh.loop_triangles]


def one_way(points: list[Vector], tree: BVHTree) -> np.ndarray:
    return np.asarray([tree.find_nearest(point)[3] for point in points])


def distance_report(before, after, diagonal: float) -> dict:
    forward, backward = one_way(samples(before), bvh(after)), one_way(samples(after), bvh(before))
    both = np.concatenate([forward, backward])
    return {"note": "nearest-surface distance at vertices and triangle centroids, both directions; not a continuous Hausdorff",
            "source_to_result_max_m": float(forward.max()), "result_to_source_max_m": float(backward.max()),
            "max_m": float(both.max()), "p99_m": float(np.percentile(both, 99)), "mean_m": float(both.mean()),
            "max_over_diagonal": float(both.max() / diagonal), "p99_over_diagonal": float(np.percentile(both, 99) / diagonal)}


def setup_render(scene) -> None:
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "SINGLE"
    scene.display.shading.single_color = (0.78, 0.78, 0.80)
    scene.display.shading.show_cavity = True
    scene.display.shading.cavity_type = "BOTH"
    camera = bpy.data.objects.new("DecimateCamera", bpy.data.cameras.new("DecimateCamera"))
    scene.collection.objects.link(camera)
    camera.data.type = "ORTHO"
    scene.camera = camera


def render(scene, visible, hidden, direction, low: np.ndarray, high: np.ndarray, resolution: int, path: Path) -> np.ndarray:
    """Render ``visible`` alone, seen from the side ``direction`` points to, in the fixed frame of the source bounds."""
    for obj in hidden:
        obj.hide_render = True
    visible.hide_render = False
    centre, radius = (low + high) / 2, float(np.linalg.norm(high - low)) / 2
    axis = Vector(direction).normalized()
    camera = scene.camera
    camera.location = Vector(centre) + axis * (radius * 4)
    camera.rotation_euler = axis.to_track_quat("Z", "Y").to_euler()
    camera.data.ortho_scale = radius * 2.1
    camera.data.clip_end = radius * 10
    scene.render.resolution_x = scene.render.resolution_y = resolution
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path))
    pixels = np.empty(resolution * resolution * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    bpy.data.images.remove(image)
    return pixels.reshape(resolution, resolution, 4)


def save_image(pixels: np.ndarray, path: Path) -> None:
    height, width = pixels.shape[:2]
    image = bpy.data.images.new("sheet", width, height, alpha=True)
    image.pixels.foreach_set(np.ascontiguousarray(pixels, dtype=np.float32).ravel())
    image.filepath_raw, image.file_format = str(path), "PNG"
    image.save()
    bpy.data.images.remove(image)


def over(pixels: np.ndarray, background=(0.16, 0.16, 0.17)) -> np.ndarray:
    alpha = pixels[..., 3:4]
    return np.concatenate([pixels[..., :3] * alpha + np.asarray(background, dtype=np.float32) * (1 - alpha),
                           np.ones_like(alpha)], axis=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--budgets", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    source, out = args.input.resolve(), args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit("Output must be a new or empty directory")
    (out / "renders").mkdir(parents=True)
    config = json.loads(args.budgets.read_text(encoding="utf-8"))
    gates_config = config["gates"]
    quads = {**QUAD_DEFAULTS, **config.get("quads", {})}
    if set(quads) != set(QUAD_DEFAULTS):
        raise SystemExit(f"Unknown quads options: {sorted(set(quads) - set(QUAD_DEFAULTS))}")
    reduce = config.get("reduce", "to_budget")
    if reduce not in ("to_budget", "over_budget"):
        raise SystemExit("reduce must be to_budget or over_budget")
    config = {**config, "quads": quads, "reduce": reduce}
    source_hash = sha256(source)

    bpy.ops.wm.open_mainfile(filepath=str(source))
    scene = bpy.context.scene
    setup_render(scene)
    missing = sorted(set(config["budgets"]) - {obj.name for obj in scene.objects if obj.type == "MESH"})
    if missing:
        raise SystemExit(f"Parts missing from the input: {missing}")

    # pairs: keep the better half, put its mirror image in place of the other
    bounds = [world_bounds(scene.objects[name]) for name in config["budgets"]]
    axis = "xyz".index(config.get("mirror_axis", MIRROR_AXIS))
    centre_x = float(min(low[axis] for low, _ in bounds) + max(high[axis] for _, high in bounds)) / 2
    pairs, mirror_of = [], {}
    for rule in config.get("mirror_pairs", []):
        names = rule["pieces"]
        if len(names) != 2 or any(name not in config["budgets"] for name in names):
            raise SystemExit(f"mirror_pairs: a pair is two budgeted pieces, got {names}")
        quality = [pair_quality(scene.objects[name], rule) for name in names]
        # the two halves as they came, side by side, the second one mirrored so that they can be told apart by eye
        strip = []
        for index, name in enumerate(names):
            shown = scene.objects[name]
            flipped = None
            if index == 1:
                flipped = bpy.data.objects.new(name + "__mirrored_view", mirrored_mesh(shown, scene.objects[names[0]], centre_x, axis))
                flipped.matrix_world = scene.objects[names[0]].matrix_world.copy()
                scene.collection.objects.link(flipped)
                shown = flipped
            low, high = world_bounds(scene.objects[names[0]])
            everything = [obj for obj in scene.objects if obj.type == "MESH"]
            strip += [over(render(scene, shown, everything, direction, low, high, SHEET_RESOLUTION,
                                  out / "renders" / f"pair_{name}_{view}.png")) for view, direction in SHEET_VIEWS.items()]
            if flipped is not None:
                bpy.data.objects.remove(flipped, do_unlink=True)
        save_image(np.concatenate(strip, axis=1), out / f"pair_{names[0]}_{names[1]}.png")
        keep = rule.get("keep", "auto")
        if keep == "auto":
            index, reason = better_of(*quality)
        elif keep in names:
            index, reason = names.index(keep), "set in the budgets file"
        else:
            raise SystemExit(f"mirror_pairs: keep must be auto or one of {names}")
        kept, replaced = names[index], names[1 - index]
        target = scene.objects[replaced]
        target.data = mirrored_mesh(scene.objects[kept], target, centre_x, axis)
        mirror_of[replaced] = kept
        pairs.append({"pieces": names, "kept": kept, "replaced_by_mirror": replaced, "decided_by": reason,
                      "sheet": f"pair_{names[0]}_{names[1]}.png",
                      "sheet_layout": f"front, three-quarter, back of {names[0]}, then of {names[1]} mirrored onto it",
                      "mirror_plane": {"axis": "xyz"[axis], "at_m": centre_x}, "quality": dict(zip(names, quality))})

    # halves: one side kept and mirrored onto the other
    halves = {}
    for rule in config.get("symmetrize", []):
        for name in rule["pieces"]:
            if name not in config["budgets"]:
                raise SystemExit(f"symmetrize: {name} is not a budgeted piece")
            target = scene.objects[name]
            target.data = target.data.copy()
            halves[name] = {**symmetrize(target, rule, centre_x, axis), "rule": rule}

    # lids pushed down into the piece; on the sources, so that everything after compares with the recessed piece
    recesses = {}
    for rule in config.get("recess_lids", []):
        for name in rule["pieces"]:
            if name not in config["budgets"]:
                raise SystemExit(f"recess_lids: {name} is not a budgeted piece")
            target = scene.objects[name]
            target.data = target.data.copy()
            if quads["enabled"]:
                join_quads(target.data, quads)      # first: the faces stretched into a wall would not pair up after
            recesses[name] = recess_lid(target, rule)

    parts, rows, results, reduced = [], [], [], {}
    for name, budget in sorted(config["budgets"].items(), key=lambda item: item[0] in mirror_of):
        before = scene.objects[name]
        low, high = world_bounds(before)
        diagonal = float(np.linalg.norm(high - low))
        work = before.data.copy()
        for attribute in [a.name for a in work.attributes if a.name.startswith("_a3s_")]:
            work.attributes.remove(work.attributes[attribute])  # per-element source IDs do not survive a collapse
        welded = weld(work, diagonal * config["weld_over_diagonal"])
        welded_topology = topology(work)
        if quads["enabled"]:
            join_quads(work, quads)
        recovered = topology(work)
        if name in mirror_of:
            # the same topology as the kept half, mirrored: the two are reduced once
            bpy.data.meshes.remove(work)
            mesh, ratio = mirrored_mesh(reduced[mirror_of[name]][0], before, centre_x, axis), reduced[mirror_of[name]][1]
            if ratio < 1.0:
                shade(mesh, config["crease_deg"])
        elif reduce == "over_budget" and recovered["triangles"] <= budget:
            mesh, ratio = work, 1.0                      # already fits: geometry and normals stay as imported
        else:
            target = budget
            joining = {**SYMMETRIZE_DEFAULTS, **halves[name]["rule"]} if name in halves else SYMMETRIZE_DEFAULTS
            for _ in range(joining["attempts"]):
                # the reduction itself can keep the two sides alike only about the origin of the piece
                mesh, _, ratio = collapse_to(work, target, config["target_tolerance"], triangulate=not quads["enabled"],
                                             symmetric=name in halves and axis == 0 and abs(centre_x) < 1e-6)
                if name not in halves:
                    break
                # a reduction does not treat the two sides alike: the reduced piece is made the same on both
                # again. Joining the halves adds faces along the middle, so the reduction aims lower until it fits
                probe = bpy.data.objects.new(name + "__halves", mesh)
                probe.matrix_world = before.matrix_world.copy()
                scene.collection.objects.link(probe)
                halves[name]["after_reduction"] = symmetrize(probe, halves[name]["rule"], centre_x, axis)
                bpy.data.objects.remove(probe, do_unlink=True)
                excess = triangles(mesh) - budget
                if excess <= 0:
                    break
                bpy.data.meshes.remove(mesh)
                target -= excess + joining["margin_triangles"]
            bpy.data.meshes.remove(work)
            if quads["enabled"]:
                join_quads(mesh, quads)
            shade(mesh, config["crease_deg"])
        after = bpy.data.objects.new(name + "__decimated", mesh)
        after.matrix_world = before.matrix_world.copy()
        scene.collection.objects.link(after)
        if name in recesses and recesses[name]["recessed"]:
            after.vertex_groups.new(name=RECESS_GROUP)       # the weights came through the reduction with the mesh
        reduced[name] = (after, ratio)
        bpy.context.view_layer.update()
        parts.append((before, after, low, high))
        results.append({"name": name, "budget": budget, "ratio": ratio, "reduced": ratio < 1.0, "weld": welded,
                        "mirror_of": mirror_of.get(name), "symmetrized": halves.get(name), "recess": recesses.get(name),
                        "quads_recovered": recovered if quads["enabled"] else None, "diagonal_m": diagonal,
                        "before": topology(before.data), "after_weld": welded_topology, "after": topology(mesh),
                        "distance": distance_report(before, after, diagonal)})

    everything = [obj for pair in parts for obj in pair[:2]]
    for (before, after, low, high), result in zip(parts, results):
        name = result["name"]
        iou = {}
        for view, direction in VIEWS.items():
            masks = [render(scene, obj, everything, direction, low, high, MASK_RESOLUTION,
                            out / "renders" / f"{name}_{view}_{tag}.png")[..., 3] > 0.5
                     for obj, tag in ((before, "before"), (after, "after"))]
            iou[view] = float((masks[0] & masks[1]).sum() / max(1, (masks[0] | masks[1]).sum()))
        result["silhouette_iou"] = iou
        strip = [over(render(scene, obj, everything, direction, low, high, SHEET_RESOLUTION,
                             out / "renders" / f"{name}_sheet_{view}_{tag}.png"))
                 for direction_name, direction in SHEET_VIEWS.items() for view in (direction_name,)
                 for obj, tag in ((before, "before"), (after, "after"))]
        rows.append(np.concatenate(strip, axis=1))
        save_image(rows[-1], out / "renders" / f"{name}_before_after.png")
        count, budget = result["after"]["triangles"], result["budget"]
        result["gates"] = {
            "triangles_within_budget": (count <= budget * (1 + gates_config["budget_tolerance"])) if not result["reduced"]
            else abs(count - budget) <= gates_config["budget_tolerance"] * budget,
            "quad_face_share": result["after"]["quad_face_share"] >= quads["min_quad_face_share"],
            "silhouette_iou": min(iou.values()) >= gates_config["min_silhouette_iou"],
            "distance_p99": result["distance"]["p99_over_diagonal"] <= gates_config["max_p99_distance_over_diagonal"],
            "distance_max": result["distance"]["max_over_diagonal"] <= gates_config["max_distance_over_diagonal"],
            "no_new_non_manifold_edges": result["after"]["non_manifold_edges"] <= result["before"]["non_manifold_edges"],
            "no_new_boundary_edges": result["after"]["boundary_edges"] <= result["before"]["boundary_edges"],
            "no_zero_area_faces": result["after"]["zero_area_faces"] == 0,
        }
        result["status"] = "PASS" if all(result["gates"].values()) else "FAIL"
    save_image(np.concatenate(rows[::-1], axis=0), out / "before_after_sheet.png")

    for before, after, _, _ in parts:
        name = before.name
        bpy.data.objects.remove(before, do_unlink=True)
        after.name, after.data.name = name, name + "_Mesh"
        after.hide_render = False
    for obj in [obj for obj in bpy.data.objects if obj.type != "MESH"]:
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.outliner.orphans_purge(do_recursive=True)
    blend = out / "armour_decimated.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    if sha256(source) != source_hash:
        raise SystemExit("Input file changed")
    status = "PASS" if all(result["status"] == "PASS" for result in results) else "FAIL"
    report = {"status": status, "input": str(source), "input_sha256": source_hash, "input_unchanged": True,
              "blender_version": bpy.app.version_string, "config": config, "blend_sha256": sha256(blend),
              "triangles_before": sum(r["before"]["triangles"] for r in results),
              "triangles_after": sum(r["after"]["triangles"] for r in results),
              "quad_faces_after": sum(r["after"]["quad_faces"] for r in results),
              "tri_faces_after": sum(r["after"]["tri_faces"] for r in results), "pairs": pairs, "parts": results,
              "sheet": "before_after_sheet.png",
              "sheet_layout": "one row per part; columns: front, three-quarter, back, each as before | after"}
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{status}: {out / 'report.json'}")
    for pair in pairs:
        print(f"  pair {pair['pieces']}: kept {pair['kept']} ({pair['decided_by']}), {pair['replaced_by_mirror']} is its mirror")
    for result in results:
        distance = result["distance"]
        print(f"  {result['name']:8s} {result['before']['triangles']:6d} -> {result['after']['triangles']:6d} "
              f"(budget {result['budget']}) quads {result['after']['quad_face_share'] * 100:.0f}% IoU min {min(result['silhouette_iou'].values()):.4f} "
              f"p99 {distance['p99_m'] * 1000:.2f} mm max {distance['max_m'] * 1000:.2f} mm {result['status']} "
              f"{[gate for gate, ok in result['gates'].items() if not ok]}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
