"""Decimate separated armour parts to triangle budgets and measure what the reduction cost.

Runs inside Blender on the output of ``armour_3d_split``::

    blender -b --factory-startup --python-exit-code 2 -P armour_3d_decimate/decimate.py -- \
        --input split/armour_split.blend --budgets armour_3d_decimate/budgets.plate.json --out NEW_DIR

The input file is never saved. Every part is collapsed with
Blender's quadric Decimate to its budget, and compared with its source: two-way surface distance,
silhouette IoU on four orthographic views and manifoldness. Exit code 1 when a gate fails.
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
    return {"vertices": len(mesh.vertices), "triangles": triangles(mesh),
            "boundary_edges": int((uses == 1).sum()), "non_manifold_edges": int((uses > 2).sum()),
            "zero_area_faces": int((areas < 1e-14).sum())}


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


def collapsed(source_mesh, ratio: float):
    """A new mesh: ``source_mesh`` collapsed by ``ratio``."""
    holder = bpy.data.objects.new("__decimate_probe", source_mesh)
    bpy.context.scene.collection.objects.link(holder)
    modifier = holder.modifiers.new("Decimate", "DECIMATE")
    modifier.decimate_type = "COLLAPSE"
    modifier.ratio = ratio
    modifier.use_collapse_triangulate = True
    result = bpy.data.meshes.new_from_object(holder.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    bpy.data.objects.remove(holder, do_unlink=True)
    return result


def collapse_to(source_mesh, target: int, tolerance: float):
    """Bisect the ratio until the triangle count is within ``tolerance`` of ``target``."""
    low, high = 0.0, 1.0
    best = None
    for _ in range(24):
        ratio = (low + high) / 2
        mesh = collapsed(source_mesh, ratio)
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
    source_hash = sha256(source)

    bpy.ops.wm.open_mainfile(filepath=str(source))
    scene = bpy.context.scene
    setup_render(scene)
    missing = sorted(set(config["budgets"]) - {obj.name for obj in scene.objects if obj.type == "MESH"})
    if missing:
        raise SystemExit(f"Parts missing from the input: {missing}")

    parts, rows, results = [], [], []
    for name, budget in config["budgets"].items():
        before = scene.objects[name]
        low, high = world_bounds(before)
        diagonal = float(np.linalg.norm(high - low))
        work = before.data.copy()
        for attribute in [a.name for a in work.attributes if a.name.startswith("_a3s_")]:
            work.attributes.remove(work.attributes[attribute])  # per-element source IDs do not survive a collapse
        welded = weld(work, diagonal * config["weld_over_diagonal"])
        welded_topology = topology(work)
        mesh, count, ratio = collapse_to(work, budget, config["target_tolerance"])
        bpy.data.meshes.remove(work)
        shade(mesh, config["crease_deg"])
        after = bpy.data.objects.new(name + "__decimated", mesh)
        after.matrix_world = before.matrix_world.copy()
        scene.collection.objects.link(after)
        bpy.context.view_layer.update()
        parts.append((before, after, low, high))
        results.append({"name": name, "budget": budget, "ratio": ratio, "weld": welded, "diagonal_m": diagonal,
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
            "triangles_within_budget": abs(count - budget) <= gates_config["budget_tolerance"] * budget,
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
              "triangles_after": sum(r["after"]["triangles"] for r in results), "parts": results,
              "sheet": "before_after_sheet.png",
              "sheet_layout": "one row per part; columns: front, three-quarter, back, each as before | after"}
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{status}: {out / 'report.json'}")
    for result in results:
        distance = result["distance"]
        print(f"  {result['name']:8s} {result['before']['triangles']:6d} -> {result['after']['triangles']:6d} "
              f"(budget {result['budget']}) IoU min {min(result['silhouette_iou'].values()):.4f} "
              f"p99 {distance['p99_m'] * 1000:.2f} mm max {distance['max_m'] * 1000:.2f} mm {result['status']} "
              f"{[gate for gate, ok in result['gates'].items() if not ok]}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
