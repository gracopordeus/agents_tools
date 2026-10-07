"""Chain armour_3d_split, armour_3d_decimate and armour_3d_fit from one asset file.

    python3 -m armour_3d_pipeline --asset armour_3d_pipeline/assets/medieval_boots.asset.json --out NEW_DIR

Every step runs as its own process with its own folder and stops the chain when it does not deliver.
No step is re-implemented here: this module only validates the asset file, passes paths along and
records what happened in ``pipeline.json``.
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
STEPS = ("split", "decimate", "fit")
STYLES = ("plate", "leather")
ASSET_FIELDS = {"name", "source", "split", "decimate", "fit"}
STEP_FIELDS = {"split": {"plan", "normal_policy"}, "decimate": {"budgets"},
               "fit": {"pieces", "body", "body_profile", "slot_profile", "style", "config"}}


class AssetError(ValueError):
    pass


def load_asset(path: Path) -> dict:
    """Read and validate an asset file; every path in the result is absolute."""
    try:
        asset = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AssetError(f"Cannot read asset file: {error}") from error
    if not isinstance(asset, dict) or set(asset) != ASSET_FIELDS:
        raise AssetError(f"Asset file needs exactly the fields {sorted(ASSET_FIELDS)}")
    for step in STEPS:
        if not isinstance(asset[step], dict):
            raise AssetError(f"{step} must be an object")
        unknown = set(asset[step]) - STEP_FIELDS[step]
        if unknown:
            raise AssetError(f"Unknown fields in {step}: {sorted(unknown)}")
    resolve = lambda value: str((path.parent / value).resolve())
    try:
        out = {"name": asset["name"], "source": resolve(asset["source"]),
               "split": {"plan": resolve(asset["split"]["plan"]),
                         "normal_policy": asset["split"].get("normal_policy", "strict")},
               "decimate": {"budgets": resolve(asset["decimate"]["budgets"])},
               "fit": {"pieces": asset["fit"]["pieces"],
                       "body": {key: resolve(value) for key, value in asset["fit"]["body"].items()},
                       "body_profile": resolve(asset["fit"]["body_profile"]),
                       "slot_profile": resolve(asset["fit"]["slot_profile"]),
                       "style": asset["fit"].get("style", "plate"), "config": asset["fit"].get("config", {})}}
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
             out["fit"]["slot_profile"], *out["fit"]["body"].values()]
    missing = [name for name in files if not Path(name).is_file()]
    if missing:
        raise AssetError(f"Missing files: {missing}")
    # the three steps must talk about the same pieces
    plan = json.loads(Path(out["split"]["plan"]).read_text(encoding="utf-8"))
    budgets = json.loads(Path(out["decimate"]["budgets"]).read_text(encoding="utf-8"))
    parts = {part["name"] for part in plan.get("parts", [])}
    for label, names in (("decimate budgets", set(budgets.get("budgets", {}))), ("fit pieces", set(out["fit"]["pieces"]))):
        if names != parts:
            raise AssetError(f"{label} and the split plan name different pieces: "
                             f"only in plan {sorted(parts - names)}, only in {label} {sorted(names - parts)}")
    return out


def commands(asset: dict, out: Path, blender: str) -> dict[str, list[str]]:
    python = sys.executable
    return {
        "split": [python, "-m", "armour_3d_split", "split", "--input", asset["source"], "--plan", asset["split"]["plan"],
                  "--normal-policy", asset["split"]["normal_policy"], "--blender", blender, "--out", str(out / "1_split")],
        "decimate": [blender, "--background", "--factory-startup", "--python-exit-code", "2",
                     "--python", str(ROOT / "armour_3d_decimate" / "decimate.py"), "--",
                     "--input", str(out / "1_split" / "armour_split.blend"), "--budgets", asset["decimate"]["budgets"],
                     "--out", str(out / "2_decimate")],
        "fit": [python, "-m", "armour_3d_fit", "--job", str(out / "fit.job.json"), "--blender", blender,
                "--out", str(out / "3_fit")],
    }


FOLDERS = {"split": "1_split", "decimate": "2_decimate", "fit": "3_fit"}
REPORT_OK = {"split": ("PASS", "PASS_WITH_NORMAL_LIMITATION"), "decimate": ("PASS",), "fit": ("PASS",)}


def step_status(out: Path, step: str) -> str | None:
    report = out / FOLDERS[step] / "report.json"
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tripo GLB -> split -> decimate -> armour fitted on a rigged body.")
    parser.add_argument("--asset", required=True, type=Path, help="Asset file (source, split plan, budgets, fit job)")
    parser.add_argument("--out", required=True, type=Path, help="Run directory: new, or an earlier run with --resume")
    parser.add_argument("--style", choices=STYLES, help="Overrides fit.style of the asset file")
    parser.add_argument("--resume", action="store_true",
                        help="Keep the steps that already delivered in --out and run the rest")
    parser.add_argument("--until", choices=STEPS, default="fit", help="Last step to run")
    parser.add_argument("--blender", default="blender")
    args = parser.parse_args(argv)
    try:
        asset = load_asset(args.asset.resolve())
        if args.style:
            asset["fit"]["style"] = args.style
        out = args.out.resolve()
        blender = shutil.which(args.blender)
        if blender is None:
            raise AssetError(f"Blender not found: {args.blender}")
        if out.exists() and any(out.iterdir()) and not args.resume:
            raise AssetError("Output must be a new or empty directory (use --resume to continue an earlier run)")
        if out.is_relative_to(ROOT / "armour_3d_pipeline") or Path(asset["source"]).is_relative_to(out):
            raise AssetError("Output overlaps the tool or the source")
        out.mkdir(parents=True, exist_ok=True)
    except AssetError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    summary = {"name": asset["name"], "asset_file": str(args.asset.resolve()), "style": asset["fit"]["style"], "steps": []}
    code, upstream_changed = 0, False
    for step in STEPS[:STEPS.index(args.until) + 1]:
        folder = out / FOLDERS[step]
        status = step_status(out, step)
        if args.resume and not upstream_changed and status in REPORT_OK[step]:
            summary["steps"].append({"step": step, "status": status, "skipped": "delivered by an earlier run"})
            print(f"[{step}] {status} (kept from the earlier run)")
            continue
        upstream_changed = True
        if folder.exists():                                  # never overwritten: a leftover is set aside
            index = 1
            while (out / f"{FOLDERS[step]}_superseded_{index}").exists():
                index += 1
            folder.rename(out / f"{FOLDERS[step]}_superseded_{index}")
        if step == "fit":
            write_fit_job(asset, out)
        command = commands(asset, out, blender)[step]
        started = time.time()
        with (out / f"{step}.log").open("w", encoding="utf-8") as log:
            code = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
        status = step_status(out, step)
        entry = {"step": step, "status": status or "ERROR", "exit_code": code, "seconds": round(time.time() - started, 1),
                 "folder": FOLDERS[step], "log": f"{step}.log"}
        summary["steps"].append(entry)
        print(f"[{step}] {entry['status']} in {entry['seconds']} s ({folder})")
        if status not in REPORT_OK[step]:
            code = code or 2
            break
    delivered = [entry for entry in summary["steps"] if entry["status"] in REPORT_OK[entry["step"]]]
    summary["status"] = "PASS" if len(delivered) == len(summary["steps"]) else "FAIL"
    summary["stopped_at"] = None if summary["status"] == "PASS" else summary["steps"][-1]["step"]
    if (out / "3_fit" / "report.json").is_file() and summary["steps"][-1]["step"] == "fit":
        report = json.loads((out / "3_fit" / "report.json").read_text(encoding="utf-8"))
        summary["fit_gates"] = report["gates"]
        summary["deliverables"] = {"blend": "3_fit/armour_fitted.blend", "glb": "3_fit/armour_fitted.glb",
                                   "body_mask": "3_fit/body_mask.npz", "sheet": "3_fit/fit_sheet.png"}
        for name, gate in report["gates"].items():
            print(f"  {'ok  ' if gate['pass'] else 'FAIL'} {name}: {gate['value']} (limit {gate['limit']})")
    (out / "pipeline.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{summary['status']}: {out / 'pipeline.json'}")
    return 0 if summary["status"] == "PASS" else (code or 1)


if __name__ == "__main__":
    raise SystemExit(main())
