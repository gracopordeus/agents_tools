"""Focused end-to-end tests; accepts a new test output directory."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Test directory must be empty")
    root.mkdir(parents=True, exist_ok=True)
    events = []

    def run(command: list[str], expected_code: int = 0) -> None:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        events.append({"command": command, "exit_code": result.returncode, "expected_code": expected_code,
                       "output": result.stdout + result.stderr})
        if result.returncode != expected_code:
            raise AssertionError(result.stdout + result.stderr)

    run(["blender", "-b", "--factory-startup", "--threads", "2", "--python-exit-code", "2",
         "-P", str(PACKAGE / "tests" / "normals_blender_check.py")])
    run(["blender", "-b", "--factory-startup", "--threads", "2", "--python-exit-code", "2",
         "-P", str(PACKAGE / "tests" / "make_fixture.py"), "--", str(root / "input")])
    source = root / "input" / "fixture.blend"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    plan_path = source.with_name("assignment.json")
    plan = json.loads(plan_path.read_text())
    plan["input_sha256"] = source_hash
    plan_path.write_text(json.dumps(plan))
    # Relocation/cwd independence and spaces in both package and output paths.
    relocated = root / "tool with spaces"
    shutil.copytree(PACKAGE, relocated, ignore=shutil.ignore_patterns("__pycache__"))
    cli = [sys.executable, str(relocated / "run.py")]
    common = ["--input", str(source)]
    destination = root / "split with spaces"
    run(cli + ["split"] + common + ["--plan", str(plan_path), "--out", str(destination)])
    report = json.loads((destination / "report.json").read_text())
    assert report["status"] == "PASS" and report["after_reopen"]["triangles"] == 25
    assert [part["triangles"] for part in report["after_reopen"]["parts"]] == [6, 6, 13]
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in destination.iterdir() if path.is_file()}
    run(cli + ["split"] + common + ["--plan", str(plan_path), "--out", str(destination)], 2)
    assert hashes == {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in destination.iterdir() if path.is_file()}
    bad_plan = dict(plan)
    bad_plan["parts"] = plan["parts"] + [{"name": "Duplicate", "selectors": [{"object": "Armour", "faces": [0]}]}]
    bad_path = root / "duplicated.json"
    bad_path.write_text(json.dumps(bad_plan))
    failed = root / "failed_duplicate"
    run(cli + ["split"] + common + ["--plan", str(bad_path), "--out", str(failed)], 2)
    assert not (failed / "armour_split.blend").exists() and not (failed / "report.json").exists()
    run(cli + ["split"] + common + ["--plan", str(plan_path), "--out", str(source.parent)], 2)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    (root / "evidence.json").write_text(json.dumps({"status": "PASS", "commands": events,
        "checks": ["reopened geometry/UV/material/color/attributes/seam/sharp/custom normals",
                   "zero/nonfinite normals diagnosed and rejected without mutation",
                   "25 triangles and shared-boundary partition", "relocation and spaces",
                   "nonempty output remains byte-identical", "duplicate faces fail before blend delivery",
                   "input directory overlap rejected", "source file unchanged"]}, indent=2))
    print(f"PASS: {root / 'evidence.json'}")


if __name__ == "__main__":
    main()
