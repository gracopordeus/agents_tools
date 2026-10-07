"""Diagnose a pipeline run: where the armour is off the body, and by how much.

    python3 -m armour_3d_diagnose summary   --run outputs/armour_3d_pipeline/RUN [--against OTHER_RUN]
    python3 -m armour_3d_diagnose clearance --run RUN [--piece Boot_R] [--bands 8]
    python3 -m armour_3d_diagnose all       --run RUN --out RUN/diagnose.json

``summary`` only reads the reports. The other checks open ``<run>/3_fit/armour_fitted.blend`` in Blender and
measure the fitted geometry against the body of the fit job; nothing is changed or saved in the run.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GEOMETRY = ("length", "proportion", "shape", "clearance", "enclosed", "alignment", "reach", "gloves", "collar", "weights", "symmetry")
ABOUT = {
    "views": "close-up pictures of the joints (collar, elbows, wrists, knees, hips, shoulders) into --out, for the visual review",
    "summary": "gates, sizes, sub-parts, worst poses and hidden skin, from the reports of the run",
    "length": "every length rule of the slots against its bone: where the rim was told to land and where it is",
    "proportion": "size of each piece against the body armour, as designed and as fitted",
    "shape": "how much the fit bent each piece out of its own shape (dents, crumples)",
    "clearance": "room between the skin and the plate over it, by slice of each bone and side of the limb",
    "enclosed": "a piece against the body it hides whole (helmet on the head): room by height and side, centre, sizes",
    "alignment": "middle of each piece against the middle of the limb under it",
    "reach": "where each piece starts and ends along the bones it is on (short of a joint, or past it)",
    "gloves": "cuff against the forearm and every finger against the finger in it",
    "collar": "gap between the collar and the neck, by height and side",
    "weights": "which bones move each height of a piece",
    "symmetry": "how far the fitted armour is from its own mirror image",
    "all": "every geometry check",
}


def table(rows: list[dict]) -> str:
    if not rows:
        return "  (nothing to measure)"
    columns = list(dict.fromkeys(key for row in rows for key in row))
    cell = lambda value: "-" if value is None else str(value)
    widths = [max(len(column), *(len(cell(row.get(column))) for row in rows)) for column in columns]
    lines = ["  " + "  ".join(column.ljust(width) for column, width in zip(columns, widths))]
    lines += ["  " + "  ".join(cell(row.get(column)).ljust(width) for column, width in zip(columns, widths)) for row in rows]
    return "\n".join(lines)


def load(run: Path) -> dict:
    read = lambda name: json.loads((run / name).read_text(encoding="utf-8")) if (run / name).is_file() else None
    return {"run": run.name, "decimate": read("2_decimate/report.json"), "fit": read("3_fit/report.json")}


def summary(run: Path, against: Path | None) -> list[dict]:
    """The reports of a run as tables; with ``against``, the same numbers of another run beside them."""
    runs = [load(run)] + ([load(against)] if against else [])
    if runs[0]["fit"] is None:
        raise SystemExit(f"No fit report in {run}")
    tables = []
    gates = [{"gate": name, **{item["run"]: ("PASS " if item["fit"]["gates"][name]["pass"] else "FAIL ") + str(
        round(item["fit"]["gates"][name]["value"], 1) if isinstance(item["fit"]["gates"][name]["value"], float) else item["fit"]["gates"][name]["value"])
        for item in runs if item["fit"] and name in item["fit"]["gates"]}, "limit": gate["limit"]}
        for name, gate in runs[0]["fit"]["gates"].items()]
    tables.append({"check": "summary: gates", "rows": gates})
    fit, decimate = runs[0]["fit"], runs[0]["decimate"]
    if decimate:
        parts = {part["name"]: part for part in decimate["parts"]}
        rows = [{"piece": name, "triangles_before": part["before"]["triangles"], "triangles_after": part["after"]["triangles"],
                 "budget": part["budget"], "quad_share": round(part["after"]["quad_face_share"], 2),
                 "mirror_of": part.get("mirror_of"), "halves_mirror_error_mm": round(
                     (part["symmetrized"].get("after_reduction") or part["symmetrized"])["mirror_error_m"] * 1000, 3) if part.get("symmetrized") else None,
                 "lid_recessed_cm": round(part["recess"]["depth_m"] * 100, 1) if part.get("recess") and part["recess"].get("recessed") else None,
                 "failing": ", ".join(gate for gate, ok in part["gates"].items() if not ok) or None} for name, part in parts.items()]
        rows.append({"piece": "TOTAL", "triangles_before": decimate["triangles_before"], "triangles_after": decimate["triangles_after"]})
        tables.append({"check": "summary: step 2 (pairs, halves, lids, budget)", "rows": rows})
        if decimate.get("pairs"):
            tables.append({"check": "summary: pairs", "rows": [{"pair": " / ".join(pair["pieces"]), "kept": pair["kept"],
                                                              "decided_by": pair["decided_by"]} for pair in decimate["pairs"]]})
    sizes = []
    for name, piece in fit["pieces"].items():
        seat = piece["seat"]
        sizes.append({"piece": name, "slot": piece["slot"], "set_scale": round(piece["placement"]["scale"], 3),
                      "mirror_of": piece.get("mirrored_from"),
                      "seat_shift_cm": " ".join(f"{value * 100:+.1f}" for value in seat["translation_m"]), "seat_turn_deg": round(seat["rotation_deg"], 2),
                      "at_limit": ", ".join(key for key, value in seat["at_limit"].items() if value) or None,
                      "length_factor": round(piece["fit_length"]["factor"], 3) if piece.get("fit_length") else None})
    tables.append({"check": "summary: pieces", "rows": sizes, "note": "seat_shift is x y z; a piece on the middle of the body should have x = 0"})
    rows = []
    for name, piece in fit["pieces"].items():
        turns = {part["name"]: part for part in piece.get("parts", [])}
        centred = {part["name"]: part for part in piece.get("centred_parts") or []}
        for item in piece.get("proportion", []):
            turn, mouth = turns.get(item["name"]), centred.get(item["name"])
            rows.append({"piece": name, "part": item["name"], "sizing": item.get("mode"),
                         "factor": round(item["isotropic"]["factor"], 3) if "isotropic" in item else None,
                         "length_factor": round(item["lengthened"]["factor"], 3) if "lengthened" in item else None,
                         "offset_cm": "{:+.1f} .. {:+.1f}".format(item["mean_offset_m"]["min"] * 100, item["mean_offset_m"]["max"] * 100)
                         if "mean_offset_m" in item else None,
                         "moved_max_cm": round(item["vertex_shift_m"]["max"] * 100, 1) if "vertex_shift_m" in item else None,
                         "turn_deg": round(turn["rotation_deg"], 1) if turn else None, "copies": (turn or {}).get("mirrored_from") or item.get("mirrored_from"),
                         "mouth_off_centre_cm": "{:.1f} -> {:.2f}".format(mouth["off_centre_before_m"] * 100, mouth["off_centre_after_m"] * 100)
                         if mouth and mouth.get("centred") else None})
        if piece.get("collar") and piece["collar"].get("fitted"):
            rows.append({"piece": name, "part": "collar", "factor": round(piece["collar"].get("factor", piece["collar"].get("factor_at_rim")), 3),
                         "moved_max_cm": round(piece["collar"]["vertex_shift_max_m"] * 100, 1)})
    tables.append({"check": "summary: how each piece and part was sized", "rows": rows,
                   "note": "factor = one factor in every direction; offset = section-by-section fit, across the limb; turn = swing about the joint"})
    skin = fit["poke"]["area_cm2_by_bone"]
    tables.append({"check": "summary: skin hidden at rest for coming through the armour (cm2)",
                   "rows": [{"bone": bone, "cm2": round(area)} for bone, area in list(skin.items())[:10]],
                   "note": f"{fit['poke']['body_area_pct']:.1f}% of the body; a clean render does not show this skin, the mask hides it"})
    if fit.get("poses"):
        worst = sorted(fit["poses"]["rows"], key=lambda row: -row["poke_area_cm2"])[:6]
        tables.append({"check": "summary: worst poses", "rows": [
            {"clip": row["clip"], "at": row["fraction"], "cm2": round(row["poke_area_cm2"]),
             "incl_covered_cm2": round(row["poke_area_incl_covered_cm2"]),
             "under": ", ".join(f"{key} {round(value)}" for key, value in list(row.get("area_cm2_by_piece", row["area_cm2_by_bone"]).items())[:4])}
            for row in worst], "note": f"{fit['poses']['tested']} poses tested; 'under' is the piece below the skin and the bone that moves it"})
    return tables


def geometry(run: Path, checks: list[str], pieces: list[str], settings: dict, blender: str) -> list[dict]:
    blend, job, report = run / "3_fit" / "armour_fitted.blend", run / "fit.job.json", run / "3_fit" / "report.json"
    missing = [str(path) for path in (blend, job, report) if not path.is_file()]
    if missing:
        raise SystemExit(f"Not a finished pipeline run, missing: {missing}")
    with tempfile.TemporaryDirectory() as folder:
        request, result = Path(folder) / "request.json", Path(folder) / "result.json"
        request.write_text(json.dumps({"job": str(job), "report": str(report), "checks": checks, "pieces": pieces,
                                       "settings": settings, "result": str(result)}), encoding="utf-8")
        done = subprocess.run([blender, "--background", "--factory-startup", "--python-exit-code", "2", str(blend),
                               "--python", str(HERE / "checks.py"), "--", str(request)], capture_output=True, text=True, check=False)
        if done.returncode != 0 or not result.is_file():
            raise SystemExit("Blender failed:\n" + "\n".join((done.stdout + done.stderr).splitlines()[-25:]))
        return json.loads(result.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m armour_3d_diagnose", description="Measure a finished armour pipeline run.",
                                     epilog="\n".join(f"  {name:10s} {text}" for name, text in ABOUT.items()),
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("check", choices=list(ABOUT))
    parser.add_argument("--run", required=True, type=Path, help="Pipeline run directory")
    parser.add_argument("--against", type=Path, help="summary: another run to put beside this one")
    parser.add_argument("--piece", action="append", default=[], help="Only this piece (repeatable)")
    parser.add_argument("--bands", type=int, help="Slices along a bone or a piece (default 5)")
    parser.add_argument("--reach-m", type=float, help="clearance: how far from the skin a plate is looked for (default 0.2)")
    parser.add_argument("--out", type=Path, help="Also write the tables as JSON; for views, the folder of the pictures")
    parser.add_argument("--blender", default="blender")
    args = parser.parse_args(argv)
    run = args.run.resolve()
    if args.check == "summary":
        tables = summary(run, args.against.resolve() if args.against else None)
    else:
        blender = shutil.which(args.blender)
        if blender is None:
            raise SystemExit(f"Blender not found: {args.blender}")
        settings = {key: value for key, value in (("bands", args.bands), ("reach_m", args.reach_m)) if value is not None}
        if args.check == "views":
            if args.out is None or (args.out.exists() and any(args.out.iterdir())):
                raise SystemExit("views needs --out: a new or empty folder for the pictures")
            args.out.mkdir(parents=True, exist_ok=True)
            settings["views_out"], args.out = str(args.out.resolve()), None
        tables = geometry(run, list(GEOMETRY) if args.check == "all" else [args.check], args.piece, settings, blender)
    for item in tables:
        title = " / ".join(str(item[key]) for key in ("check", "piece", "bone") if key in item)
        print(f"\n== {title}")
        print(table(item["rows"]))
        if item.get("note"):
            print(f"  ({item['note']})")
    if args.out:
        args.out.write_text(json.dumps(tables, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
