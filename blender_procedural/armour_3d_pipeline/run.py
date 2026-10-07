"""Chain armour_3d_split, armour_3d_decimate, armour_3d_fit and armour_3d_gates from one asset file.

    python3 -m armour_3d_pipeline --asset armour_3d_pipeline/assets/medieval_knight.asset.json --out NEW_DIR

Every step runs as its own process with its own folder and stops the chain when it does not deliver.
No step is re-implemented here: this module only validates the asset file, passes paths along and
records what happened in ``pipeline.json``. The verdict of a run is the one of the stage gates
(``4_gates/gates.json``). With ``--autofix N`` the failing gates that have a remedy are answered with
patches to copies of the profiles kept in the run, and the steps they touch run again, up to N times.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # blender_procedural
sys.path.insert(0, str(ROOT))
from armour_3d_gates import remedies  # noqa: E402
from armour_3d_pipeline import game as game_step  # noqa: E402

STEPS = ("split", "decimate", "fit", "gates")
STYLES = ("plate", "leather")
ASSET_FIELDS = {"name", "source", "split", "decimate", "fit"}
OPTIONAL_FIELDS = {"gates", "game"}
STEP_FIELDS = {"split": {"plan", "normal_policy"}, "decimate": {"budgets"},
               "fit": {"pieces", "body", "body_profile", "slot_profile", "style", "config"},
               "gates": {"limits"}, "game": {"project", "pieces_dir", "audit"}}
DEFAULT_LIMITS = ROOT / "armour_3d_gates" / "gates.plate.json"
AUTO_PLAN = "auto"                                   # split.plan: name the pieces of an assembled set by where they stand
AUTO_PIECES = ("Helmet", "Suit", "Glove_L", "Glove_R", "Boot_L", "Boot_R")


class AssetError(ValueError):
    pass


def load_asset(path: Path) -> dict:
    """Read and validate an asset file; every path in the result is absolute."""
    try:
        asset = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AssetError(f"Cannot read asset file: {error}") from error
    if not isinstance(asset, dict) or not ASSET_FIELDS <= set(asset) or set(asset) - ASSET_FIELDS - OPTIONAL_FIELDS:
        raise AssetError(f"Asset file needs the fields {sorted(ASSET_FIELDS)} and, optionally, {sorted(OPTIONAL_FIELDS)}")
    for step in [*ASSET_FIELDS - {"name", "source"}, *(OPTIONAL_FIELDS & set(asset))]:
        if not isinstance(asset[step], dict):
            raise AssetError(f"{step} must be an object")
        unknown = set(asset[step]) - STEP_FIELDS[step]
        if unknown:
            raise AssetError(f"Unknown fields in {step}: {sorted(unknown)}")
    resolve = lambda value: str((path.parent / value).resolve())
    try:
        out = {"name": asset["name"], "source": resolve(asset["source"]),
               "split": {"plan": AUTO_PLAN if asset["split"]["plan"] == AUTO_PLAN else resolve(asset["split"]["plan"]),
                         "normal_policy": asset["split"].get("normal_policy", "strict")},
               "decimate": {"budgets": resolve(asset["decimate"]["budgets"])},
               "fit": {"pieces": asset["fit"]["pieces"],
                       "body": {key: resolve(value) for key, value in asset["fit"]["body"].items()},
                       "body_profile": resolve(asset["fit"]["body_profile"]),
                       "slot_profile": resolve(asset["fit"]["slot_profile"]),
                       "style": asset["fit"].get("style", "plate"), "config": asset["fit"].get("config", {})},
               "gates": {"limits": resolve(asset["gates"]["limits"]) if asset.get("gates", {}).get("limits") else str(DEFAULT_LIMITS)},
               "game": None if "game" not in asset else {"project": resolve(asset["game"]["project"]),
                                                         "pieces_dir": asset["game"]["pieces_dir"], "audit": asset["game"]["audit"]}}
    except (KeyError, TypeError, AttributeError) as error:
        raise AssetError(f"Missing or malformed field: {error}") from error
    if out["split"]["normal_policy"] not in ("strict", "backup"):
        raise AssetError("split.normal_policy must be strict or backup")
    if out["fit"]["style"] not in STYLES:
        raise AssetError(f"fit.style must be one of {list(STYLES)}")
    if not isinstance(out["fit"]["pieces"], dict) or not out["fit"]["pieces"]:
        raise AssetError("fit.pieces must map object names to slots")
    if set(out["fit"]["body"]) - {"rig", "poses"} or "rig" not in out["fit"]["body"]:
        raise AssetError("fit.body takes rig and, optionally, poses")
    files = [out["source"], out["split"]["plan"], out["decimate"]["budgets"], out["fit"]["body_profile"],
             out["fit"]["slot_profile"], *out["fit"]["body"].values(), out["gates"]["limits"]]
    missing = [name for name in files if name != AUTO_PLAN and not Path(name).is_file()]
    if missing:
        raise AssetError(f"Missing files: {missing}")
    # the three steps must talk about the same pieces
    budgets = json.loads(Path(out["decimate"]["budgets"]).read_text(encoding="utf-8"))
    if out["split"]["plan"] == AUTO_PLAN:
        parts = set(AUTO_PIECES)                             # the names the split gives pieces it reads by position
    else:
        parts = {part["name"] for part in json.loads(Path(out["split"]["plan"]).read_text(encoding="utf-8")).get("parts", [])}
    for label, names in (("decimate budgets", set(budgets.get("budgets", {}))), ("fit pieces", set(out["fit"]["pieces"]))):
        if names != parts:
            raise AssetError(f"{label} and the split plan name different pieces: "
                             f"only in plan {sorted(parts - names)}, only in {label} {sorted(names - parts)}")
    return out


def commands(asset: dict, out: Path, blender: str) -> dict[str, list[str]]:
    python = sys.executable
    return {
        "split": [python, "-m", "armour_3d_split", "split", "--input", asset["source"],
                  *(["--preset", "assembled"] if asset["split"]["plan"] == AUTO_PLAN else ["--plan", asset["split"]["plan"]]),
                  "--normal-policy", asset["split"]["normal_policy"], "--blender", blender, "--out", str(out / "1_split")],
        "decimate": [blender, "--background", "--factory-startup", "--python-exit-code", "2",
                     "--python", str(ROOT / "armour_3d_decimate" / "decimate.py"), "--",
                     "--input", str(out / "1_split" / "armour_split.blend"), "--budgets", asset["decimate"]["budgets"],
                     "--out", str(out / "2_decimate")],
        "fit": [python, "-m", "armour_3d_fit", "--job", str(out / "fit.job.json"), "--blender", blender,
                "--out", str(out / "3_fit")],
        "gates": [python, "-m", "armour_3d_gates", "--run", str(out), "--limits", asset["gates"]["limits"], "--blender", blender,
                  "--out", str(out / "4_gates")],
    }


FOLDERS = {"split": "1_split", "decimate": "2_decimate", "fit": "3_fit", "gates": "4_gates"}
REPORTS = {"gates": "gates.json"}
# a fit that fails its own gates still delivers an armour to measure: the verdict is the one of the stage gates
REPORT_OK = {"split": ("PASS", "PASS_WITH_NORMAL_LIMITATION"), "decimate": ("PASS",), "fit": ("PASS", "FAIL"), "gates": ("PASS",)}


def step_status(out: Path, step: str) -> str | None:
    report = out / FOLDERS[step] / REPORTS.get(step, "report.json")
    if not report.is_file():
        return None
    try:
        return json.loads(report.read_text(encoding="utf-8")).get("status")
    except json.JSONDecodeError:
        return None


def write_fit_job(asset: dict, out: Path) -> None:
    fit = asset["fit"]
    job = {"name": asset["name"], "armour": str(out / "2_decimate" / "armour_decimated.blend"), "pieces": fit["pieces"],
           "body": fit["body"], "body_profile": fit["body_profile"], "slot_profile": fit["slot_profile"],
           "config": {**fit["config"], "style": fit["style"]}}
    (out / "fit.job.json").write_text(json.dumps(job, indent=2), encoding="utf-8")


def set_aside(out: Path, folder: str) -> None:
    """A leftover of an earlier attempt is never overwritten: it is renamed."""
    if (out / folder).exists():
        index = 1
        while (out / f"{folder}_superseded_{index}").exists():
            index += 1
        (out / folder).rename(out / f"{folder}_superseded_{index}")


def run_chain(asset: dict, out: Path, blender: str, steps: tuple[str, ...], keep: bool, label: str = "") -> tuple[list[dict], int]:
    """Run ``steps`` in order; with ``keep`` the leading ones an earlier run delivered are not repeated."""
    entries, code, upstream_changed = [], 0, False
    for step in steps:
        folder = out / FOLDERS[step]
        status = step_status(out, step)
        if keep and not upstream_changed and status in REPORT_OK[step] and step != "gates":
            entries.append({"step": step, "status": status, "skipped": "delivered by an earlier run"})
            print(f"[{step}] {status} (kept from the earlier run)")
            continue
        upstream_changed = True
        set_aside(out, FOLDERS[step])
        if step == "fit":
            write_fit_job(asset, out)
        started = time.time()
        with (out / f"{step}{label}.log").open("w", encoding="utf-8") as log:
            code = subprocess.run(commands(asset, out, blender)[step], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
        status = step_status(out, step)
        entry = {"step": step, "status": status or "ERROR", "exit_code": code, "seconds": round(time.time() - started, 1),
                 "folder": FOLDERS[step], "log": f"{step}{label}.log"}
        entries.append(entry)
        print(f"[{step}] {entry['status']} in {entry['seconds']} s ({folder})")
        if status not in REPORT_OK[step] and not (step == "gates" and status == "FAIL"):
            return entries, code or 2
    return entries, 0


def read_gates(out: Path) -> dict | None:
    path = out / "4_gates" / "gates.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def print_gates(gates: dict) -> None:
    for stage in gates["stages"]:
        print(f"  [{stage['stage']}] {stage['title']}: {stage['status']}")
        for gate in gates["gates"]:
            if gate["stage"] == stage["stage"] and not gate["pass"]:
                print(f"      FAIL {gate['gate']}: {json.dumps(gate['value'], ensure_ascii=False)[:160]} (limit {json.dumps(gate['limit'], ensure_ascii=False)[:120]})")


def autofix(asset: dict, out: Path, blender: str, attempts: int) -> list[dict]:
    """Answer failing gates with their remedies, on copies of the profiles kept in the run."""
    log, tried = [], set()
    limits = json.loads(Path(asset["gates"]["limits"]).read_text(encoding="utf-8"))
    for attempt in range(1, attempts + 1):
        gates = read_gates(out)
        if gates is None or gates["status"] == "PASS":
            break
        slots = json.loads(Path(asset["fit"]["slot_profile"]).read_text(encoding="utf-8"))
        budgets = json.loads(Path(asset["decimate"]["budgets"]).read_text(encoding="utf-8"))
        patch_list = remedies.patches(gates["gates"], limits, slots, budgets)
        key = json.dumps([[patch["file"], patch["path"], patch["value"]] for patch in patch_list], sort_keys=True)
        failing = [gate["gate"] for gate in gates["gates"] if not gate["pass"]]
        if not patch_list or key in tried:
            log.append({"attempt": attempt, "failing": failing, "stopped": "no remedy left for the failing gates" if not patch_list
                        else "the remedies repeat: the ranges they are allowed are used up"})
            break
        tried.add(key)
        folder = out / "overrides" / f"attempt_{attempt}"
        folder.mkdir(parents=True)
        (folder / "patches.json").write_text(json.dumps(patch_list, indent=2, ensure_ascii=False), encoding="utf-8")
        (folder / "slots.json").write_text(json.dumps(remedies.apply(slots, patch_list, "slots"), indent=2, ensure_ascii=False), encoding="utf-8")
        (folder / "budgets.json").write_text(json.dumps(remedies.apply(budgets, patch_list, "budgets"), indent=2, ensure_ascii=False), encoding="utf-8")
        asset["fit"]["slot_profile"], asset["decimate"]["budgets"] = str(folder / "slots.json"), str(folder / "budgets.json")
        first = "decimate" if any(patch["file"] == "budgets" for patch in patch_list) else "fit"
        print(f"[autofix {attempt}] {failing} -> " + "; ".join(f"{'.'.join(map(str, patch['path'][1:]))} = {json.dumps(patch['value'])[:60]}" for patch in patch_list))
        entries, _ = run_chain(asset, out, blender, STEPS[STEPS.index(first):], keep=False, label=f".autofix_{attempt}")
        after = read_gates(out)
        log.append({"attempt": attempt, "failing_before": failing, "patches": patch_list, "reran_from": first, "steps": entries,
                    "status_after": after and after["status"], "failing_after": after and [gate["gate"] for gate in after["gates"] if not gate["pass"]],
                    "profiles": f"overrides/attempt_{attempt}"})
    return log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tripo GLB -> split -> decimate -> armour fitted on a rigged body -> stage gates.")
    parser.add_argument("--asset", required=True, type=Path, help="Asset file (source, split plan, budgets, fit job)")
    parser.add_argument("--out", required=True, type=Path, help="Run directory: new, or an earlier run with --resume")
    parser.add_argument("--style", choices=STYLES, help="Overrides fit.style of the asset file")
    parser.add_argument("--resume", action="store_true",
                        help="Keep the steps that already delivered in --out and run the rest")
    parser.add_argument("--until", choices=STEPS, default="gates", help="Last step to run")
    parser.add_argument("--autofix", type=int, default=0, metavar="N",
                        help="Answer failing gates with their remedies and run again, up to N times")
    parser.add_argument("--game", action="store_true",
                        help="When every gate passes, put the pieces in the game project of the asset file and run its audit")
    parser.add_argument("--blender", default="blender")
    parser.add_argument("--godot", default="godot")
    args = parser.parse_args(argv)
    try:
        asset = load_asset(args.asset.resolve())
        if args.style:
            asset["fit"]["style"] = args.style
        out = args.out.resolve()
        blender = shutil.which(args.blender)
        if blender is None:
            raise AssetError(f"Blender not found: {args.blender}")
        if args.game and asset["game"] is None:
            raise AssetError("--game needs a game block in the asset file (project, pieces_dir, audit)")
        if args.game and shutil.which(args.godot) is None:
            raise AssetError(f"Godot not found: {args.godot}")
        if out.exists() and any(out.iterdir()) and not args.resume:
            raise AssetError("Output must be a new or empty directory (use --resume to continue an earlier run)")
        if out.is_relative_to(ROOT / "armour_3d_pipeline") or Path(asset["source"]).is_relative_to(out):
            raise AssetError("Output overlaps the tool or the source")
        out.mkdir(parents=True, exist_ok=True)
    except AssetError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    summary = {"name": asset["name"], "asset_file": str(args.asset.resolve()), "style": asset["fit"]["style"], "steps": []}
    summary["steps"], code = run_chain(asset, out, blender, STEPS[:STEPS.index(args.until) + 1], keep=args.resume)
    if code == 0 and args.until == "gates" and args.autofix:
        summary["autofix"] = autofix(asset, out, blender, args.autofix)
    gates = read_gates(out) if args.until == "gates" else None
    delivered = [entry for entry in summary["steps"] if entry["status"] in REPORT_OK[entry["step"]]]
    summary["status"] = "PASS" if code == 0 and len(delivered) == len(summary["steps"]) else "FAIL"
    if gates is not None:
        summary["status"] = gates["status"] if code == 0 else "FAIL"
        summary["stages"] = gates["stages"]
        print_gates(gates)
    summary["stopped_at"] = None if summary["status"] == "PASS" else (
        "gates" if gates is not None and code == 0 else summary["steps"][-1]["step"])
    if (out / "3_fit" / "report.json").is_file():
        summary["fit_gates"] = json.loads((out / "3_fit" / "report.json").read_text(encoding="utf-8"))["gates"]
        summary["deliverables"] = {"blend": "3_fit/armour_fitted.blend", "glb": "3_fit/armour_fitted.glb", "pieces": "3_fit/pieces",
                                   "body_mask": "3_fit/body_mask.npz", "sheet": "3_fit/fit_sheet.png", "gates": "4_gates/gates.json"}
    if summary.get("autofix") and summary["status"] == "PASS" and any(entry.get("patches") for entry in summary["autofix"]):
        last = [entry for entry in summary["autofix"] if entry.get("patches")][-1]
        summary["promote"] = {"profiles": last["profiles"], "note": "the gates pass with these copies of the profiles; the repository "
                              "profiles were not changed. Copy the patches into them to make the fix permanent"}
        print(f"  the gates pass with the patched profiles in {last['profiles']} (repository profiles unchanged)")
    if args.game and summary["status"] == "PASS":
        set_aside(out, "5_game")
        summary["game"] = game_step.install(out, asset["game"], shutil.which(args.godot))
        print(f"[game] {summary['game']['status']}: {summary['game']['audit']}")
        if summary["game"]["status"] != "PASS":
            summary["status"], summary["stopped_at"] = "FAIL", "game"
    (out / "pipeline.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{summary['status']}: {out / 'pipeline.json'}")
    return 0 if summary["status"] == "PASS" else (code or 1)


if __name__ == "__main__":
    raise SystemExit(main())
