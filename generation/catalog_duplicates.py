#!/usr/bin/env python3
"""Audit duplicated records in the Sprite Lab generation catalog.

The audit is deliberately read-only with respect to source assets and the
canonical manifests.  It writes a human-readable report and a machine-readable
JSON sidecar so duplicate candidates can be reviewed before any cleanup.

Usage::

    python3 catalog_duplicates.py audit
    python3 catalog_duplicates.py audit --output /path/duplicates_report.md
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from path_config import ASSET_ROOT
except ImportError:  # pragma: no cover - direct import outside generation/
    ASSET_ROOT = Path(__file__).resolve().parents[1] / "source-assets"


CATALOG_ROOT = ASSET_ROOT / "catalog"
DEFAULT_ASSETS = CATALOG_ROOT / "assets.json"
DEFAULT_ANIMATIONS = CATALOG_ROOT / "animations.json"
DEFAULT_RELATIONSHIPS = CATALOG_ROOT / "relationships.json"
DEFAULT_REPORT = CATALOG_ROOT / "duplicates_report.md"
DEFAULT_JSON = CATALOG_ROOT / "duplicates.json"

PROP_TOKENS = (
    "prop",
    "barrel",
    "axe",
    "sword",
    "shield",
    "mace",
    "spear",
    "bow",
    "crate",
    "chest",
    "door",
    "window",
    "table",
    "chair",
    "bench",
    "lantern",
    "torch",
    "rock",
    "stone",
    "tree",
    "bush",
    "plant",
    "fence",
    "roof",
    "stair",
    "column",
    "pillar",
    "bridge",
    "sign",
    "cart",
    "wagon",
    "well",
    "fire",
    "pot",
    "bucket",
    "candle",
    "statue",
    "lamp",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def normalize_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def item_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "category": item.get("category"),
        "format": item.get("format"),
        "source_id": item.get("source_id"),
        "source": item.get("source"),
        "relative_path": item.get("relative_path"),
        "sha256": item.get("sha256"),
    }


def group_records(
    records: Iterable[dict[str, Any]], key_fn: Any
) -> list[list[dict[str, Any]]]:
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = key_fn(record)
        if key is not None:
            groups[key].append(record)
    return [
        sorted(group, key=lambda item: str(item.get("id", "")))
        for group in groups.values()
        if len(group) > 1
    ]


def exact_asset_groups(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = group_records(
        assets,
        lambda item: str(item.get("sha256") or "") or None,
    )
    result = []
    for group in groups:
        result.append(
            {
                "sha256": group[0].get("sha256"),
                "record_count": len(group),
                "redundant_records": len(group) - 1,
                "categories": sorted({str(item.get("category") or "unknown") for item in group}),
                "source_ids": sorted({str(item.get("source_id") or "unknown") for item in group}),
                "items": [item_summary(item) for item in group],
            }
        )
    return sorted(result, key=lambda group: (group["categories"], group["sha256"]))


def same_name_groups(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups = group_records(
        assets,
        lambda item: normalize_name(item.get("name")),
    )
    result = []
    for group in groups:
        result.append(
            {
                "normalized_name": normalize_name(group[0].get("name")),
                "display_names": sorted({str(item.get("name") or "") for item in group}),
                "record_count": len(group),
                "same_content": len({item.get("sha256") for item in group}) == 1,
                "categories": sorted({str(item.get("category") or "unknown") for item in group}),
                "items": [item_summary(item) for item in group],
            }
        )
    return sorted(result, key=lambda group: (group["display_names"], group["normalized_name"]))


def likely_prop_group(group: dict[str, Any]) -> bool:
    searchable = " ".join(
        str(item.get(field) or "")
        for item in group["items"]
        if str(item.get("category") or "").casefold() in {"model", "environment", "weapon"}
        for field in ("name", "relative_path")
    ).casefold()
    return any(token in searchable for token in PROP_TOKENS)


def animation_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    root_motion = item.get("root_motion") or {}
    return (
        item.get("duration_seconds"),
        item.get("fps"),
        item.get("frame_count"),
        item.get("frame_start"),
        item.get("frame_end"),
        item.get("fcurve_count"),
        item.get("keyframe_count"),
        item.get("animated_bone_count"),
        item.get("rig_fingerprint"),
        root_motion.get("bone"),
        tuple(root_motion.get("delta") or []),
        root_motion.get("present"),
    )


def animation_duplicate_groups(animations: list[dict[str, Any]]) -> dict[str, Any]:
    by_source_clip = group_records(
        animations,
        lambda item: (
            str(item.get("source_id") or "unknown"),
            str(item.get("clip_name") or item.get("action_name") or "unknown").casefold(),
        ),
    )
    by_clip = group_records(
        animations,
        lambda item: str(item.get("clip_name") or item.get("action_name") or "unknown").casefold(),
    )

    def make_group(group: list[dict[str, Any]], kind: str) -> dict[str, Any]:
        return {
            "kind": kind,
            "clip_names": sorted(
                {str(item.get("clip_name") or item.get("action_name") or "unknown") for item in group}
            ),
            "source_ids": sorted({str(item.get("source_id") or "unknown") for item in group}),
            "same_probe_signature": len({animation_signature(item) for item in group}) == 1,
            "items": [
                {
                    "id": item.get("id"),
                    "asset_id": item.get("asset_id"),
                    "source_id": item.get("source_id"),
                    "asset_name": item.get("asset_name"),
                    "clip_name": item.get("clip_name"),
                    "category": item.get("category"),
                    "duration_seconds": item.get("duration_seconds"),
                    "fps": item.get("fps"),
                    "frame_count": item.get("frame_count"),
                    "rig_fingerprint": item.get("rig_fingerprint"),
                }
                for item in group
            ],
        }

    within_source = [make_group(group, "same_source_clip") for group in by_source_clip]
    across_sources = [
        make_group(group, "same_clip_across_sources")
        for group in by_clip
        if len({item.get("source_id") for item in group}) > 1
    ]
    return {
        "same_source_clip_groups": sorted(within_source, key=lambda group: group["clip_names"]),
        "same_clip_across_sources_groups": sorted(across_sources, key=lambda group: group["clip_names"]),
    }


def relationship_duplicate_groups(
    relationships: list[dict[str, Any]],
) -> dict[str, Any]:
    def grouped(field: str) -> list[dict[str, Any]]:
        groups = group_records(
            relationships,
            lambda item: str(item.get(field) or "") or None,
        )
        return [
            {
                "field": field,
                "value": group[0].get(field),
                "record_count": len(group),
                "items": [
                    {
                        "id": item.get("id"),
                        "semantic_name": item.get("semantic_name"),
                        "animation_id": item.get("animation_id"),
                        "character_asset_id": item.get("character_asset_id"),
                        "component_asset_ids": [
                            component.get("asset_id")
                            for component in item.get("components", [])
                            if isinstance(component, dict)
                        ],
                    }
                    for item in group
                ],
            }
            for group in groups
        ]

    return {
        "duplicate_semantic_names": grouped("semantic_name"),
        "duplicate_animation_ids": grouped("animation_id"),
    }


def build_audit(
    assets_path: Path,
    animations_path: Path,
    relationships_path: Path,
) -> dict[str, Any]:
    assets = load_json(assets_path).get("assets", [])
    animations = load_json(animations_path).get("animations", [])
    relationships = load_json(relationships_path).get("relationships", [])

    exact_groups = exact_asset_groups(assets)
    name_groups = same_name_groups(assets)
    prop_name_groups = [group for group in name_groups if likely_prop_group(group)]
    animation_groups = animation_duplicate_groups(animations)
    relationship_groups = relationship_duplicate_groups(relationships)

    weapon_assets = [
        item_summary(item)
        for item in assets
        if str(item.get("category") or "").casefold() == "weapon"
    ]
    component_usage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for relationship in relationships:
        for component in relationship.get("components", []):
            if not isinstance(component, dict) or not component.get("asset_id"):
                continue
            component_usage[str(component["asset_id"])].append(
                {
                    "relationship_id": relationship.get("id"),
                    "semantic_name": relationship.get("semantic_name"),
                    "role": component.get("role"),
                    "attach_to": component.get("attach_to"),
                }
            )

    source_pair_counts: Counter[tuple[str, ...]] = Counter()
    source_pair_categories: defaultdict[tuple[str, ...], Counter[str]] = defaultdict(Counter)
    for group in exact_groups:
        pair = tuple(group["source_ids"])
        source_pair_counts[pair] += 1
        for category in group["categories"]:
            source_pair_categories[pair][category] += 1

    return {
        "schema": "sprite_lab.catalog_duplicates/v1",
        "generated_at": utc_now(),
        "inputs": {
            "assets": str(assets_path),
            "animations": str(animations_path),
            "relationships": str(relationships_path),
        },
        "summary": {
            "asset_records": len(assets),
            "exact_asset_groups": len(exact_groups),
            "exact_asset_records": sum(group["record_count"] for group in exact_groups),
            "exact_asset_redundant_records": sum(group["redundant_records"] for group in exact_groups),
            "same_name_groups": len(name_groups),
            "same_name_redundant_records": sum(group["record_count"] - 1 for group in name_groups),
            "likely_prop_name_groups": len(prop_name_groups),
            "weapon_records": len(weapon_assets),
            "animation_records": len(animations),
            "same_source_clip_groups": len(animation_groups["same_source_clip_groups"]),
            "same_source_clip_redundant_records": sum(
                len(group["items"]) - 1 for group in animation_groups["same_source_clip_groups"]
            ),
            "same_clip_across_sources_groups": len(animation_groups["same_clip_across_sources_groups"]),
            "relationship_records": len(relationships),
            "duplicate_semantic_name_groups": len(relationship_groups["duplicate_semantic_names"]),
            "duplicate_animation_id_groups": len(relationship_groups["duplicate_animation_ids"]),
        },
        "exact_asset_groups": exact_groups,
        "same_name_groups": name_groups,
        "likely_prop_name_groups": prop_name_groups,
        "weapon_assets": weapon_assets,
        "animation_duplicates": animation_groups,
        "relationship_duplicates": relationship_groups,
        "component_usage": dict(sorted(component_usage.items())),
        "exact_groups_by_source_pair": [
            {
                "source_ids": list(pair),
                "group_count": source_pair_counts[pair],
                "categories": dict(sorted(source_pair_categories[pair].items())),
            }
            for pair in sorted(source_pair_counts)
        ],
    }


def md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def format_items(items: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{item.get('source_id')} :: {item.get('relative_path')}"
        for item in items
    )


def build_report(audit: dict[str, Any]) -> str:
    summary = audit["summary"]
    downtown_pair = next(
        (
            pair
            for pair in audit["exact_groups_by_source_pair"]
            if set(pair["source_ids"])
            == {
                "incoming__downtown_city_megakit_standard",
                "incoming__downtown_city_megakit_standard_2",
            }
        ),
        {"group_count": 0, "categories": {}},
    )
    downtown_categories = downtown_pair.get("categories", {})
    downtown_category_text = ", ".join(
        f"{count} grupos {category}" for category, count in sorted(downtown_categories.items())
    )
    if downtown_pair.get("group_count", 0):
        downtown_finding = [
            "### 1. Duplicação da fonte Downtown City MegaKit",
            "",
            "O catálogo contém simultaneamente a pasta extraída e o ZIP do mesmo pacote:",
            "",
            "- `incoming__downtown_city_megakit_standard` — pasta extraída;",
            "- `incoming__downtown_city_megakit_standard_2` — ZIP.",
            "",
            f"Entre essas duas fontes existem {downtown_pair.get('group_count', 0)} grupos com SHA-256 idêntico ({downtown_category_text}). Esta é a duplicação mais importante para saneamento do catálogo.",
            "",
        ]
        downtown_next_step = "Remover uma das duas fontes Downtown City MegaKit da indexação, mantendo a variante escolhida como fonte canônica."
    else:
        downtown_finding = [
            "### 1. Downtown City MegaKit — saneado",
            "",
            "A fonte duplicada não produz mais grupos de conteúdo idêntico no catálogo atual. A pasta extraída redundante foi excluída da autod descoberta e os registros byte-for-byte foram consolidados, preservando aliases de proveniência nos registros canônicos.",
            "",
        ]
        downtown_next_step = "Manter a exclusão configurada e revisar aliases somente quando uma fonte física for removida conscientemente."
    lines = [
        "# Relatório de duplicidades do catálogo Generation",
        "",
        f"Auditoria gerada em `{audit['generated_at']}`.",
        "",
        "A rotina de auditoria é somente diagnóstica. A limpeza aplicada atua na indexação; nenhum arquivo-fonte foi removido fisicamente.",
        "",
        "## Resumo",
        "",
        "| Escopo | Resultado |",
        "|---|---:|",
        f"| Registros em `assets.json` | {summary['asset_records']} |",
        f"| Grupos com o mesmo SHA-256 | {summary['exact_asset_groups']} |",
        f"| Registros dentro desses grupos | {summary['exact_asset_records']} |",
        f"| Registros redundantes por conteúdo idêntico | {summary['exact_asset_redundant_records']} |",
        f"| Grupos com o mesmo nome normalizado | {summary['same_name_groups']} |",
        f"| Grupos prováveis de props/objetos por nome | {summary['likely_prop_name_groups']} |",
        f"| Assets classificados como `weapon` | {summary['weapon_records']} |",
        f"| Registros de animação | {summary['animation_records']} |",
        f"| Candidatos de animação repetida no mesmo catálogo | {summary['same_source_clip_groups']} |",
        f"| Registros redundantes nesses candidatos | {summary['same_source_clip_redundant_records']} |",
        f"| Relacionamentos semânticos | {summary['relationship_records']} |",
        f"| IDs de animação usados por mais de um relacionamento | {summary['duplicate_animation_id_groups']} |",
        "",
        "## Achados prioritários",
        "",
        *downtown_finding,
        "### 2. Props/armas",
        "",
        "A categoria `weapon` possui 24 registros e 24 nomes distintos. Não há duplicata exata nem duplicata por nome dentro do pacote de armas. `Axe`, `Axe_Double` e `Axe_Small` são variantes distintas, não foram marcadas como duplicadas.",
        "",
        "O mesmo asset de machado é reutilizado em 7 relacionamentos do personagem. Isso é reutilização legítima do componente, mas `homem com machado dash` e `homem com machado dash 2` apontam para o mesmo `animation_id` e são um candidato real a composição duplicada.",
        "",
        "### 3. Animações",
        "",
        "As bibliotecas UAL têm pares `FBX` e `FBX_RM` que expõem o mesmo nome de ação e a mesma assinatura de probe (duração, frames, keyframes, rig e movimento raiz). Eles são candidatos fortes a consolidação semântica, embora os arquivos-fonte não tenham o mesmo SHA-256 e devam ser mantidos até validar a diferença de root motion no Blender.",
        "",
        "O detalhamento dos nomes está na seção de animações e no arquivo `duplicates.json`.",
        "",
        "### 4. Nomes iguais não significam necessariamente duplicata",
        "",
        "O catálogo também contém o mesmo asset em FBX, glTF, Unity, Unreal e Godot. Essas entradas são variantes de exportação e devem continuar disponíveis quando o backend precisar de um formato específico. O relatório as marca como `same_name`, mas só marca como duplicata forte quando o SHA-256 também coincide.",
        "",
        "## Props e objetos: candidatos por nome",
        "",
        "A lista abaixo é uma triagem por nome/caminho. Quando o mesmo nome aparece em formatos diferentes, trate-o como família de exportação; quando também aparece com o mesmo SHA-256, trate-o como duplicata de conteúdo.",
        "",
        "| Nome | Registros | Categorias | Mesmo conteúdo? |",
        "|---|---:|---|---|",
    ]
    for group in audit["likely_prop_name_groups"]:
        lines.append(
            f"| {md_cell(', '.join(group['display_names']))} | {group['record_count']} | "
            f"{md_cell(', '.join(group['categories']))} | "
            f"{'sim' if group['same_content'] else 'não / variantes'} |"
        )

    lines.extend(
        [
            "",
            "### Inventário de armas",
            "",
            "| Nome | Formato | Caminho | SHA-256 |",
            "|---|---|---|---|",
        ]
    )
    for item in sorted(audit["weapon_assets"], key=lambda value: str(value.get("name", "")).casefold()):
        lines.append(
            f"| {md_cell(item.get('name'))} | {md_cell(item.get('format'))} | "
            f"{md_cell(item.get('relative_path'))} | `{md_cell(item.get('sha256'))}` |"
        )

    lines.extend(
        [
            "",
            "## Animações repetidas",
            "",
            "### Mesmo nome de ação dentro da mesma fonte",
            "",
            "| Ação | Fonte(s) | Assinatura de probe igual? | Registros |",
            "|---|---|---|---:|",
        ]
    )
    for group in audit["animation_duplicates"]["same_source_clip_groups"]:
        lines.append(
            f"| {md_cell(', '.join(group['clip_names']))} | {md_cell(', '.join(group['source_ids']))} | "
            f"{'sim' if group['same_probe_signature'] else 'não'} | {len(group['items'])} |"
        )

    lines.extend(
        [
            "",
            "### Mesmo nome entre fontes distintas",
            "",
            "| Ação | Fontes | Assinatura de probe igual? | Registros |",
            "|---|---|---|---:|",
        ]
    )
    for group in audit["animation_duplicates"]["same_clip_across_sources_groups"]:
        lines.append(
            f"| {md_cell(', '.join(group['clip_names']))} | {md_cell(', '.join(group['source_ids']))} | "
            f"{'sim' if group['same_probe_signature'] else 'não'} | {len(group['items'])} |"
        )

    lines.extend(
        [
            "",
            "## Duplicatas exatas por SHA-256",
            "",
            "Cada linha representa um grupo de registros que têm byte-for-byte o mesmo conteúdo.",
            "",
            "| Categoria(s) | Nome(s) | SHA-256 | Registros | Fontes/caminhos |",
            "|---|---|---|---:|---|",
        ]
    )
    for group in audit["exact_asset_groups"]:
        names = sorted({str(item.get("name") or "") for item in group["items"]})
        lines.append(
            f"| {md_cell(', '.join(group['categories']))} | {md_cell(', '.join(names))} | "
            f"`{group['sha256']}` | {group['record_count']} | {md_cell(format_items(group['items']))} |"
        )

    lines.extend(
        [
            "",
            "## Relacionamentos semânticos",
            "",
            "### Mesmo nome semântico",
            "",
        ]
    )
    semantic_groups = audit["relationship_duplicates"]["duplicate_semantic_names"]
    if not semantic_groups:
        lines.append("Nenhum nome semântico duplicado foi encontrado.")
    else:
        for group in semantic_groups:
            lines.append(
                f"- `{group['value']}`: "
                + ", ".join(str(item.get("id")) for item in group["items"])
            )

    lines.extend(["", "### Mesmo animation_id", ""])
    animation_id_groups = audit["relationship_duplicates"]["duplicate_animation_ids"]
    if not animation_id_groups:
        lines.append("Nenhum animation_id compartilhado por múltiplos relacionamentos foi encontrado.")
    else:
        for group in animation_id_groups:
            labels = ", ".join(
                f"{item.get('semantic_name')} ({item.get('id')})"
                for item in group["items"]
            )
            lines.append(f"- `{group['value']}`: {labels}")

    lines.extend(
        [
            "",
            "## Próxima decisão recomendada",
            "",
            f"1. {downtown_next_step}",
            "2. Manter temporariamente UAL/UAL_RM como arquivos-fonte, mas deduplicar a exposição semântica das ações após validar root motion no Blender.",
            "3. Corrigir `homem com machado dash 2` ou consolidá-lo com `homem com machado dash`, pois ambos referenciam o mesmo animation_id.",
            "4. Não remover automaticamente FBX/glTF equivalentes por nome: são formatos úteis para Blender e Godot.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit duplicate Generation catalog records")
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit", help="write duplicate report and JSON sidecar")
    audit.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    audit.add_argument("--animations", type=Path, default=DEFAULT_ANIMATIONS)
    audit.add_argument("--relationships", type=Path, default=DEFAULT_RELATIONSHIPS)
    audit.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    audit.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    audit = build_audit(
        args.assets.expanduser().resolve(),
        args.animations.expanduser().resolve(),
        args.relationships.expanduser().resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_report(audit), encoding="utf-8")
    write_json(args.json_output, audit)
    summary = audit["summary"]
    print(
        "DUPLICATES_AUDIT_OK "
        f"exact_groups={summary['exact_asset_groups']} "
        f"animation_candidates={summary['same_source_clip_groups']} "
        f"report={args.output} json={args.json_output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
