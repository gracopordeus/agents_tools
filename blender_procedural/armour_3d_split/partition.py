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


# how an assembled set is read: a figure standing, facing -Y, so that +X is its left
ASSEMBLED_DEFAULTS = {"main_share": 0.02,        # a component with at least this share of the triangles is a piece
                      "left_sign": 1.0,          # sign of X on the character's left
                      "pair_triangle_ratio": 0.5,   # the smaller of a pair has at least this share of the larger
                      "pieces": {"suit": "Suit", "helmet": "Helmet", "glove": "Glove", "boot": "Boot"}}


def assembled_plan(inventory: dict, rules: dict | None = None) -> dict:
    """Name the pieces of an assembled set of armour by where they stand: no hand-written plan.

    The body armour is the largest component; the helmet is the highest one on the middle of the body; the
    two lowest that stand apart are the boots; the two left are the gauntlets. Left and right come from the
    side of the body the piece is on. A small fragment goes to the piece whose box holds its centre. Anything
    that does not read this way (a seventh piece, both boots on one side, a fragment between two pieces) is
    an error: the plan is then written by hand after ``inspect``.
    """
    rules = {**ASSEMBLED_DEFAULTS, **(rules or {})}
    names = rules["pieces"]
    components = [{"object": obj["name"], **{key: value for key, value in component.items() if key != "face_ids"}}
                  for obj in inventory["objects"] for component in obj["components"]]
    total = sum(component["triangles"] for component in components)
    main = [component for component in components if component["triangles"] >= rules["main_share"] * total]
    if len(main) != 6:
        raise ValueError(f"An assembled set has 6 pieces; {len(main)} components hold at least {rules['main_share']:.0%} of the triangles")
    centre = lambda component, axis: (component["bbox_min"][axis] + component["bbox_max"][axis]) / 2
    low = min(component["bbox_min"][0] for component in main)
    high = max(component["bbox_max"][0] for component in main)
    middle = (low + high) / 2
    on_middle = lambda component: component["bbox_min"][0] < middle < component["bbox_max"][0]     # it spans the middle of the set
    suit = max(main, key=lambda component: component["triangles"])
    if not on_middle(suit):
        raise ValueError("The largest component is not on the middle of the set: not an assembled figure")
    rest = [component for component in main if component is not suit]
    centred = [component for component in rest if on_middle(component)]
    if len(centred) != 1 or centre(centred[0], 2) < suit["bbox_max"][2] - 0.1 * (suit["bbox_max"][2] - suit["bbox_min"][2]):
        raise ValueError(f"Expected one piece on the middle of the body above the body armour (the helmet); found {len(centred)}")
    helmet = centred[0]
    sides = sorted((component for component in rest if component is not helmet), key=lambda component: component["bbox_min"][2])
    boots, gloves = sides[:2], sides[2:]
    mapping = {names["suit"]: suit, names["helmet"]: helmet}
    for label, pair in ((names["boot"], boots), (names["glove"], gloves)):
        a, b = sorted(pair, key=lambda component: centre(component, 0) * rules["left_sign"])
        if (centre(a, 0) - middle) * (centre(b, 0) - middle) >= 0:
            raise ValueError(f"The two {label} pieces are on the same side of the body")
        if min(a["triangles"], b["triangles"]) < rules["pair_triangle_ratio"] * max(a["triangles"], b["triangles"]):
            raise ValueError(f"The two {label} pieces differ too much in size to be a pair")
        mapping[f"{label}_R"], mapping[f"{label}_L"] = a, b
    selectors = {name: [{"object": component["object"], "component": component["index"]}] for name, component in mapping.items()}
    for fragment in components:
        if any(fragment is component for component in main):
            continue
        point = [centre(fragment, axis) for axis in range(3)]
        holders = [name for name, component in mapping.items()
                   if all(lo - 1e-6 <= x <= hi + 1e-6 for x, lo, hi in zip(point, component["bbox_min"], component["bbox_max"]))]
        if len(holders) != 1:
            raise ValueError(f"Fragment {fragment['object']}/{fragment['index']} ({fragment['triangles']} triangles) lies in "
                             f"{holders or 'no piece'}: assign it by hand")
        selectors[holders[0]].append({"object": fragment["object"], "component": fragment["index"]})
    order = [names["helmet"], names["suit"], f"{names['glove']}_L", f"{names['glove']}_R", f"{names['boot']}_L", f"{names['boot']}_R"]
    return {"version": 1, "input_sha256": inventory["input_sha256"], "dependency_hashes": inventory.get("dependency_hashes", {}),
            "note": "Named by position (preset assembled): figure standing, facing -Y, +X is its left. "
                    f"{len(components) - 6} fragment(s) joined to the piece that holds them.",
            "parts": [{"name": name, "selectors": selectors[name]} for name in order]}
