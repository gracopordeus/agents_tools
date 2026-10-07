#!/usr/bin/env python3
"""Development launcher that restarts Sprite Lab when source files change."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
WATCHED_SUFFIXES = {".css", ".html", ".js", ".py"}


def source_snapshot() -> dict[str, int]:
    return {
        str(path): path.stat().st_mtime_ns
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix in WATCHED_SUFFIXES
        and "__pycache__" not in path.parts
    }


def start_server(host: str, port: int) -> subprocess.Popen[bytes]:
    interpreter = str(VENV_PYTHON) if VENV_PYTHON.is_file() else sys.executable
    return subprocess.Popen(
        [interpreter, str(ROOT / "server.py"), "--host", host, "--port", str(port)],
    )


def drain_timeout() -> float:
    """Grace period for in-flight background jobs on reload (seconds)."""
    import os

    try:
        return max(1.0, float(os.environ.get("SPRITE_LAB_DRAIN_TIMEOUT", "") or 15))
    except (TypeError, ValueError):
        return 15.0


def stop_server(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    # SIGTERM first: server.py stops accepting and finishes the current
    # poll cycle, letting bounded background jobs drain. SIGKILL only after
    # the grace period so Blender conversions are not orphaned mid-write.
    process.terminate()
    try:
        process.wait(timeout=drain_timeout())
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprite Lab com hotreload")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--interval", type=float, default=0.5, help="intervalo de verificação em segundos")
    args = parser.parse_args()

    process = start_server(args.host, args.port)
    snapshot = source_snapshot()
    print(f"Sprite Lab hotreload em http://{args.host}:{args.port}/sprites", flush=True)
    try:
        while True:
            time.sleep(max(args.interval, 0.1))
            current = source_snapshot()
            if current == snapshot:
                if process.poll() is None:
                    continue
                process = start_server(args.host, args.port)
                continue
            snapshot = current
            stop_server(process)
            process = start_server(args.host, args.port)
            print("Sprite Lab recarregado após alteração nos arquivos-fonte", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        stop_server(process)


if __name__ == "__main__":
    main()
