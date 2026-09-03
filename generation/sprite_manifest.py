#!/usr/bin/env python3
"""Contrato engine-agnostic para assets animados do Sprite Lab.

O manifest não contém caminhos absolutos nem recursos específicos de uma engine.
Adaptadores como o exportador Godot resolvem os artefatos relativos a partir da
localização do próprio manifest.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


SCHEMA_ID = "sprite_lab.sprite_manifest"
MANIFEST_VERSION = "1.0.0"


def relative_path(path: Path, root: Path) -> str:
    """Retorna um caminho POSIX relativo ao pacote do manifest."""
    return path.resolve().relative_to(root.resolve()).as_posix()


def validate_manifest(manifest: dict[str, Any]) -> None:
    """Valida o contrato mínimo antes de um adaptador consumir o arquivo."""
    required = {
        "schema_id",
        "manifest_version",
        "asset",
        "toolchain",
        "layout",
        "frames",
        "artifacts",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        raise ValueError(f"manifest incompleto; faltando: {', '.join(missing)}")
    if manifest["schema_id"] != SCHEMA_ID:
        raise ValueError(f"schema_id incompatível: {manifest['schema_id']!r}")
    if manifest["manifest_version"] != MANIFEST_VERSION:
        raise ValueError(f"manifest_version incompatível: {manifest['manifest_version']!r}")

    layout = manifest["layout"]
    for key in ("fit_policy", "directions", "columns", "frame_size", "fps", "foot_anchor"):
        if key not in layout:
            raise ValueError(f"layout sem campo obrigatório: {key}")
    if layout["fit_policy"] not in {"reference_fit", "runtime_fit"}:
        raise ValueError(f"fit_policy inválido: {layout['fit_policy']!r}")
    if len(layout["frame_size"]) != 2 or any(int(value) < 1 for value in layout["frame_size"]):
        raise ValueError("layout.frame_size deve conter duas dimensões positivas")
    if len(layout["foot_anchor"]) != 2:
        raise ValueError("layout.foot_anchor deve conter x e y")
    if int(layout["columns"]) < 1 or not layout["directions"]:
        raise ValueError("layout deve possuir direções e colunas positivas")

    expected_frames = len(layout["directions"]) * int(layout["columns"])
    if len(manifest["frames"]) != expected_frames:
        raise ValueError(
            f"manifest possui {len(manifest['frames'])} frames; esperado {expected_frames}"
        )
    directions = list(layout["directions"])
    columns = int(layout["columns"])
    direction_contract = manifest.get("direction_contract")
    if direction_contract is not None:
        contract_rows = direction_contract.get("rows") if isinstance(direction_contract, dict) else None
        if not isinstance(contract_rows, list) or len(contract_rows) != len(directions):
            raise ValueError("direction_contract não cobre todas as rows")
        for row_number, item in enumerate(contract_rows, start=1):
            if not isinstance(item, dict):
                raise ValueError("direction_contract possui uma row inválida")
            row_id = item.get("row_id") or item.get("row")
            if row_id != directions[row_number - 1] or item.get("row") != row_number:
                raise ValueError("direction_contract não corresponde à posição física das rows")
    seen: set[tuple[str, int]] = set()
    for frame in manifest["frames"]:
        for key in ("direction", "index", "path", "rect", "bbox"):
            if key not in frame:
                raise ValueError(f"frame sem campo obrigatório: {key}")
        direction = frame["direction"]
        index = int(frame["index"])
        if direction not in directions or not 0 <= index < columns:
            raise ValueError(f"frame fora do layout: {direction!r}/{index}")
        key = (direction, index)
        if key in seen:
            raise ValueError(f"frame duplicado: {direction!r}/{index}")
        seen.add(key)
        path = Path(str(frame["path"]))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("frame.path deve permanecer relativo ao pacote")
        if len(frame["rect"]) != 4 or len(frame["bbox"]) != 4:
            raise ValueError("frame.rect e frame.bbox devem conter quatro valores")
    if len(seen) != expected_frames:
        raise ValueError("manifest não cobre todas as combinações direção/coluna")


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Valida e grava JSON estável, com newline final."""
    import json

    validate_manifest(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
