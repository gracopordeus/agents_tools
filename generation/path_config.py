"""Resolve caminhos compartilhados pelos geradores centralizados.

Os scripts em ``/home/ggnp/tools/generation`` não devem inferir o projeto a
partir de ``__file__``: o projeto é um consumidor externo das ferramentas.
Variáveis de ambiente permitem usar o mesmo conjunto em outro workspace ou no
worker do SaaS sem editar código.
"""
from __future__ import annotations

import os
from pathlib import Path


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


def _configured_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser().resolve() if value else default.resolve()


TOOLS_ROOT = _configured_path("SPRITE_LAB_TOOLS_ROOT", WORKSPACE_ROOT / "tools")
PROJECT_ROOT = _configured_path("SPRITE_LAB_PROJECT_ROOT", WORKSPACE_ROOT / "simple-arpg")
PYTHON = _configured_path(
    "SPRITE_LAB_PYTHON", TOOLS_ROOT / "sfx-variator/.venv/bin/python"
)
ASSET_ROOT = _configured_path(
    "SPRITE_LAB_SOURCE_ASSET_ROOT", TOOLS_ROOT / "source-assets"
)
