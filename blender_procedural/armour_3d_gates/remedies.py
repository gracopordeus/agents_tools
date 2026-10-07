"""What to change when a gate fails: patches to the slot profile or to the budget file, inside declared ranges.

A remedy never edits a file of the repository. It returns patches; the pipeline applies them to copies kept in
the run, reruns the steps they touch, and reports which patches made the gates pass, for a person to promote.
"""
from __future__ import annotations

import copy


def _length_fraction(gate: dict, limits: dict, slots: dict, budgets: dict) -> list[dict]:
    low, high = limits["remedy"]["allowed"]
    patches = []
    for row in gate["where"]:
        slot = slots[row["slot"]]
        off_cm = row.get("beyond_cm", 0.0) - row.get("short_cm", 0.0)
        move = (off_cm + (limits["remedy"]["margin_cm"] if off_cm > 0 else 0.0)) / row["bone_length_cm"]
        if row["rule"] == "fit_length":
            holder, path = slot["fit_length"], ["slots", row["slot"], "fit_length", "fraction"]
            if not holder.get("always"):
                patches.append({"file": "slots", "path": ["slots", row["slot"], "fit_length", "always"], "value": True,
                                "why": f"{row['piece']}: the length rule only acts on plate with always"})
        else:
            part = row["rule"].split(" ", 1)[1]
            index = next(i for i, item in enumerate(slot["parts"]) if item["name"] == part)
            holder = slot["parts"][index]["proportion"]["lengthen"]
            path = ["slots", row["slot"], "parts", index, "proportion", "lengthen", "fraction"]
        # from the fraction the rule used: its own number, or the one it read from the asset
        value = round(min(high, max(low, row["told_%"] / 100 - move)), 4)
        patches.append({"file": "slots", "path": path, "value": value,
                        "why": f"{row['piece']} {row['rule']}: rim {off_cm:+.1f} cm off where it was told",
                        "at_range_end": value in (low, high)})
    return patches


def _length_design(gate: dict, limits: dict, slots: dict, budgets: dict) -> list[dict]:
    patches = []
    for row in gate["where"]:
        if row["rule"] == "fit_length":
            path = ["slots", row["slot"], "fit_length", "fraction"]
        else:
            index = next(i for i, item in enumerate(slots[row["slot"]]["parts"]) if item["name"] == row["rule"].split(" ", 1)[1])
            path = ["slots", row["slot"], "parts", index, "proportion", "lengthen", "fraction"]
        patches.append({"file": "slots", "path": path, "value": "design",
                        "why": f"{row['piece']} {row['rule']}: told {row['told_%']}%, the asset draws it at {row['design_%']}%"})
    return patches


def _relative_to(gate: dict, limits: dict, slots: dict, budgets: dict) -> list[dict]:
    return [{"file": "slots", "path": ["slots", row["slot"], "proportion", "relative_to"],
             "value": {"slot": limits["reference_slot"], "width": round(row["width_design_%"] / 100, 4),
                       "height": round(row["height_design_%"] / 100, 4)},
             "why": f"{row['piece']}: {row['fitted_over_design']:.2f} of its designed size against the {limits['reference_slot']}"}
            for row in gate["where"]]


def _collar_gap(gate: dict, limits: dict, slots: dict, budgets: dict) -> list[dict]:
    low, high = limits["remedy"]["allowed"]
    patches = []
    for row in gate["where"]:
        rule = slots[row["slot"]]["collar"]
        value = round(min(high, max(low, rule.get("gap_m", low) + (limits["min"] - row["gap_cm"]) / 100 + 0.003)), 4)
        patches.append({"file": "slots", "path": ["slots", row["slot"], "collar", "gap_m"], "value": value,
                        "why": f"{row['piece']}: the neck is {-row['gap_cm']:.1f} cm through the collar", "at_range_end": value == high})
    return patches


def _centre_rounds(gate: dict, limits: dict, slots: dict, budgets: dict) -> list[dict]:
    low, high = limits["remedy"]["allowed"]
    patches = []
    for row in gate["where"]:
        index = next(i for i, item in enumerate(slots[row["slot"]]["parts"]) if item["name"] == row["part"])
        current = slots[row["slot"]]["parts"][index]["centre"]
        rounds = min(high, (current.get("rounds", low) if isinstance(current, dict) else low) * 2)
        patches.append({"file": "slots", "path": ["slots", row["slot"], "parts", index, "centre"],
                        "value": {**(current if isinstance(current, dict) else {}), "rounds": rounds},
                        "why": f"{row['piece']} {row['part']}: mouth {row['off_centre_cm']} cm off the limb", "at_range_end": rounds == high})
    return patches


def _symmetrize(gate: dict, limits: dict, slots: dict, budgets: dict) -> list[dict]:
    rules = copy.deepcopy(budgets.get("symmetrize", []))
    names = [row["piece"] for row in gate["where"] if not any(row["piece"] in rule["pieces"] for rule in rules)]
    if not names:
        return []
    if rules:
        rules[0]["pieces"] = rules[0]["pieces"] + names
    else:
        rules = [{"pieces": names, "keep": "R"}]
    return [{"file": "budgets", "path": ["symmetrize"], "value": rules, "why": f"{names}: not mirrored from one half"}]


KINDS = {"length_fraction": _length_fraction, "length_design": _length_design, "relative_to": _relative_to, "collar_gap": _collar_gap,
         "centre_rounds": _centre_rounds, "symmetrize": _symmetrize}


def patches(results: list[dict], limits: dict, profile: dict, budgets: dict) -> list[dict]:
    """Patches for every failing gate that has a remedy; a gate without one is for a person.

    ``profile`` is the slot profile as it is on disk, ``budgets`` the budget file.
    """
    out, slots = [], profile["slots"]
    for gate in results:
        rule = limits["gates"].get(gate["gate"], {})
        if gate["pass"] or "remedy" not in rule:
            continue
        for patch in KINDS[rule["remedy"]["kind"]](gate, {**rule, "reference_slot": limits["reference_slot"]}, slots, budgets):
            out.append({**patch, "gate": gate["gate"]})
    # one value per place: the last failing gate that names it wins
    unique = {}
    for patch in out:
        unique[(patch["file"], tuple(patch["path"]))] = patch
    return list(unique.values())


def apply(document: dict, patch_list: list[dict], file: str) -> dict:
    """A copy of ``document`` with the patches for ``file`` written in."""
    out = copy.deepcopy(document)
    for patch in patch_list:
        if patch["file"] != file:
            continue
        holder = out
        for key in patch["path"][:-1]:
            holder = holder[key]
        holder[patch["path"][-1]] = patch["value"]
    return out
