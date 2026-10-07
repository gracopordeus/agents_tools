"""CLI usable with system Python; launches an isolated Blender process."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

PACKAGE = Path(__file__).resolve().parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dependencies(source: Path) -> dict:
    """Bind external glTF buffers/images to the inspected plan as well."""
    if source.suffix.lower() != ".gltf":
        return {}
    document = json.loads(source.read_text(encoding="utf-8"))
    result = {}
    for resource in document.get("buffers", []) + document.get("images", []):
        uri = resource.get("uri", "")
        if not uri or uri.startswith("data:"):
            continue
        parsed = urlsplit(uri)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("glTF external resources must be local file paths")
        path = (source.parent / unquote(parsed.path)).resolve()
        if not path.is_file():
            raise ValueError(f"Missing glTF resource: {uri}")
        result[uri] = {"path": str(path), "sha256": sha256(path)}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or split static armour without decimation.")
    parser.add_argument("command", choices=("inspect", "split"))
    parser.add_argument("--input", required=True, type=Path, help="GLB, glTF or Blender file")
    parser.add_argument("--out", required=True, type=Path, help="New, empty output directory")
    parser.add_argument("--scene", help="Required for Blender files with multiple scenes")
    parser.add_argument("--objects", nargs="+", help="Exact mesh names; otherwise all meshes in scene")
    selectors = parser.add_mutually_exclusive_group()
    selectors.add_argument("--plan", type=Path, help="Explicit JSON assignment created after inspection")
    selectors.add_argument("--preset", choices=("medieval_plate",))
    parser.add_argument("--blender", default="blender")
    parser.add_argument("--normal-policy", choices=("strict", "backup"), default="strict",
                        help="strict rejects shading drift; backup explicitly delivers a measured limitation with lossless source vectors")
    args = parser.parse_args(argv)
    try:
        source, out = args.input.resolve(), args.out.resolve()
        if not source.is_file() or source.suffix.lower() not in {".blend", ".glb", ".gltf"}:
            raise ValueError("--input must be an existing .blend, .glb or .gltf file")
        if out == source or source.is_relative_to(out) or out.is_relative_to(PACKAGE):
            raise ValueError("Output overlaps the input or installed tool")
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise ValueError("Output must be a new or empty directory; existing runs are never overwritten")
        if args.command == "split" and not (args.plan or args.preset):
            raise ValueError("Split requires --plan or --preset; anatomical names are never guessed")
        if args.command == "inspect" and (args.plan or args.preset):
            raise ValueError("Inspection does not accept a split plan")
        plan = None
        if args.plan:
            plan_path = args.plan.resolve()
            if not plan_path.is_file() or plan_path.is_relative_to(out):
                raise ValueError("Plan must be an existing file outside the output")
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        executable = shutil.which(args.blender)
        if executable is None:
            raise ValueError(f"Blender not found: {args.blender}")
        request = {"command": args.command, "input": str(source), "input_sha256": sha256(source),
                   "dependencies": dependencies(source),
                   "out": str(out), "scene": args.scene, "objects": args.objects,
                   "plan": plan, "preset": args.preset, "normal_policy": args.normal_policy}
        out.mkdir(parents=True, exist_ok=True)
        (out / "request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
        command = [executable, "--background", "--factory-startup", "--threads", "4",
                   "--python-exit-code", "2", "--python", str(PACKAGE / "blender_entry.py"),
                   "--", "--request", str(out / "request.json")]
        with (out / "blender.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
        if result.returncode:
            failure = out / "failure.json"
            detail = json.loads(failure.read_text()).get("error") if failure.exists() else "See blender.log"
            print(f"FAIL: {detail} ({out})", file=sys.stderr)
            return result.returncode
        report_path = out / ("inventory.json" if args.command == "inspect" else "report.json")
        report = json.loads(report_path.read_text())
        status = "PASS" if args.command == "inspect" else report["status"]
        print(f"{status}: {report_path}")
        for warning in report.get("warnings", []):
            print(f"WARNING: {warning}")
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
