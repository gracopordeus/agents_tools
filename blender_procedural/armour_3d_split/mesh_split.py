"""Blender adapter: native face separation, provenance, restoration and reopen audit."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

from partition import assignments, medieval_plan

PREFIX = "_a3s_"
NORMAL_TOLERANCE = 0.0002  # Blender custom-normal encoding is quantized.
# A flat pocket (coplanar faces around a vertex pointing both ways) has no usable
# vertex normal. Blender may then fail to build the custom-normal space and
# evaluate zero corner normals there, whatever the source file stores.
FOLDED_FAN_TOLERANCE = 0.05     # length of the mean face normal of the fan
FOLDED_FAN_COPLANAR_DOT = 0.99  # |cos| between every face normal of the fan and the first
ATTRIBUTE_PROPERTIES = {"FLOAT": "value", "INT": "value", "BOOLEAN": "value",
                        "FLOAT_VECTOR": "vector", "FLOAT2": "vector",
                        "FLOAT_COLOR": "color", "BYTE_COLOR": "color"}
DERIVED_ATTRIBUTES = {"position", "custom_normal", "material_index", "sharp_face", "sharp_edge", "uv_seam"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(out: Path, name: str, value: dict) -> None:
    (out / name).write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def verify_inputs(request: dict) -> None:
    if sha256(Path(request["input"])) != request["input_sha256"]:
        raise ValueError("Source file changed")
    for uri, resource in request["dependencies"].items():
        if sha256(Path(resource["path"])) != resource["sha256"]:
            raise ValueError(f"Source external resource changed: {uri}")


def world_vertices(obj) -> np.ndarray:
    return np.asarray([obj.matrix_world @ vertex.co for vertex in obj.data.vertices], dtype=np.float64)


def fan_shapes(positions: np.ndarray, polygons, vertices) -> dict[int, dict]:
    """Per vertex: length of the angle-weighted mean face normal (0 = cancelled, 1 = flat) and
    whether the fan is a flat pocket, i.e. coplanar faces that point both ways."""
    fans = {int(vertex): [] for vertex in vertices}
    for polygon in polygons:
        hits = [index for index, vertex in enumerate(polygon) if vertex in fans]
        if not hits:
            continue
        points = positions[list(polygon)]
        normal = np.cross(points, np.roll(points, -1, axis=0)).sum(axis=0)  # Newell
        length = np.linalg.norm(normal)
        for index in hits:
            a, b = points[index - 1] - points[index], points[(index + 1) % len(points)] - points[index]
            scale = np.linalg.norm(a) * np.linalg.norm(b)
            if scale < 1e-30 or length < 1e-30:
                continue
            fans[polygon[index]].append((float(np.arccos(np.clip(a @ b / scale, -1.0, 1.0))), normal / length))
    result = {}
    for vertex, corners in fans.items():
        weight = sum(angle for angle, _ in corners)
        if not weight:
            result[vertex] = {"residual": float("inf"), "folded": False}
            continue
        normals = np.asarray([normal for _, normal in corners])
        residual = float(np.linalg.norm(sum(angle * normal for angle, normal in corners)) / weight)
        alignment = normals @ normals[0]
        coplanar = bool((np.abs(alignment) >= FOLDED_FAN_COPLANAR_DOT).all())
        result[vertex] = {"residual": residual,
                          "folded": coplanar and bool((alignment < 0).any()) and residual <= FOLDED_FAN_TOLERANCE}
    return result


def normal_diagnostics(obj) -> dict:
    """Report imported shading defects without changing or normalizing the mesh."""
    mesh = obj.data
    values = np.asarray([normal.vector[:] for normal in mesh.corner_normals], dtype=np.float64)
    finite = np.isfinite(values).all(axis=1)
    zero = finite & (np.linalg.norm(values, axis=1) < 1e-12)
    invalid = np.flatnonzero(~finite | zero)
    folded = np.zeros(len(values), dtype=bool)
    fans = {"corner_count": 0, "vertices": [], "faces": [], "max_residual": None,
            "tolerance": FOLDED_FAN_TOLERANCE, "coplanar_dot": FOLDED_FAN_COPLANAR_DOT}
    if zero.any():
        corner_vertex = np.asarray([loop.vertex_index for loop in mesh.loops])
        positions = np.asarray([vertex.co[:] for vertex in mesh.vertices], dtype=np.float64)
        polygons = [tuple(polygon.vertices) for polygon in mesh.polygons]
        shapes = fan_shapes(positions, polygons, set(corner_vertex[zero].tolist()))
        accepted = sorted(vertex for vertex, shape in shapes.items() if shape["folded"])
        folded = zero & np.isin(corner_vertex, accepted)
        if accepted:
            members = set(accepted)
            fans.update(corner_count=int(folded.sum()), vertices=accepted,
                        faces=[index for index, polygon in enumerate(polygons) if members.intersection(polygon)],
                        max_residual=max(shapes[vertex]["residual"] for vertex in accepted))
    unexplained = np.flatnonzero((~finite | zero) & ~folded)
    return {"valid": not len(invalid), "splittable": not len(unexplained), "corners": len(values),
            "zero_length_count": int(zero.sum()), "nonfinite_count": int((~finite).sum()),
            "first_invalid_loop_ids": invalid[:32].tolist(), "folded_fans": fans,
            "unexplained_invalid_count": len(unexplained),
            "first_unexplained_loop_ids": unexplained[:32].tolist(),
            "folded_fan_corner_mask": folded}


def world_normals(obj, allowed_zero: np.ndarray | None = None) -> np.ndarray:
    """Unit world-space corner normals; corners in ``allowed_zero`` may stay zero, as evaluated."""
    transform = obj.matrix_world.to_3x3().inverted().transposed()
    values = np.asarray([transform @ normal.vector for normal in obj.data.corner_normals], dtype=np.float64)
    lengths = np.linalg.norm(values, axis=1)
    zero = lengths < 1e-12
    bad = ~np.isfinite(values).all(axis=1) | zero
    if allowed_zero is not None:
        bad &= ~np.asarray(allowed_zero, dtype=bool)
    if bad.any():
        invalid = np.flatnonzero(bad)
        raise ValueError(f"Invalid imported corner normals: {obj.name}; "
                         f"count={len(invalid)}, first_loop_ids={invalid[:32].tolist()}. "
                         "No automatic recalculation or geometry repair is performed.")
    values[zero] = 0.0
    return values / np.where(zero, 1.0, lengths)[:, None]


def connected_components(obj) -> list[dict]:
    mesh = obj.data
    parent = list(range(len(mesh.vertices)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for edge in mesh.edges:
        a, b = (root(index) for index in edge.vertices)
        parent[b] = a
    groups = {}
    for polygon in mesh.polygons:
        groups.setdefault(root(polygon.vertices[0]), []).append(polygon.index)
    mesh.calc_loop_triangles()
    triangle_counts = {}
    for triangle in mesh.loop_triangles:
        triangle_counts[triangle.polygon_index] = triangle_counts.get(triangle.polygon_index, 0) + 1
    world = world_vertices(obj)
    result = []
    for faces in groups.values():
        vertices = sorted({vertex for face in faces for vertex in mesh.polygons[face].vertices})
        points = world[vertices]
        result.append({"vertices": len(vertices), "triangles": sum(triangle_counts[i] for i in faces),
                       "face_ids": faces, "minimum_vertex_id": vertices[0],
                       "bbox_min": points.min(axis=0).tolist(), "bbox_max": points.max(axis=0).tolist()})
    result.sort(key=lambda item: (-item["vertices"], item["minimum_vertex_id"]))
    return [dict(index=i, **item) for i, item in enumerate(result)]


def inventory_for(request: dict, objects: list) -> dict:
    result = {"version": 1, "input": request["input"], "input_sha256": request["input_sha256"],
              "dependency_hashes": {uri: resource["sha256"] for uri, resource in request["dependencies"].items()},
              "blender_version": bpy.app.version_string, "scene": bpy.context.scene.name,
              "frame": bpy.context.scene.frame_current,
              "units": {"system": bpy.context.scene.unit_settings.system,
                        "scale_length": bpy.context.scene.unit_settings.scale_length}, "objects": []}
    for obj in objects:
        mesh = obj.data
        if obj.library or mesh.library:
            raise ValueError(f"Linked objects/meshes require a local source file first: {obj.name}")
        mesh.calc_loop_triangles()
        world = world_vertices(obj)
        if not len(mesh.polygons) or not np.isfinite(world).all():
            raise ValueError(f"Mesh is empty or nonfinite: {obj.name}")
        result["objects"].append({"name": obj.name, "mesh": mesh.name,
                                  "parent": obj.parent.name if obj.parent else None,
                                  "matrix_world": [list(row) for row in obj.matrix_world],
                                  "vertices": len(mesh.vertices), "polygons": len(mesh.polygons),
                                  "triangles": len(mesh.loop_triangles),
                                  "bbox_min": world.min(axis=0).tolist(), "bbox_max": world.max(axis=0).tolist(),
                                  "materials": [slot.material.name if slot.material else None for slot in obj.material_slots],
                                  "polygon_material_slots": [face.material_index for face in mesh.polygons],
                                  "uv_layers": [layer.name for layer in mesh.uv_layers],
                                  "imported_corner_normals": {key: value for key, value in normal_diagnostics(obj).items()
                                                              if key != "folded_fan_corner_mask"},
                                  "modifiers": [{"name": mod.name, "type": mod.type} for mod in obj.modifiers],
                                  "shape_keys": mesh.shape_keys is not None,
                                  "object_animation": obj.animation_data is not None,
                                  "weighted_vertices": sum(bool(vertex.groups) for vertex in mesh.vertices),
                                  "components": connected_components(obj)})
    result["triangles"] = sum(obj["triangles"] for obj in result["objects"])
    return result


def snapshots(objects: list) -> list[dict]:
    result = []
    for obj in objects:
        mesh = obj.data
        ancestors = []
        current = obj
        while current is not None:
            ancestors.append(current)
            current = current.parent
        if (obj.modifiers or mesh.shape_keys or any(vertex.groups for vertex in mesh.vertices)
                or any(item.animation_data or item.type == "ARMATURE" for item in ancestors)):
            raise ValueError(f"Static split only: modifiers, shape keys, skin/rig or animation require a separate adapter ({obj.name})")
        used_edges = {tuple(sorted(pair)) for polygon in mesh.polygons
                      for pair in zip(polygon.vertices, list(polygon.vertices)[1:] + [polygon.vertices[0]])}
        used_vertices = {index for polygon in mesh.polygons for index in polygon.vertices}
        if len(used_vertices) != len(mesh.vertices) or any(tuple(sorted(edge.vertices)) not in used_edges for edge in mesh.edges):
            raise ValueError(f"Loose edges/unreferenced vertices need explicit ownership: {obj.name}")
        if any(attribute.name.startswith(PREFIX) for attribute in mesh.attributes):
            raise ValueError(f"Reserved provenance attributes already exist: {obj.name}")
        if abs(obj.matrix_world.to_3x3().determinant()) < 1e-12:
            raise ValueError(f"Singular object transformation: {obj.name}")
        attributes = {}
        for attribute in mesh.attributes:
            if attribute.name.startswith(".") or attribute.name in DERIVED_ATTRIBUTES:
                continue
            prop = ATTRIBUTE_PROPERTIES.get(attribute.data_type)
            if prop is None or attribute.domain not in {"POINT", "EDGE", "FACE", "CORNER"}:
                raise ValueError(f"Unsupported mesh attribute: {obj.name}/{attribute.name}/{attribute.data_type}")
            values = []
            for item in attribute.data:
                value = getattr(item, prop)
                values.append(tuple(value) if hasattr(value, "__len__") else value)
            attributes[attribute.name] = {"domain": attribute.domain, "type": attribute.data_type,
                                           "property": prop, "values": values}
        # Blender's evaluated normal on a flat pocket is numerical noise (it flips to zero
        # or back when vertices are merely reordered), so those corners are not auditable.
        shapes = fan_shapes(np.asarray([vertex.co[:] for vertex in mesh.vertices], dtype=np.float64),
                            [tuple(polygon.vertices) for polygon in mesh.polygons], range(len(mesh.vertices)))
        pockets = [vertex for vertex, shape in shapes.items() if shape["folded"]]
        undefined = np.isin([loop.vertex_index for loop in mesh.loops], pockets)
        result.append({"name": obj.name, "positions": world_vertices(obj), "normals": world_normals(obj, undefined),
                       "normals_undefined": undefined, "pocket_vertices": sorted(pockets),
                       "normals_local_raw": np.asarray([normal.vector[:] for normal in mesh.corner_normals], dtype=np.float32),
                       "matrix_world": np.asarray([list(row) for row in obj.matrix_world]),
                       "faces": [tuple(face.vertices) for face in mesh.polygons],
                       "face_loops": [tuple(face.loop_indices) for face in mesh.polygons],
                       "face_smooth": [face.use_smooth for face in mesh.polygons],
                       "materials": [obj.material_slots[face.material_index].material.name
                                     if obj.material_slots and obj.material_slots[face.material_index].material else None
                                     for face in mesh.polygons],
                       "uv": {layer.name: np.asarray([item.uv[:] for item in layer.data]) for layer in mesh.uv_layers},
                       "edges": [tuple(edge.vertices) for edge in mesh.edges],
                       "edge_flags": [(edge.use_seam, edge.use_edge_sharp) for edge in mesh.edges],
                       "attributes": attributes})
    return result


def provenance(obj, source_id: int) -> None:
    mesh = obj.data
    for domain, suffix, count in [("POINT", "vertex", len(mesh.vertices)), ("EDGE", "edge", len(mesh.edges)),
                                  ("FACE", "face", len(mesh.polygons)), ("CORNER", "loop", len(mesh.loops))]:
        attribute = mesh.attributes.new(PREFIX + suffix, "INT", domain)
        attribute.data.foreach_set("value", np.arange(count, dtype=np.int32))
        attribute = mesh.attributes.new(PREFIX + suffix + "_object", "INT", domain)
        attribute.data.foreach_set("value", np.full(count, source_id, dtype=np.int32))


def activate(obj) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def separate_selected(working, faces: list[int], scene):
    mesh = working.data
    desired = set(faces)
    remaining_ids = [item.value for item in mesh.attributes[PREFIX + "face"].data]
    if len(desired) == len(remaining_ids) and desired == set(remaining_ids):
        return working, None
    activate(working)
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    for polygon, face in zip(mesh.polygons, remaining_ids):
        polygon.select = face in desired
    previous = set(scene.objects)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.separate(type="SELECTED")
    bpy.ops.object.mode_set(mode="OBJECT")
    created = [obj for obj in scene.objects if obj not in previous]
    if len(created) != 1:
        raise ValueError("Face separation did not produce exactly one mesh")
    return created[0], working


def restore_normals(obj, source: list[dict]) -> None:
    mesh = obj.data
    loops = mesh.attributes[PREFIX + "loop"].data
    object_ids = mesh.attributes[PREFIX + "loop_object"].data
    to_local = obj.matrix_world.to_3x3().transposed()
    expected_world = [source[oid.value]["normals"][loop.value] for oid, loop in zip(object_ids, loops)]
    backup = mesh.attributes.new(PREFIX + "source_normal_world", "FLOAT_VECTOR", "CORNER")
    backup.data.foreach_set("vector", np.asarray(expected_world, dtype=np.float32).ravel())
    backup_local = mesh.attributes.new(PREFIX + "source_normal_local_raw", "FLOAT_VECTOR", "CORNER")
    expected_local = [source[oid.value]["normals_local_raw"][loop.value] for oid, loop in zip(object_ids, loops)]
    backup_local.data.foreach_set("vector", np.asarray(expected_local, dtype=np.float32).ravel())
    expected_array = np.asarray(expected_world)
    undefined = np.asarray([source[oid.value]["normals_undefined"][loop.value] for oid, loop in zip(object_ids, loops)])

    def error() -> float:
        return float(np.linalg.norm(world_normals(obj, undefined) - expected_array, axis=1)[~undefined].max())

    native_error = error()
    obj["armour_split_normal_strategy"] = "native_preserved"
    if native_error <= NORMAL_TOLERANCE:
        return
    # Re-encoding malformed/custom fans can increase the error. Evaluate a copy,
    # retaining the native separated data when restoration cannot improve it.
    candidate = mesh.copy()
    obj.data = candidate
    candidate.normals_split_custom_set([(to_local @ Vector(normal)).normalized() for normal in expected_world])
    candidate.update()
    bpy.context.view_layer.update()
    restored_error = error()
    # Re-encoding may mark edges sharp; a candidate that edits source flags is not a restoration.
    flags_kept = all((old.use_seam, old.use_edge_sharp) == (new.use_seam, new.use_edge_sharp)
                     for old, new in zip(mesh.edges, candidate.edges))
    obj["armour_split_normal_native_error"], obj["armour_split_normal_restored_error"] = native_error, restored_error
    obj["armour_split_normal_restore_kept_edge_flags"] = flags_kept
    if restored_error < native_error and flags_kept:
        obj["armour_split_normal_strategy"] = "restored_lower_error"
        bpy.data.meshes.remove(mesh)
    else:
        obj.data = mesh
        bpy.data.meshes.remove(candidate)


def audit(objects: list, source: list[dict], expected: dict, normal_policy: str) -> dict:
    expected_sets = {name: {obj: set(faces) for obj, faces in selections.items()}
                     for name, selections in expected.items()}
    seen = set()
    rows = []
    max_position = max_normal = 0.0
    worst_normal = None
    undefined_corners = zero_corners = 0
    for obj in objects:
        mesh = obj.data
        point_ids = [item.value for item in mesh.attributes[PREFIX + "vertex"].data]
        point_objects = [item.value for item in mesh.attributes[PREFIX + "vertex_object"].data]
        face_ids = [item.value for item in mesh.attributes[PREFIX + "face"].data]
        face_objects = [item.value for item in mesh.attributes[PREFIX + "face_object"].data]
        loop_ids = [item.value for item in mesh.attributes[PREFIX + "loop"].data]
        loop_objects = [item.value for item in mesh.attributes[PREFIX + "loop_object"].data]
        domain_ids = {"POINT": (point_objects, point_ids), "FACE": (face_objects, face_ids),
                      "CORNER": (loop_objects, loop_ids),
                      "EDGE": ([item.value for item in mesh.attributes[PREFIX + "edge_object"].data],
                               [item.value for item in mesh.attributes[PREFIX + "edge"].data])}
        expected_normals = np.asarray([source[oid]["normals"][index] for oid, index in zip(loop_objects, loop_ids)])
        undefined = np.asarray([source[oid]["normals_undefined"][index] for oid, index in zip(loop_objects, loop_ids)])
        positions, normals = world_vertices(obj), world_normals(obj, undefined)
        expected_positions = np.asarray([source[oid]["positions"][index] for oid, index in zip(point_objects, point_ids)])
        position_error = float(np.linalg.norm(positions - expected_positions, axis=1).max())
        undefined_corners += int(undefined.sum())
        zero_corners += int((~normals.any(axis=1)).sum())
        backup_world = np.asarray([item.vector[:] for item in mesh.attributes[PREFIX + "source_normal_world"].data], dtype=np.float32)
        backup_local = np.asarray([item.vector[:] for item in mesh.attributes[PREFIX + "source_normal_local_raw"].data], dtype=np.float32)
        expected_local = np.asarray([source[oid]["normals_local_raw"][index]
                                     for oid, index in zip(loop_objects, loop_ids)], dtype=np.float32)
        if not np.array_equal(backup_world, expected_normals.astype(np.float32)) or not np.array_equal(backup_local, expected_local):
            raise ValueError("Lossless source-normal backup changed")
        normal_errors = np.where(undefined, 0.0, np.linalg.norm(normals - expected_normals, axis=1))
        normal_error = float(normal_errors.max())
        if normal_error > max_normal:
            index = int(np.argmax(normal_errors))
            worst_normal = {"part": obj.name, "output_loop": index,
                            "source_object": source[loop_objects[index]]["name"],
                            "source_loop": loop_ids[index], "expected": expected_normals[index].tolist(),
                            "observed": normals[index].tolist()}
        max_position, max_normal = max(max_position, position_error), max(max_normal, normal_error)
        for polygon, oid, fid in zip(mesh.polygons, face_objects, face_ids):
            key = (oid, fid)
            if key in seen or fid not in expected_sets[obj.name].get(source[oid]["name"], set()):
                raise ValueError(f"Duplicated/wrong owner source face: {key}")
            seen.add(key)
            if tuple(point_ids[i] for i in polygon.vertices) != source[oid]["faces"][fid]:
                raise ValueError("Source polygon winding/corner order changed")
            if tuple(loop_ids[i] for i in polygon.loop_indices) != source[oid]["face_loops"][fid]:
                raise ValueError("Source corner identity changed")
            if polygon.use_smooth != source[oid]["face_smooth"][fid]:
                raise ValueError("Source face smoothing flag changed")
            material = obj.material_slots[polygon.material_index].material if obj.material_slots else None
            if (material.name if material else None) != source[oid]["materials"][fid]:
                raise ValueError("Source face material assignment changed")
        for oid, snapshot in enumerate(source):
            mask = [index for index, object_id in enumerate(loop_objects) if object_id == oid]
            if not mask:
                continue
            for name, uv in snapshot["uv"].items():
                layer = mesh.uv_layers.get(name)
                if layer is None or any(tuple(layer.data[i].uv) != tuple(uv[loop_ids[i]]) for i in mask):
                    raise ValueError(f"UV changed: {obj.name}/{name}")
            for name, attribute in snapshot["attributes"].items():
                current = mesh.attributes.get(name)
                if current is None or (current.domain, current.data_type) != (attribute["domain"], attribute["type"]):
                    raise ValueError(f"Mesh attribute lost: {name}")
                object_ids, ids = domain_ids[attribute["domain"]]
                for index, owner in enumerate(object_ids):
                    if owner != oid:
                        continue
                    value = getattr(current.data[index], attribute["property"])
                    actual = tuple(value) if hasattr(value, "__len__") else value
                    if actual != attribute["values"][ids[index]]:
                        raise ValueError(f"Mesh attribute changed: {name}")
        changed = [edge.index for edge, oid, eid in zip(mesh.edges, *domain_ids["EDGE"])
                   if (edge.use_seam, edge.use_edge_sharp) != source[oid]["edge_flags"][eid]]
        if changed:
            raise ValueError(f"Seam/sharp edge flags changed: {obj.name}; count={len(changed)}, "
                             f"first_edge_ids={changed[:16]}, normal_strategy={obj.get('armour_split_normal_strategy')}")
        mesh.calc_loop_triangles()
        rows.append({"name": obj.name, "polygons": len(mesh.polygons), "triangles": len(mesh.loop_triangles),
                     "vertices": len(mesh.vertices), "world_position_max_error": position_error,
                     "world_corner_normal_max_vector_error": normal_error,
                     "normal_strategy": obj.get("armour_split_normal_strategy"),
                     "normal_native_error": obj.get("armour_split_normal_native_error"),
                     "normal_restored_error": obj.get("armour_split_normal_restored_error"),
                     "normal_restore_kept_edge_flags": obj.get("armour_split_normal_restore_kept_edge_flags"),
                     "uv_layers": [layer.name for layer in mesh.uv_layers],
                     "origin_world": list(obj.matrix_world.translation), "scale": list(obj.scale),
                     "rotation_euler": list(obj.rotation_euler)})
    expected_faces = {(oid, face) for oid, snapshot in enumerate(source) for face in range(len(snapshot["faces"]))}
    if seen != expected_faces:
        raise ValueError("Source face partition is incomplete")
    source_diagonal = float(np.linalg.norm(np.ptp(np.concatenate([item["positions"] for item in source]), axis=0)))
    position_tolerance = max(1e-7, source_diagonal * 1e-6)
    if max_position > position_tolerance or (max_normal > NORMAL_TOLERANCE and normal_policy == "strict"):
        raise ValueError(f"Preservation failed: position={max_position}, normal={max_normal}, worst={worst_normal}")
    normal_status = "PASS" if max_normal <= NORMAL_TOLERANCE else "LIMITATION"
    return {"status": "PASS" if normal_status == "PASS" else "PASS_WITH_NORMAL_LIMITATION",
            "parts": rows, "source_face_exact_partition": True,
            "source_polygon_winding_preserved": True, "uv_material_attributes_seam_sharp_preserved": True,
            "world_position_tolerance": position_tolerance, "world_position_max_error": max_position,
            "normal_vector_tolerance": NORMAL_TOLERANCE, "normal_vector_max_error": max_normal,
            "normal_policy": normal_policy, "evaluated_normal_preservation": normal_status,
            "source_normal_local_raw_backup_exact": True, "source_normal_world_backup_exact": True,
            "worst_normal": worst_normal, "flat_pocket_corners_not_audited": undefined_corners,
            "flat_pocket_zero_normal_corners": zero_corners,
            "normal_note": "Imported Blender corner vectors restored; quantized encoding is audited, not claimed bit-identical to raw GLB.",
            "triangles": sum(row["triangles"] for row in rows)}


def execute(request: dict) -> None:
    source_path, out = Path(request["input"]), Path(request["out"])
    verify_inputs(request)
    if source_path.suffix.lower() == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(source_path))
        if request["scene"]:
            scene = bpy.data.scenes.get(request["scene"])
            if scene is None:
                raise ValueError("Requested source scene does not exist")
            bpy.context.window.scene = scene
        elif len(bpy.data.scenes) != 1:
            raise ValueError("Multiple scenes: select the source explicitly with --scene")
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(source_path))
        if request["scene"] and request["scene"] != bpy.context.scene.name:
            raise ValueError("Requested scene does not match imported glTF scene")
    scene = bpy.context.scene
    all_meshes = {obj.name: obj for obj in scene.objects if obj.type == "MESH"}
    selected = request["objects"] if request["objects"] is not None else sorted(all_meshes)
    if not selected or len(selected) != len(set(selected)) or any(name not in all_meshes for name in selected):
        raise ValueError("No meshes, duplicate names, or missing selected object")
    objects = [all_meshes[name] for name in sorted(selected)]
    inventory = inventory_for(request, objects)
    write(out, "inventory.json", inventory)
    if request["command"] == "inspect":
        template = {"version": 1, "input_sha256": request["input_sha256"],
                    "dependency_hashes": inventory["dependency_hashes"],
                    "note": "Edit names/groupings after inspection; no anatomical inference.", "parts": []}
        for obj in inventory["objects"]:
            for component in obj["components"]:
                template["parts"].append({"name": f"Part_{len(template['parts']) + 1:03d}",
                                          "selectors": [{"object": obj["name"], "component": component["index"]}]})
        write(out, "plan.template.json", template)
        verify_inputs(request)
        return
    plan = medieval_plan(inventory) if request["preset"] else request["plan"]
    mapping = assignments(plan, inventory)
    source = snapshots(objects)
    # Native separate/join retain layers, flags and user attributes; provenance
    # makes preservation verifiable even where shared boundary vertices duplicate.
    output_scene = bpy.data.scenes.new("ArmourSplit")
    output_scene.unit_settings.system = scene.unit_settings.system
    output_scene.unit_settings.scale_length = scene.unit_settings.scale_length
    working = {}
    for oid, obj in enumerate(objects):
        copy = obj.copy()
        copy.data = obj.data.copy()
        copy.name = "__armour_split_work_" + str(oid)
        matrix = obj.matrix_world.copy()
        copy.parent = None
        copy.matrix_world = matrix
        copy.animation_data_clear()
        output_scene.collection.objects.link(copy)
        copy.hide_viewport = False
        copy.hide_render = False
        copy.hide_set(False, view_layer=output_scene.view_layers[0])
        provenance(copy, oid)
        working[obj.name] = copy
    bpy.context.window.scene = output_scene
    result = []
    for name, selections in mapping.items():
        chunks = []
        for original_name, faces in selections.items():
            chunk, remaining = separate_selected(working[original_name], faces, output_scene)
            working[original_name] = remaining
            chunks.append(chunk)
        target = chunks[0]
        activate(target)
        for chunk in chunks[1:]:
            chunk.select_set(True)
        if len(chunks) > 1:
            bpy.ops.object.join()
        activate(target)
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
        bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="BOUNDS")
        # Original scenes are removed below; release their names first.
        existing = bpy.data.objects.get(name)
        if existing and existing != target:
            existing.name = "__armour_split_source_" + name
        target.name = name
        target.data.name = name + "_Mesh"
        restore_normals(target, source)
        result.append(target)
    before_save = audit(result, source, mapping, request["normal_policy"])
    if before_save["triangles"] != inventory["triangles"]:
        raise ValueError("Triangulated count changed during separation")
    # Only selected armour parts are delivered, never unrelated bodies/cameras.
    for obj in list(bpy.data.objects):
        if obj not in result:
            bpy.data.objects.remove(obj, do_unlink=True)
    for other_scene in list(bpy.data.scenes):
        if other_scene != output_scene:
            bpy.data.scenes.remove(other_scene)
    bpy.ops.file.pack_all()
    write(out, "plan.resolved.json", plan)
    arrays = {}
    for oid, snapshot in enumerate(source):
        arrays[f"source_{oid}_positions_world"] = snapshot["positions"]
        arrays[f"source_{oid}_normals_world"] = snapshot["normals"]
        arrays[f"source_{oid}_normals_local_raw"] = snapshot["normals_local_raw"]
        arrays[f"source_{oid}_matrix_world"] = snapshot["matrix_world"]
        arrays[f"source_{oid}_polygon_lengths"] = [len(face) for face in snapshot["faces"]]
        arrays[f"source_{oid}_polygon_vertices"] = [vertex for face in snapshot["faces"] for vertex in face]
    np.savez_compressed(out / "source_provenance.npz", **arrays)
    blend = out / "armour_split_v001.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    reopened = [bpy.data.objects[name] for name in mapping]
    after_reopen = audit(reopened, source, mapping, request["normal_policy"])
    if after_reopen["triangles"] != inventory["triangles"]:
        raise ValueError("Reopen triangle count mismatch")
    verify_inputs(request)
    shutil.copyfile(blend, out / "armour_split.blend")
    pockets = {snapshot["name"]: {"vertices": len(snapshot["pocket_vertices"]),
                                  "corners": int(snapshot["normals_undefined"].sum()),
                                  "source_zero_normal_corners": int((~snapshot["normals"].any(axis=1)).sum()),
                                  "first_vertex_ids": snapshot["pocket_vertices"][:32]}
               for snapshot in source if snapshot["pocket_vertices"]}
    warnings = [f"{name}: normals on {item['corners']} corners of {item['vertices']} flat-pocket vertices are undefined in "
                f"Blender ({item['source_zero_normal_corners']} imported as zero); kept as evaluated, not audited or repaired"
                for name, item in pockets.items()]
    write(out, "report.json", {"status": after_reopen["status"], "warnings": warnings, "flat_pocket_normals": pockets,
                               "scope": "static split only; no decimation, fitting or mirroring",
                               "input_sha256": request["input_sha256"], "original_unchanged": True,
                               "before_save": before_save, "after_reopen": after_reopen,
                               "blend_sha256": sha256(blend), "preset_note": plan.get("note"),
                               "provenance": "_a3s_ attributes in output meshes plus source_provenance.npz"})
