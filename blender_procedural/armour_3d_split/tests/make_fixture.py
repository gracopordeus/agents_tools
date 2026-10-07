"""Headless regression input: transforms, shared boundaries, two UVs and attributes."""
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

bpy.ops.wm.read_factory_settings(use_empty=True)
directory = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
directory.mkdir(parents=True, exist_ok=True)
materials = [bpy.data.materials.new(name) for name in ("Metal", "Leather", "Extra")]
corners = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
           (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def mesh_object(name, vertices, polygons):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], polygons)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    for material in materials:
        mesh.materials.append(material)
    for polygon in mesh.polygons:
        polygon.material_index = polygon.index % 3
    for name in ("UVMap", "Lightmap"):
        layer = mesh.uv_layers.new(name=name)
        for index, item in enumerate(layer.data):
            item.uv = ((index % 4) * 0.125, (index % 7) * 0.0625)
    color = mesh.color_attributes.new(name="Paint", type="FLOAT_COLOR", domain="CORNER")
    for index, item in enumerate(color.data):
        item.color = ((index % 3) * 0.25, 0.5, 0.75, 1)
    region = mesh.attributes.new("region_code", "INT", "POINT")
    for index, item in enumerate(region.data):
        item.value = index % 4
    for edge in mesh.edges:
        edge.use_seam = edge.index % 3 == 0
        edge.use_edge_sharp = edge.index % 2 == 0
    mesh.update()
    normals = [(polygon.normal + Vector((0.03, 0.07, 0.02))).normalized()
               for polygon in mesh.polygons for _ in polygon.loop_indices]
    mesh.normals_split_custom_set(normals)
    return obj


vertices = corners + [(x + 4, y, z) for x, y, z in corners]
armour = mesh_object("Armour", vertices, faces + [tuple(i + 8 for i in face) for face in faces])
armour.location = (0.5, -0.2, 0.8)
armour.rotation_euler = (0.2, -0.3, 0.4)
armour.scale = (1.25, 0.75, 1.5)
extra = mesh_object("PlateExtra", [(0, 0, 0), (0.5, 0, 0), (0, 0.5, 0)], [(0, 1, 2)])
extra.location = (0, 0, 2)
extra.rotation_euler = (-0.2, 0.1, 0.3)
bpy.context.view_layer.update()
bpy.ops.wm.save_as_mainfile(filepath=str(directory / "fixture.blend"))
(directory / "assignment.json").write_text(json.dumps({"version": 1, "input_sha256": "REPLACE_AFTER_SAVE",
    "parts": [{"name": "Plate_A", "selectors": [{"object": "Armour", "faces": [0, 1, 2]}]},
              {"name": "Plate_B", "selectors": [{"object": "Armour", "faces": [3, 4, 5]}]},
              {"name": "Plate_C", "selectors": [{"object": "Armour", "component": 1},
                                                  {"object": "PlateExtra"}]}]}, indent=2))
