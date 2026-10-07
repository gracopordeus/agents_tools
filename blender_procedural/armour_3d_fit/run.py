"""CLI for the system Python: validates the job and launches an isolated Blender process."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fit separated armour parts onto a rigged body.")
    parser.add_argument("--job", required=True, type=Path, help="Job JSON (armour blend, pieces -> slots, body rig)")
    parser.add_argument("--out", required=True, type=Path, help="New, empty output directory")
    parser.add_argument("--style", choices=("plate", "leather"),
                        help="How pieces take the body's proportions; overrides config.style of the job (default: plate)")
    parser.add_argument("--blender", default="blender")
    args = parser.parse_args(argv)
    try:
        job_path, out = args.job.resolve(), args.out.resolve()
        job = json.loads(job_path.read_text(encoding="utf-8"))
        unknown = set(job) - {"name", "armour", "pieces", "body", "body_profile", "slot_profile", "config"}
        if unknown:
            raise ValueError(f"Unknown job fields: {sorted(unknown)}")
        if args.style:
            job.setdefault("config", {})["style"] = args.style
        resolve = lambda value: (job_path.parent / value).resolve()
        inputs = {"armour": resolve(job["armour"]), "rig": resolve(job["body"]["rig"])}
        if job["body"].get("poses"):
            inputs["poses"] = resolve(job["body"]["poses"])
        profiles = {"body_profile": resolve(job["body_profile"]), "slot_profile": resolve(job["slot_profile"])}
        for name, path in {**inputs, **profiles}.items():
            if not path.is_file():
                raise ValueError(f"Missing {name}: {path}")
        if not isinstance(job.get("pieces"), dict) or not job["pieces"]:
            raise ValueError("Job requires pieces: {object name: slot}")
        slot_profile = json.loads(profiles["slot_profile"].read_text(encoding="utf-8"))
        missing = sorted(set(job["pieces"].values()) - set(slot_profile["slots"]))
        if missing or len(set(job["pieces"].values())) != len(job["pieces"]):
            raise ValueError(f"Unknown or repeated slots: {missing or sorted(job['pieces'].values())}")
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise ValueError("Output must be a new or empty directory; existing runs are never overwritten")
        if out.is_relative_to(PACKAGE) or any(path.is_relative_to(out) for path in inputs.values()):
            raise ValueError("Output overlaps the inputs or the installed tool")
        executable = shutil.which(args.blender)
        if executable is None:
            raise ValueError(f"Blender not found: {args.blender}")
        request = {"out": str(out), "job": job, "inputs": {name: str(path) for name, path in inputs.items()},
                   "input_sha256": {name: sha256(path) for name, path in inputs.items()},
                   "body_profile": json.loads(profiles["body_profile"].read_text(encoding="utf-8")),
                   "slot_profile": slot_profile}
        out.mkdir(parents=True, exist_ok=True)
        (out / "request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
        command = [executable, "--background", "--factory-startup", "--python-exit-code", "2",
                   "--python", str(PACKAGE / "blender_entry.py"), "--", "--request", str(out / "request.json")]
        with (out / "blender.log").open("w", encoding="utf-8") as log:
            code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False).returncode
        report = out / "report.json"
        if not report.is_file():
            failure = out / "failure.json"
            detail = json.loads(failure.read_text())["error"] if failure.is_file() else "see blender.log"
            print(f"ERROR: {detail} ({out})", file=sys.stderr)
            return 2
        data = json.loads(report.read_text(encoding="utf-8"))
        print(f"{data['status']} ({data['style']}): {report}")
        for name, gate in data["gates"].items():
            print(f"  {'ok  ' if gate['pass'] else 'FAIL'} {name}: {gate['value']} (limit {gate['limit']})")
        return code
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
