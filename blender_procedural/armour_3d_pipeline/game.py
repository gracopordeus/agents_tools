"""Stage 8: put the fitted pieces in the game project and run its audit.

The pieces the project had are kept in the run (``5_game/previous_pieces``) before anything is copied over them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path


def install(run: Path, game: dict, godot: str) -> dict:
    out, project = run / "5_game", Path(game["project"])
    target, source = project / game["pieces_dir"], run / "3_fit" / "pieces"
    out.mkdir()
    started = time.time()
    (out / "previous_pieces").mkdir()
    kept = [path.name for path in sorted(target.iterdir()) if path.is_file() and not path.name.endswith(".import")] if target.is_dir() else []
    for name in kept:
        shutil.copy2(target / name, out / "previous_pieces" / name)
    target.mkdir(parents=True, exist_ok=True)
    installed = [path.name for path in sorted(source.iterdir()) if path.is_file()]
    for name in installed:
        shutil.copy2(source / name, target / name)
    with (out / "godot.log").open("w", encoding="utf-8") as log:
        subprocess.run([godot, "--headless", "--import"], cwd=project, stdout=log, stderr=subprocess.STDOUT, check=False)
        audit = subprocess.run([godot, "--headless", "-s", game["audit"]], cwd=project, capture_output=True, text=True, check=False)
        log.write(audit.stdout + audit.stderr)
    verdict = next((line.strip() for line in reversed((audit.stdout + audit.stderr).splitlines()) if line.startswith("RUN:")), None)
    report = {"status": "PASS" if verdict and " PASS " in f"{verdict} " and audit.returncode == 0 else "FAIL", "audit": verdict,
              "project": str(project), "pieces_dir": game["pieces_dir"], "installed": installed, "previous_pieces": kept,
              "seconds": round(time.time() - started, 1), "log": "5_game/godot.log",
              "note": "the audit passing is not the approval: the armour is looked at with the game running, and the user accepts it"}
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
