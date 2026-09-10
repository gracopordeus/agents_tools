"""Partition Blender cells without changing the full-sheet framing contract."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image


def worker_count(value, rows: int) -> int:
    count = int(value)
    if count not in (1, 2, 4, 8):
        raise ValueError("blender_workers deve ser 1, 2, 4 ou 8")
    # A quantidade de workers é uma escolha explícita de paralelização. Pode
    # exceder o número de CPUs; nesse caso o orçamento de threads por worker
    # é reduzido para 1 abaixo, permitindo medir também esse cenário.
    return min(count, rows)


def row_partitions(rows: int, count: int) -> list[list[int]]:
    return [list(range(index, rows, count)) for index in range(count)]


def merge_reports(request: dict, reports: list[dict], output: Path) -> dict:
    rows, columns = int(request["rows"]), int(request["phases"])
    if not reports:
        raise RuntimeError("Nenhum worker Blender retornou metadata")
    first = reports[0]
    # Each process fits against ALL rows/phases, even though it only renders
    # its assigned rows. Reject drift before publishing any final metadata.
    shared = ("directions", "direction_contract", "direction_targets", "cell",
              "sampled_frames", "camera", "effective_render_profile", "bounds",
              "animation_timing", "lighting", "render_backend")
    expected = {(row, col) for row in range(rows) for col in range(columns)}
    cells = {}
    for report in reports:
        for key in shared:
            if report.get(key) != first.get(key):
                raise RuntimeError(f"Workers Blender divergiram em {key}")
        for cell in report.get("cells", []):
            key = (cell["row"], cell["column"])
            if key not in expected or key in cells:
                raise RuntimeError(f"Célula duplicada ou fora da grade: {key}")
            if cell.get("direction") != first["directions"][key[0]]:
                raise RuntimeError(f"Direção incorreta: {key}")
            if cell.get("frame") != first["sampled_frames"][key[1]]:
                raise RuntimeError(f"Fase incorreta: {key}")
            source = Path(cell["path"])
            with Image.open(source) as image:
                if list(image.size) != first["cell"] or image.mode != "RGBA":
                    raise RuntimeError(f"PNG inválido: {source}")
                image.load()
            cells[key] = dict(cell)
    if set(cells) != expected:
        raise RuntimeError(f"Folha incompleta: {len(cells)}/{len(expected)} células")
    for (row, col), cell in cells.items():
        destination = output / f"row{row}_col{col}.png"
        shutil.copy2(cell["path"], destination)
        cell["path"] = str(destination)
    return {**first, "cells": [cells[key] for key in sorted(cells)]}


def run_backend(command, output, result_path, backend, timeout, *, run_worker):
    request_path = Path(command[command.index("--request") + 1])
    request = json.loads(request_path.read_text())
    count = worker_count(request.get("blender_workers", 4), int(request["rows"]))
    threads = max(1, (os.cpu_count() or 1) // count)
    cancel = threading.Event()
    started = time.monotonic()
    jobs = []
    for index, assigned in enumerate(row_partitions(int(request["rows"]), count)):
        directory = output / "workers" / backend / str(index)
        directory.mkdir(parents=True, exist_ok=True)
        child_request = directory / "request.json"
        child_request.write_text(json.dumps({**request, "output": str(directory),
            "assigned_rows": assigned, "render_threads": threads}, indent=2) + "\n")
        child_command = list(command)
        child_command[child_command.index("--request") + 1] = str(child_request)
        # Blender and Mesa must share the CPU budget across processes.
        child_command[1:1] = ["--threads", str(threads)]
        jobs.append((child_command, directory, Path(str(child_request) + ".result.json")))
    reports = []
    try:
        with ThreadPoolExecutor(max_workers=count) as pool:
            futures = {pool.submit(run_worker, cmd, directory, result, backend,
                timeout, cancel_event=cancel, render_threads=threads): result
                for cmd, directory, result in jobs}
            for future in as_completed(futures):
                try:
                    completed = future.result()
                    if completed.returncode != 0:
                        raise RuntimeError(completed.stderr or completed.stdout[-2000:])
                    reports.append(json.loads(futures[future].read_text()))
                except BaseException:
                    cancel.set()
                    raise
        report = merge_reports(request, reports, output)
        report["parallel_render"] = {"workers": count, "threads_per_worker": threads,
            "backend": backend, "row_partitions": row_partitions(int(request["rows"]), count),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "worker_timings": [r.get("timing") for r in reports]}
        report["timing"] = {"total_seconds": report["parallel_render"]["elapsed_seconds"]}
        encoded = json.dumps(report, indent=2) + "\n"
        (output / "render_metadata.json").write_text(encoded)
        result_path.write_text(encoded)
        return subprocess.CompletedProcess(command, 0, "", "")
    except Exception as error:
        return subprocess.CompletedProcess(command, 1, "", str(error))
