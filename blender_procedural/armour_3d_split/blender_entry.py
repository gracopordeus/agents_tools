"""Blender-only entry point; deliberately independent of the fitting pipeline."""
import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mesh_split import execute

parser = argparse.ArgumentParser()
parser.add_argument("--request", required=True, type=Path)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
request = json.loads(args.request.read_text())
try:
    execute(request)
except Exception as error:
    out = Path(request["out"])
    (out / "failure.json").write_text(json.dumps({"status": "FAIL", "error": str(error),
                                                "traceback": traceback.format_exc()}, indent=2))
    raise
