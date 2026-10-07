"""Stage gates of a pipeline run: every stage gets a verdict, every failing gate says where and what to change.

    python3 -m armour_3d_gates --run outputs/armour_3d_pipeline/RUN [--limits armour_3d_gates/gates.plate.json]

Reads the reports of the three steps and measures the fitted armour with ``armour_3d_diagnose``; nothing in
the run is changed. The result goes to ``<run>/4_gates/gates.json``. Exit code 0 when every gate passes.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from armour_3d_diagnose import run as diagnose  # noqa: E402

STAGES = {1: "separação", 2: "pares", 3: "metades espelhadas", 4: "tampas", 5: "orçamento", 6: "encaixe",
          7: "máscara, skin, poses e export"}
MEASURES = ("length", "proportion", "symmetry", "collar", "shape")
SPLIT_OK = ("PASS", "PASS_WITH_NORMAL_LIMITATION")


def side(slot: str) -> str | None:
    return slot[-1] if slot[-2:] in (".L", ".R") else None


def evaluate(measured: dict, limits: dict) -> list[dict]:
    """Every gate of ``limits`` against what was measured. Pure: no file, no Blender."""
    rules, results = limits["gates"], []
    fit, decimate, split = measured["fit"], measured["decimate"], measured.get("split")
    slot_of = {name: piece["slot"] for name, piece in fit["pieces"].items()}
    parts = {part["name"]: part for part in decimate["parts"]}
    tables = {}
    for table in measured["tables"]:
        tables.setdefault(table["check"], []).append(table)
    rows_of = lambda check: [{**row, "slot": slot_of.get(row.get("piece") or table.get("piece")), "piece": row.get("piece") or table.get("piece")}
                             for table in tables.get(check, []) for row in table["rows"]]

    def add(gate: str, value, limit, failing: list[dict], note: str | None = None) -> None:
        if gate in rules:
            results.append({"gate": gate, "stage": rules[gate]["stage"], "value": value, "limit": limit, "pass": not failing,
                            "where": failing, **({"note": note} if note else {})})

    add("split_delivered", split and split.get("status"), list(SPLIT_OK),
        [] if split and split.get("status") in SPLIT_OK else [{"status": split and split.get("status")}])

    # 2. the two of a pair are one piece and its mirror image
    pairs = {frozenset(pair["pieces"]): pair for pair in decimate.get("pairs", [])}
    twins = sorted({frozenset((name, other)) for name, slot in slot_of.items() for other, other_slot in slot_of.items()
                    if name != other and side(slot) and side(other_slot) and slot[:-1] == other_slot[:-1]}, key=sorted)
    failing = []
    for twin in twins:
        pair = pairs.get(twin)
        if pair is None or parts[pair["replaced_by_mirror"]].get("mirror_of") != pair["kept"]:
            failing.append({"pieces": sorted(twin), "problem": "not declared in mirror_pairs" if pair is None else "the other piece is not its mirror"})
    add("pairs_mirrored", {" / ".join(sorted(twin)): pairs[twin]["kept"] for twin in twins if twin in pairs}, "every left/right pair", failing)

    # 3. a piece on the middle of the body is one half and its mirror image
    centre = [name for name, slot in slot_of.items() if side(slot) is None]
    errors, failing, leaks = {}, [], []
    for name in centre:
        done = parts[name].get("symmetrized")
        done = done and (done.get("after_reduction") or done)
        errors[name] = None if not done else round(done["mirror_error_m"] * 1000, 4)
        if not done or errors[name] > rules.get("halves_mirrored_mm", {}).get("max", 0):
            failing.append({"piece": name, "slot": slot_of[name], "mirror_error_mm": errors[name]})
        if done and done["open_edges"] + done["non_manifold_edges"] > rules.get("halves_closed", {}).get("max", 0):
            leaks.append({"piece": name, "open_edges": done["open_edges"], "non_manifold_edges": done["non_manifold_edges"]})
    add("halves_mirrored_mm", errors, rules.get("halves_mirrored_mm", {}).get("max"), failing)
    add("halves_closed", {name: 0 for name in centre} if not leaks else leaks, rules.get("halves_closed", {}).get("max"), leaks)

    # 4. lids sunk to the declared share of the piece
    if "lid_depth_share" in rules:
        target, tolerance = rules["lid_depth_share"]["target"], rules["lid_depth_share"]["tolerance"]
        declared = {name for rule in decimate["config"].get("recess_lids", []) for name in rule["pieces"]}
        shares, failing = {}, []
        for name in sorted(declared & set(parts)):
            recess = parts[name].get("recess") or {}
            shares[name] = round(recess["depth_m"] / recess["piece_length_m"], 3) if recess.get("recessed") else None
            if shares[name] is None or abs(shares[name] - target) > tolerance:
                failing.append({"piece": name, "depth_share": shares[name], "reason": recess.get("reason")})
        add("lid_depth_share", shares, f"{target} ± {tolerance}", failing)

    add("budget", decimate["triangles_after"], "every piece inside its budget and its step-2 gates",
        [{"piece": name, "failing": [gate for gate, ok in part["gates"].items() if not ok]} for name, part in parts.items() if part["status"] != "PASS"])

    # 6. the fit
    hands = fit.get("handedness", {}).get("pieces", {})
    add("handedness", {name: item["detected"] for name, item in hands.items()}, "each gauntlet on its own hand",
        [{"piece": name, **item} for name, item in hands.items() if not item["matches"]])
    lengths = [row for row in rows_of("length") if row.get("applied", True)]       # a rule that did not act is only a reading
    if "length_as_designed_pct" in rules:
        off = lambda row: abs(row["told_%"] - row["design_%"])
        add("length_as_designed_pct", max((off(row) for row in lengths if "design_%" in row), default=0), rules["length_as_designed_pct"]["max"],
            [row for row in lengths if "design_%" in row and off(row) > rules["length_as_designed_pct"]["max"]],
            "where each rim was told to land against where the asset drew it (100 = the joint, 150 = half the bone after it)")
    for gate, key in (("length_beyond_cm", "beyond_cm"), ("length_short_cm", "short_cm")):
        if gate in rules:
            add(gate, max((row[key] for row in lengths), default=0.0), rules[gate]["max"], [row for row in lengths if row[key] > rules[gate]["max"]])
    if "mouth_off_centre_cm" in rules:
        mouths = [{"piece": name, "slot": slot_of[name], "part": part["name"], "centred": part["centred"],
                   "off_centre_cm": round(part["off_centre_after_m"] * 100, 2) if part.get("off_centre_after_m") is not None else None}
                  for name, piece in fit["pieces"].items() for part in piece.get("centred_parts") or []]
        limit = rules["mouth_off_centre_cm"]["max"]
        add("mouth_off_centre_cm", max((row["off_centre_cm"] or 0.0 for row in mouths), default=0.0), limit,
            [row for row in mouths if not row["centred"] or row["off_centre_cm"] is None or row["off_centre_cm"] > limit])
    collars = {}
    for row in rows_of("collar"):
        gaps = {key: value for key, value in row.items() if key.startswith("gap_") and value is not None}
        if gaps:
            entry = collars.setdefault(row["piece"], {"piece": row["piece"], "slot": row["slot"], "gap_cm": min(gaps.values()), "sides_differ_cm": 0.0})
            entry["gap_cm"] = min(entry["gap_cm"], *gaps.values())
            if "gap_left_cm" in gaps and "gap_right_cm" in gaps:
                entry["sides_differ_cm"] = round(max(entry["sides_differ_cm"], abs(gaps["gap_left_cm"] - gaps["gap_right_cm"])), 2)
    if "collar_gap_cm" in rules:
        add("collar_gap_cm", min((row["gap_cm"] for row in collars.values()), default=None), rules["collar_gap_cm"]["min"],
            [row for row in collars.values() if row["gap_cm"] < rules["collar_gap_cm"]["min"]])
    if "collar_sides_differ_cm" in rules:
        add("collar_sides_differ_cm", max((row["sides_differ_cm"] for row in collars.values()), default=None), rules["collar_sides_differ_cm"]["max"],
            [row for row in collars.values() if row["sides_differ_cm"] > rules["collar_sides_differ_cm"]["max"]])
    if "set_proportion" in rules:
        sized = [row for row in rows_of("proportion") if not row["sized_by_body"]]
        tolerance = rules["set_proportion"]["tolerance"]
        add("set_proportion", {row["piece"]: row["fitted_over_design"] for row in sized}, f"1 ± {tolerance}",
            [row for row in sized if abs(row["fitted_over_design"] - 1.0) > tolerance],
            "pieces that take their length from a bone (fit_length) are not held to the design")
    mirror = rows_of("symmetry")
    own = [row for row in mirror if row["against"] == "its own mirror image" and side(row["slot"] or "") is None]
    paired = [row for row in mirror if row not in own]
    for gate, rows, key in (("pair_symmetry_mm", paired, "max_mm"), ("self_symmetry_p95_mm", own, "p95_mm"), ("self_symmetry_max_mm", own, "max_mm")):
        if gate in rules:
            add(gate, max((row[key] for row in rows), default=0.0), rules[gate]["max"], [row for row in rows if row[key] > rules[gate]["max"]])
    if "shape_p95_pct" in rules:
        ceiling, failing, values = rules["shape_p95_pct"]["max"], [], {}
        for row in rows_of("shape"):
            kind = (row["slot"] or "").split(".")[0]
            if "p95_%" not in row or kind not in ceiling:
                failing.append({**row, "problem": row.get("note", f"no limit for slot kind {kind}")})
                continue
            values[row["piece"]] = row["p95_%"]
            if row["p95_%"] > ceiling[kind]:
                failing.append({**row, "limit": ceiling[kind]})
        add("shape_p95_pct", values, ceiling, failing, "distortion against the piece as it came; a tripwire against the approved run, it does not see every dent")

    # 7. the gates the fit computes itself
    add("fit_gates", {name: gate["value"] for name, gate in fit["gates"].items()}, {name: gate["limit"] for name, gate in fit["gates"].items()},
        [{"gate": name, "value": gate["value"], "limit": gate["limit"]} for name, gate in fit["gates"].items() if not gate["pass"]])

    return results


def stages(results: list[dict]) -> list[dict]:
    out = []
    for number, title in STAGES.items():
        gates = [gate for gate in results if gate["stage"] == number]
        if gates:
            out.append({"stage": number, "title": title, "status": "PASS" if all(gate["pass"] for gate in gates) else "FAIL",
                        "failing": [gate["gate"] for gate in gates if not gate["pass"]]})
    return out


def measure(run: Path, blender: str) -> dict:
    read = lambda name: json.loads((run / name).read_text(encoding="utf-8")) if (run / name).is_file() else None
    measured = {"split": read("1_split/report.json"), "decimate": read("2_decimate/report.json"), "fit": read("3_fit/report.json")}
    missing = [key for key in ("decimate", "fit") if measured[key] is None]
    if missing:
        raise SystemExit(f"Not a finished pipeline run, missing reports: {missing}")
    measured["tables"] = diagnose.geometry(run, list(MEASURES), [], {}, blender)
    return measured


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m armour_3d_gates", description="Stage gates of an armour pipeline run.")
    parser.add_argument("--run", required=True, type=Path, help="Pipeline run directory")
    parser.add_argument("--limits", type=Path, default=HERE / "gates.plate.json")
    parser.add_argument("--out", type=Path, help="Folder for gates.json (default <run>/4_gates; must not exist)")
    parser.add_argument("--blender", default="blender")
    args = parser.parse_args(argv)
    run = args.run.resolve()
    out = (args.out or run / "4_gates").resolve()
    blender = shutil.which(args.blender)
    if blender is None:
        raise SystemExit(f"Blender not found: {args.blender}")
    if out.exists():
        raise SystemExit(f"Output exists, nothing is overwritten: {out}")
    limits = json.loads(args.limits.read_text(encoding="utf-8"))
    measured = measure(run, blender)
    results = evaluate(measured, limits)
    report = {"run": run.name, "limits": str(args.limits.resolve()), "status": "PASS" if all(gate["pass"] for gate in results) else "FAIL",
              "stages": stages(results), "gates": results, "measures": measured["tables"]}
    out.mkdir(parents=True)
    (out / "gates.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    for stage in report["stages"]:
        print(f"[{stage['stage']}] {stage['title']}: {stage['status']}")
        for gate in results:
            if gate["stage"] == stage["stage"]:
                print(f"    {'ok  ' if gate['pass'] else 'FAIL'} {gate['gate']}: {json.dumps(gate['value'], ensure_ascii=False)} (limit {json.dumps(gate['limit'], ensure_ascii=False)})")
                for row in gate["where"] if not gate["pass"] else []:
                    print(f"         {json.dumps(row, ensure_ascii=False)[:230]}")
    print(f"{report['status']}: {out / 'gates.json'}")
    return 0 if report["status"] == "PASS" else 1
