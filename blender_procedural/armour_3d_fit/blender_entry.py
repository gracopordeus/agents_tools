"""Blender-only entry point."""
import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fit import execute

parser = argparse.ArgumentParser()
parser.add_argument("--request", required=True, type=Path)
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
request = json.loads(args.request.read_text(encoding="utf-8"))
try:
    code = execute(request)
except Exception as error:
    (Path(request["out"]) / "failure.json").write_text(json.dumps(
        {"status": "ERROR", "error": str(error), "traceback": traceback.format_exc()}, indent=2), encoding="utf-8")
    raise
sys.exit(code)
