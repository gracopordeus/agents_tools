"""Pure Python assignment rules. Every selected source polygon must belong to one part."""
from __future__ import annotations

import re

MEDIEVAL_SHA256 = "e422e03e5c4dd798eedd6eb6c7f56b474dda5a3bc6c73a233ce1145c6e258733"


def assignments(plan: dict, inventory: dict) -> dict[str, dict[str, list[int]]]:
    if not isinstance(plan, dict) or set(plan) - {"version", "input_sha256", "dependency_hashes", "parts", "note"}:
        raise ValueError("Unknown plan fields; use version, input_sha256, dependency_hashes, parts, note")
    if plan.get("version") != 1 or plan.get("input_sha256") != inventory["input_sha256"]:
        raise ValueError("Plan version/input hash does not match the inspected source")
    if plan.get("dependency_hashes", {}) != inventory.get("dependency_hashes", {}):
        raise ValueError("Plan external-resource hashes differ from inspection")
    objects = {obj["name"]: obj for obj in inventory["objects"]}
    parts = plan.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("Plan requires nonempty parts")
    owners = {name: [None] * obj["polygons"] for name, obj in objects.items()}
    output = {}
    for part in parts:
        if not isinstance(part, dict) or set(part) != {"name", "selectors"}:
            raise ValueError("Part must contain name and selectors")
        name = part["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name):
            raise ValueError("Part names must be safe Blender/file identifiers")
        if name in output:
            raise ValueError(f"Duplicate part name: {name}")
        output[name] = {}
        if not isinstance(part["selectors"], list) or not part["selectors"]:
            raise ValueError(f"Empty selectors: {name}")
        for selector in part["selectors"]:
            if not isinstance(selector, dict) or "object" not in selector:
                raise ValueError("Each selector requires an exact source object name")
            source = selector["object"]
            if source not in objects:
                raise ValueError(f"Unknown selected source object: {source}")
            obj = objects[source]
            fields = set(selector) - {"object"}
            if not fields:
                faces = list(range(obj["polygons"]))
            elif fields == {"component"}:
                component = selector["component"]
                if type(component) is not int or not 0 <= component < len(obj["components"]):
                    raise ValueError(f"Invalid component for {source}: {component}")
                faces = obj["components"][component]["face_ids"]
            elif fields == {"material_slot"}:
                slot = selector["material_slot"]
                if type(slot) is not int or not 0 <= slot < len(obj["materials"]):
                    raise ValueError(f"Invalid material slot for {source}")
                faces = [i for i, value in enumerate(obj["polygon_material_slots"]) if value == slot]
            elif fields == {"faces"}:
                faces = selector["faces"]
                if not isinstance(faces, list) or any(type(i) is not int or not 0 <= i < obj["polygons"] for i in faces):
                    raise ValueError(f"Invalid face IDs for {source}")
            else:
                raise ValueError("Selector accepts only object, or object plus component/material_slot/faces")
            if not faces:
                raise ValueError(f"Empty selection for {name}/{source}")
            output[name].setdefault(source, [])
            for face in faces:
                if owners[source][face] is not None:
                    raise ValueError(f"Face duplicated: {source}/{face} in {owners[source][face]} and {name}")
                owners[source][face] = name
                output[name][source].append(face)
    missing = {name: sum(owner is None for owner in values) for name, values in owners.items()}
    if any(missing.values()):
        raise ValueError(f"Unassigned/orphan source faces: {missing}")
    return output


def medieval_plan(inventory: dict) -> dict:
    if inventory["input_sha256"] != MEDIEVAL_SHA256 or len(inventory["objects"]) != 1:
        raise ValueError("medieval_plate preset is bound to the original inspected GLB SHA-256")
    obj = inventory["objects"][0]
    components = obj["components"]
    if len(components) != 10 or obj["triangles"] != 58800:
        raise ValueError("medieval_plate source structure differs from its recorded baseline")
    mapping = {"Chest": [0], "Legs": [1], "Helmet": [2], "Boot_L": [3], "Boot_R": [4],
               "Glove_R": [5], "Glove_L": [6]}
    # The source contains tiny islands inside their corresponding plate bounds.
    # Only this exact hashed fixture may use the recorded anatomical assignments.
    for index in range(7, 10):
        center = [(a + b) / 2 for a, b in zip(components[index]["bbox_min"], components[index]["bbox_max"])]
        candidates = []
        for name, ids in mapping.items():
            main = components[ids[0]]
            if all(lo - 1e-6 <= x <= hi + 1e-6 for x, lo, hi in zip(center, main["bbox_min"], main["bbox_max"])):
                candidates.append(name)
        if len(candidates) != 1:
            raise ValueError(f"Fragment {index} requires explicit assignment: {candidates}")
        mapping[candidates[0]].append(index)
    return {"version": 1, "input_sha256": inventory["input_sha256"],
            "note": "Historical source-layout L/R names; no mirror or anatomical handedness correction.",
            "parts": [{"name": name, "selectors": [{"object": obj["name"], "component": i} for i in ids]}
                      for name, ids in mapping.items()]}
