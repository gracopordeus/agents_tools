"""Builder and validator for portable character/weapon layered bundles."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

from PIL import Image


LAYERED_BUNDLE_SCHEMA = "sprite_lab.layered_sprite_bundle/v1"
LAYERED_BUNDLE_SCHEMA_V1 = LAYERED_BUNDLE_SCHEMA
LAYERED_BUNDLE_SCHEMA_V2 = "sprite_lab.layered_sprite_bundle/v2"
GENERATION_MODE = "character_weapon_holdout"
GENERATION_MODE_V2 = "character_component_holdout"
GRID_ROWS = 8
GRID_COLUMNS = 8
EXPECTED_LAYERS = (("weapon", 0), ("character_holdout", 1))
PUBLISHED_ARTIFACTS = {
    "character_holdout": "character_holdout_spritesheet.png",
    "weapon": "weapon_spritesheet.png",
    "holdout_source": "holdout_cut_mask.png",
    "preview": "composite_preview.png",
}
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
ARTIFACT_HASH_REGISTRY_SCHEMA = "sprite_lab.layered_artifact_hashes/v1"
ARTIFACT_HASH_REGISTRY_FILENAME = "layered_artifact_hashes.json"
PROVENANCE_CATEGORIES = {
    "reference_character",
    "reference_weapon",
    "blender_channel",
    "raw_output",
    "processed_output",
    "front_mask",
    "final_mask",
    "preview",
    "manifest",
    "config",
    "other",
}
REQUIRED_PROVENANCE_CATEGORIES = {
    "reference_character",
    "reference_weapon",
    "blender_channel",
    "raw_output",
    "processed_output",
    "front_mask",
    "final_mask",
    "preview",
    "manifest",
    "config",
}
MANIFEST_FIELDS = {
    "schema",
    "generation_mode",
    "layout",
    "layers",
    "preview",
    "source",
    "hashes",
}
LAYOUT_FIELDS = {"rows", "columns", "cell_size"}
LAYER_FIELDS = (
    {"id", "file", "z"},
    {"id", "file", "z", "holdout_source"},
)
V2_MANIFEST_FIELDS = {
    "schema",
    "generation_mode",
    "layout",
    "runtime",
    "layers",
    "preview",
    "source",
    "hashes",
}
V2_LAYOUT_FIELDS = {"rows", "columns", "cell_size"}
V2_RUNTIME_FIELDS = {
    "actions",
    "fps",
    "directions",
    "frame_size",
    "coordinate_space",
    "pivot",
    "foot_anchor",
}
V2_ACTION_FIELDS = {"id", "start_column", "frame_count", "loop"}
V2_DIRECTION_FIELDS = {"id", "row", "vector"}
V2_BASE_LAYER_FIELDS = {"id", "role", "file", "z", "immutable", "blend_mode"}
V2_COMPONENT_LAYER_FIELDS = {
    "id",
    "role",
    "kind",
    "file",
    "z",
    "blend_mode",
    "occlusion",
}
V2_OCCLUSION_FIELDS = {"mode", "visible_mask", "occluders"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    """Serialize metadata deterministically before fingerprinting it."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"config deve conter apenas valores JSON finitos: {exc}") from None


def validate_auditable_source(source: Any) -> dict[str, Any]:
    """Require the minimum AI Render identity needed to audit a bundle."""
    if not isinstance(source, Mapping):
        raise ValueError("source auditável deve ser um objeto JSON")
    aliases = {
        "job": ("job_id", "job"),
        "render": ("render_id", "render_name", "render"),
        "provider": ("provider", "provider_name"),
        "model": ("model", "model_id", "model_name"),
    }
    missing: list[str] = []
    for label, names in aliases.items():
        found = False
        for name in names:
            value = source.get(name)
            if isinstance(value, str) and value.strip():
                found = True
                break
            if type(value) in (int, float) and math.isfinite(float(value)):
                found = True
                break
        if not found:
            missing.append(label)
    if missing:
        raise ValueError(
            "source deve ser auditável; campos ausentes: " + ", ".join(missing)
        )
    # Validate now so callers cannot pass an object that only fails while the
    # manifest is being serialized after expensive processing.
    _canonical_json(dict(source))
    return dict(source)


def _registry_scope(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}.root deve ser uma string")
    scope = value.strip() or "source"
    if not scope or "." in scope or "/" in scope or "\\" in scope:
        raise ValueError(f"{field}.root deve ser um escopo simples")
    return scope


def _registry_category(value: Any, role: str) -> str:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"artifacts[{role}].category deve ser uma string")
    category = value.strip() if isinstance(value, str) else ""
    category = category or {
        "character_reference": "reference_character",
        "weapon_reference": "reference_weapon",
        "front_mask": "front_mask",
        "final_mask": "final_mask",
        "preview": "preview",
        "manifest": "manifest",
        "config": "config",
    }.get(role, "other")
    category = {
        "character_reference": "reference_character",
        "weapon_reference": "reference_weapon",
        "reference": "reference_character" if "character" in role else "reference_weapon" if "weapon" in role else "other",
        "blender_channels": "blender_channel",
        "raw_outputs": "raw_output",
        "processed_outputs": "processed_output",
        "front_masks": "front_mask",
        "final_masks": "final_mask",
        "preview_output": "preview",
        "configuration": "config",
    }.get(category, category)
    if category not in PROVENANCE_CATEGORIES:
        raise ValueError(f"artifacts[{role}].category inválida: {category}")
    return category


def _registry_specs(artifacts: Any) -> dict[str, Any]:
    if artifacts is None:
        return {}
    if isinstance(artifacts, Mapping):
        result = dict(artifacts)
    elif isinstance(artifacts, Sequence) and not isinstance(artifacts, (str, bytes)):
        result = {}
        for index, item in enumerate(artifacts):
            if not isinstance(item, Mapping):
                raise ValueError(f"artifacts[{index}] deve ser um objeto")
            role = str(item.get("id") or item.get("role") or "").strip()
            if not role:
                raise ValueError(f"artifacts[{index}].id é obrigatório")
            if role in result:
                raise ValueError(f"artifacts[{role}] duplicado")
            result[role] = dict(item)
        return result
    else:
        raise ValueError("artifacts deve ser um objeto ou lista")
    normalized: dict[str, Any] = {}
    for role, spec in result.items():
        key = str(role or "").strip()
        if not key:
            raise ValueError("artifacts exige ids não vazios")
        normalized[key] = spec
    return normalized


def _discover_provenance_specs(
    roots: Mapping[str, Path],
    existing: Mapping[str, Any],
) -> dict[str, Any]:
    """Find conventional local chain files without inventing missing inputs."""
    candidates = (
        ("character_reference", "reference.png", "reference_character"),
        ("weapon_reference", "weapon_reference.png", "reference_weapon"),
        ("blender_beauty", "spritesheet_beauty.png", "blender_channel"),
        ("blender_bones", "spritesheet_bones.png", "blender_channel"),
        ("blender_lineart", "spritesheet_lineart.png", "blender_channel"),
        ("blender_frame_control", "frame_control.png", "blender_channel"),
        ("character_raw", "character_full.png", "raw_output"),
        ("weapon_raw", "weapon_full.png", "raw_output"),
        ("front_mask", "weapon_front_mask.png", "front_mask"),
    )
    discovered = dict(existing)
    for role, filename, category in candidates:
        if role in discovered:
            continue
        for scope, root in roots.items():
            matches = sorted(root.rglob(filename)) if root.is_dir() else []
            if not matches:
                continue
            candidate = next(
                (item.resolve() for item in matches if item.resolve().is_relative_to(root.resolve())),
                None,
            )
            if candidate is None:
                continue
            relative = candidate.relative_to(root.resolve()).as_posix()
            discovered[role] = {
                "path": relative,
                "root": scope,
                "category": category,
            }
            break
    if "config" not in discovered:
        for scope, root in roots.items():
            for relative in ("config/render_spec.json", "render_spec.json", "config.json"):
                candidate = root / relative
                if candidate.is_file():
                    discovered["config"] = {
                        "path": relative,
                        "root": scope,
                        "category": "config",
                    }
                    break
            if "config" in discovered:
                break
    return discovered


def _registry_record(
    roots: dict[str, Path],
    role: str,
    spec: Any,
    *,
    hash_file: Callable[[Path], str],
) -> dict[str, Any]:
    if isinstance(spec, Mapping):
        path_value = spec.get("path")
        if path_value is None:
            raise ValueError(f"artifacts[{role}].path é obrigatório")
        scope = _registry_scope(spec.get("root", spec.get("scope", "source")), f"artifacts[{role}]")
        category = _registry_category(spec.get("category"), role)
        expected_hash = spec.get("sha256", ...)
    else:
        path_value = spec
        scope = "source"
        category = _registry_category(None, role)
        expected_hash = ...
    if scope not in roots:
        raise ValueError(f"artifacts[{role}] usa raiz desconhecida: {scope}")
    root = roots[scope].expanduser().resolve()
    path, relative = _source_path(root, path_value, f"artifacts[{role}].path")
    # `_source_path` accepts filesystem Path objects, while this second
    # boundary guarantees that serialized/public paths follow the same
    # portable contract as the v1 bundle manifest.
    relative = _portable_path(relative, f"artifacts[{role}].path")
    if "\\" in relative:
        raise ValueError(f"artifacts[{role}].path deve usar separadores POSIX")
    if not path.is_file():
        raise ValueError(f"artifacts[{role}] não existe: {relative}")
    digest = str(hash_file(path)).strip().lower()
    if not SHA256_PATTERN.fullmatch(digest):
        raise ValueError(f"artifacts[{role}] não produziu SHA-256 válido")
    if expected_hash is not ...:
        if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(expected_hash):
            raise ValueError(f"artifacts[{role}].sha256 deve ser um SHA-256 hexadecimal")
        if expected_hash != digest:
            raise ValueError(f"artifacts[{role}] hash não corresponde ao arquivo")
    return {
        "category": category,
        "scope": scope,
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": digest,
    }


def _registry_hash_key(record: Mapping[str, Any]) -> str:
    return f"{record['scope']}:{record['path']}"


def _registry_config_record(
    roots: dict[str, Path],
    config: Any,
    *,
    hash_file: Callable[[Path], str],
) -> dict[str, Any]:
    if isinstance(config, (str, Path)) or (
        isinstance(config, Mapping) and "path" in config
    ):
        spec = config if isinstance(config, Mapping) else {"path": config}
        record = _registry_record(roots, "config", spec, hash_file=hash_file)
        return {
            "category": "config",
            "scope": record["scope"],
            "path": record["path"],
            "bytes": record["bytes"],
            "sha256": record["sha256"],
            "fingerprint": record["sha256"],
        }
    if config is None:
        # An empty object is explicit and deterministic; callers that have a
        # config file should pass its path so the file hash is also recorded.
        config = {}
    encoded = _canonical_json(config).encode("utf-8")
    fingerprint = hashlib.sha256(encoded).hexdigest()
    return {
        "category": "config",
        "fingerprint": fingerprint,
    }


def _provenance_complete(registry: Mapping[str, Any]) -> bool:
    categories = {
        record.get("category")
        for record in registry.get("artifacts", {}).values()
        if isinstance(record, Mapping)
    }
    manifest = registry.get("manifest")
    if isinstance(manifest, Mapping):
        categories.add("manifest")
    config = registry.get("config")
    if isinstance(config, Mapping):
        categories.add("config")
    return REQUIRED_PROVENANCE_CATEGORIES.issubset(categories)


def build_artifact_hash_registry(
    root: Path,
    artifacts: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    *,
    manifest: Path | str | Mapping[str, Any] | None = None,
    config: Any = None,
    roots: Mapping[str, Path] | None = None,
    hash_file: Callable[[Path], str] | None = None,
    require_complete: bool = False,
    require_hashes: bool = False,
    discover: bool = False,
    required_hash_roles: set[str] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, relative-path registry for the full render chain.

    ``root`` is the default ``source`` scope.  A record can set ``root`` (or
    ``scope``) to another name present in ``roots``; this lets publication
    audit source inputs and promoted outputs without leaking absolute paths.
    Explicit input hashes are checked when provided, while every record always
    receives a freshly calculated SHA-256.
    """
    default_root = Path(root).expanduser().resolve()
    scope_roots = {"source": default_root}
    if roots:
        scope_roots.update({str(name): Path(value).expanduser().resolve() for name, value in roots.items()})
    hasher = hash_file or _sha256
    spec_map = _registry_specs(artifacts)
    if discover:
        spec_map = _discover_provenance_specs(scope_roots, spec_map)
    if require_hashes:
        roles_to_check = required_hash_roles or set(spec_map)
        missing_hashes = sorted(
            role
            for role, spec in spec_map.items()
            if role in roles_to_check
            if isinstance(spec, Mapping) and "sha256" not in spec
        )
        missing_hashes.extend(
            sorted(
                role
                for role, spec in spec_map.items()
                if role in roles_to_check and not isinstance(spec, Mapping)
            )
        )
        if missing_hashes:
            raise ValueError(
                "provenance explícito exige SHA-256 para: "
                + ", ".join(missing_hashes)
            )
    records: dict[str, dict[str, Any]] = {}
    for role, spec in spec_map.items():
        if role == "config":
            continue
        records[role] = _registry_record(scope_roots, role, spec, hash_file=hasher)

    # Keep the file digest even when callers also provide the normalized
    # config object for its semantic fingerprint.  This avoids replacing a
    # real config-file hash with a hash of an in-memory copy.
    config_spec = spec_map.get("config")
    if config_spec is not None:
        config_file_spec = (
            {"path": config_spec, "category": "config"}
            if not isinstance(config_spec, Mapping)
            else config_spec
        )
        records["config_file"] = _registry_record(
            scope_roots,
            "config_file",
            config_file_spec,
            hash_file=hasher,
        )

    # A compositor always emits the preview at this canonical path.  Include
    # it even when callers provide only the input-chain records so the
    # registry can prove the final visual output was part of the chain.
    if discover and "preview" not in records:
        for scope, scope_root in scope_roots.items():
            candidate = scope_root / PUBLISHED_ARTIFACTS["preview"]
            if candidate.is_file():
                records["preview"] = _registry_record(
                    scope_roots,
                    "preview",
                    {
                        "path": PUBLISHED_ARTIFACTS["preview"],
                        "root": scope,
                        "category": "preview",
                    },
                    hash_file=hasher,
                )
                break

    config_record = _registry_config_record(
        scope_roots,
        config if config is not None else config_spec,
        hash_file=hasher,
    )

    manifest_record = None
    if discover and manifest is None:
        for scope, scope_root in scope_roots.items():
            candidate = scope_root / "layered_sprite_bundle.json"
            if candidate.is_file():
                manifest = {"path": candidate.name, "root": scope}
                break
    if manifest is not None:
        manifest_spec = manifest if isinstance(manifest, Mapping) else {"path": manifest, "root": "source"}
        manifest_record = _registry_record(
            scope_roots,
            "manifest",
            {**manifest_spec, "category": "manifest"},
            hash_file=hasher,
        )

    registry: dict[str, Any] = {
        "schema": ARTIFACT_HASH_REGISTRY_SCHEMA,
        "artifacts": records,
        "config": config_record,
        "config_fingerprint": config_record["fingerprint"],
    }
    if manifest_record is not None:
        registry["manifest"] = manifest_record

    hash_map = {
        _registry_hash_key(record): record["sha256"]
        for record in records.values()
    }
    if manifest_record is not None:
        hash_map[_registry_hash_key(manifest_record)] = manifest_record["sha256"]
    if "sha256" in config_record:
        hash_map[_registry_hash_key(config_record)] = config_record["sha256"]
    registry["hashes"] = dict(sorted(hash_map.items()))
    registry["complete"] = _provenance_complete(registry)
    fingerprint_payload = {
        "artifacts": registry["artifacts"],
        "config": registry["config"],
        "manifest": registry.get("manifest"),
        "hashes": registry["hashes"],
    }
    registry["fingerprint"] = hashlib.sha256(
        _canonical_json(fingerprint_payload).encode("utf-8")
    ).hexdigest()
    if require_complete and not registry["complete"]:
        missing = sorted(REQUIRED_PROVENANCE_CATEGORIES - {
            record.get("category") for record in records.values()
        })
        if "manifest" in registry:
            missing = [item for item in missing if item != "manifest"]
        if "config" in registry:
            missing = [item for item in missing if item != "config"]
        raise ValueError(
            "registro de proveniência incompleto; categorias ausentes: "
            + ", ".join(missing)
        )
    return registry


def validate_artifact_hash_registry(
    registry: Any,
    root: Path | Mapping[str, Path],
    *,
    scopes: set[str] | None = None,
    hash_file: Callable[[Path], str] | None = None,
) -> dict[str, Any]:
    """Validate registry structure and file hashes before publication/use."""
    if not isinstance(registry, dict):
        raise ValueError("registro de proveniência deve ser um objeto JSON")
    if registry.get("schema") != ARTIFACT_HASH_REGISTRY_SCHEMA:
        raise ValueError(f"schema do registro inválido: {registry.get('schema')}")
    allowed_top = {
        "schema",
        "artifacts",
        "config",
        "config_fingerprint",
        "manifest",
        "hashes",
        "complete",
        "fingerprint",
    }
    extras = sorted(set(registry) - allowed_top)
    if extras:
        raise ValueError("registro contém propriedades adicionais não permitidas: " + ", ".join(extras))
    artifacts = registry.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("registro.artifacts deve ser um objeto")
    roots = (
        {"source": Path(root).expanduser().resolve()}
        if isinstance(root, (str, Path))
        else {str(name): Path(value).expanduser().resolve() for name, value in root.items()}
    )
    hasher = hash_file or _sha256
    all_records: list[tuple[str, Mapping[str, Any]]] = []
    allowed_record = {"category", "scope", "path", "bytes", "sha256"}
    for role, record in artifacts.items():
        if not isinstance(record, dict):
            raise ValueError(f"artifacts[{role}] deve ser um objeto")
        extras = sorted(set(record) - allowed_record)
        if extras:
            raise ValueError(f"artifacts[{role}] contém propriedades adicionais não permitidas")
        all_records.append((f"artifacts[{role}]", record))
    for field in ("manifest", "config"):
        record = registry.get(field)
        if field == "config" and isinstance(record, dict) and "path" not in record:
            extras = sorted(set(record) - {"category", "fingerprint"})
            if extras:
                raise ValueError("config contém propriedades adicionais não permitidas")
            if record.get("category") != "config":
                raise ValueError("config.category inválida")
            if not isinstance(record.get("fingerprint"), str) or not SHA256_PATTERN.fullmatch(record["fingerprint"]):
                raise ValueError("config.fingerprint deve ser um SHA-256 hexadecimal")
            continue
        if not isinstance(record, dict):
            raise ValueError(f"registro.{field} deve possuir hash")
        allowed_fields = allowed_record | ({"fingerprint"} if field == "config" else set())
        extras = sorted(set(record) - allowed_fields)
        if extras:
            raise ValueError(f"registro.{field} contém propriedades adicionais não permitidas")
        all_records.append((field, record))

    for field, record in all_records:
        scope = _registry_scope(record.get("scope", "source"), field)
        relative = _portable_path(record.get("path"), f"{field}.path")
        if "\\" in relative:
            raise ValueError(f"{field}.path deve usar separadores POSIX")
        digest = record.get("sha256")
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise ValueError(f"{field}.sha256 deve ser um SHA-256 hexadecimal")
        if field == "config" and "path" in record and record.get("fingerprint") != digest:
            raise ValueError("config.fingerprint deve corresponder ao config.sha256")
        if field == "config" and record.get("category") != "config":
            raise ValueError("config.category inválida")
        if field != "config" and (
            not isinstance(record.get("category"), str)
            or record.get("category") not in PROVENANCE_CATEGORIES
        ):
            raise ValueError(f"{field}.category inválida")
        if type(record.get("bytes")) is not int or record["bytes"] < 0:
            raise ValueError(f"{field}.bytes deve ser um inteiro não negativo")
        known_scopes = {"source", "published"} | set(roots)
        if scope not in known_scopes:
            raise ValueError(f"{field} usa raiz/escopo desconhecido: {scope}")
        if scopes is not None and scope not in scopes:
            continue
        if scope not in roots:
            raise ValueError(f"{field} usa raiz desconhecida para o escopo: {scope}")
        path = (roots[scope] / relative).resolve()
        if not path.is_relative_to(roots[scope]):
            raise ValueError(f"{field}.path deve permanecer na raiz do escopo")
        if not path.is_file():
            raise ValueError(f"{field}.path não existe: {relative}")
        actual = str(hasher(path)).strip().lower()
        if actual != digest:
            raise ValueError(f"{field}.path hash não corresponde ao arquivo")
        if record.get("bytes") != path.stat().st_size:
            raise ValueError(f"{field}.bytes não corresponde ao arquivo")
    hashes = registry.get("hashes")
    if not isinstance(hashes, dict):
        raise ValueError("registro.hashes deve ser um objeto")
    for key, digest in hashes.items():
        if not isinstance(key, str) or not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise ValueError("registro.hashes contém hash inválido")
    expected_hash_keys = {
        _registry_hash_key(record)
        for _field, record in all_records
    }
    if set(hashes) != expected_hash_keys:
        raise ValueError("registro.hashes deve cobrir exatamente os registros")
    expected_record_hashes = [
        (_registry_hash_key(record), record["sha256"])
        for _field, record in all_records
    ]
    for key, digest in expected_record_hashes:
        if hashes.get(key) != digest:
            raise ValueError(f"registro.hashes[{key}] ausente ou divergente")
    config = registry.get("config")
    if not isinstance(config, dict) or registry.get("config_fingerprint") != config.get("fingerprint"):
        raise ValueError("config_fingerprint não corresponde à configuração registrada")
    expected_fingerprint = hashlib.sha256(
        _canonical_json({
            "artifacts": artifacts,
            "config": registry.get("config"),
            "manifest": registry.get("manifest"),
            "hashes": hashes,
        }).encode("utf-8")
    ).hexdigest()
    if registry.get("fingerprint") != expected_fingerprint:
        raise ValueError("fingerprint do registro não corresponde ao conteúdo")
    if registry.get("complete") is not _provenance_complete(registry):
        raise ValueError("complete do registro não corresponde às categorias")
    return registry


def _reject_additional_properties(
    value: dict[str, Any],
    allowed: set[str],
    field: str,
) -> None:
    extras = sorted(set(value) - allowed)
    if extras:
        raise ValueError(
            f"{field} contém propriedades adicionais não permitidas: "
            + ", ".join(extras)
        )


def _portable_path(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} deve ser uma string")
    path = value.strip()
    windows_path = PureWindowsPath(path)
    posix_path = PurePosixPath(path)
    if not path:
        raise ValueError(f"{field} é obrigatório")
    if "\\" in path:
        raise ValueError(
            f"{field} deve ser um path relativo POSIX sem separadores Windows"
        )
    if windows_path.drive:
        raise ValueError(
            f"{field} deve ser um path relativo POSIX sem caminhos drive-relative"
        )
    # ``PureWindowsPath.is_absolute`` is false for a single leading ``\\``:
    # that spelling is root-relative on Windows and would discard the bundle
    # drive/root when joined.  It must be treated as absolute for this
    # portable manifest contract, alongside drive and UNC paths.
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.root:
        raise ValueError(f"{field} deve ser um path relativo")
    if ".." in posix_path.parts or ".." in windows_path.parts:
        raise ValueError(f"{field} deve permanecer dentro da raiz do bundle")
    return posix_path.as_posix()


def _source_path(root: Path, value: Path | str, field: str) -> tuple[Path, str]:
    root_resolved = root.expanduser().resolve()
    source = Path(value).expanduser()
    if not source.is_absolute():
        source = root_resolved / source
    source = source.resolve()
    try:
        relative = source.relative_to(root_resolved).as_posix()
    except ValueError:
        raise ValueError(f"{field} deve permanecer dentro da raiz do bundle") from None
    return source, relative


def _png_size(path: Path, field: str) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError(f"{field} deve apontar para um PNG")
            return image.size
    except (OSError, ValueError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"{field} não é um PNG válido: {exc}") from None


def _artifact_paths(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    layers = manifest["layers"]
    return [
        ("layers[0].file", layers[0]["file"]),
        ("layers[1].file", layers[1]["file"]),
        ("layers[1].holdout_source", layers[1]["holdout_source"]),
        ("preview", manifest["preview"]),
    ]


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} deve ser numérico")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} deve ser finito")
    return number


def _validate_point_v2(value: Any, field: str, frame_size: list[int]) -> list[float | int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"runtime.{field} deve conter duas coordenadas")
    point = [_finite_number(item, f"runtime.{field}[{index}]") for index, item in enumerate(value)]
    if not 0 <= point[0] <= frame_size[0] or not 0 <= point[1] <= frame_size[1]:
        raise ValueError(f"runtime.{field} deve permanecer dentro do frame")
    return value


def _validate_runtime_v2(runtime: Any, *, rows: int, columns: int, cell_size: list[int]) -> dict[str, Any]:
    if not isinstance(runtime, dict):
        raise ValueError("runtime deve ser um objeto")
    _reject_additional_properties(runtime, V2_RUNTIME_FIELDS, "runtime")
    if runtime.get("coordinate_space") != "cell_pixels_top_left":
        raise ValueError("runtime.coordinate_space deve ser cell_pixels_top_left")
    frame_size = runtime.get("frame_size")
    if frame_size != cell_size:
        raise ValueError("runtime.frame_size deve corresponder a layout.cell_size")
    fps = _finite_number(runtime.get("fps"), "runtime.fps")
    if fps <= 0 or fps > 240:
        raise ValueError("runtime.fps deve estar no intervalo (0, 240]")

    actions = runtime.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("runtime.actions deve conter ao menos uma ação")
    action_ids: set[str] = set()
    claimed_columns: set[int] = set()
    for index, action in enumerate(actions):
        field = f"runtime.actions[{index}]"
        if not isinstance(action, dict):
            raise ValueError(f"{field} deve ser um objeto")
        _reject_additional_properties(action, V2_ACTION_FIELDS, field)
        action_id = action.get("id")
        if not isinstance(action_id, str) or not action_id.strip():
            raise ValueError(f"{field}.id é obrigatório")
        if action_id in action_ids:
            raise ValueError("runtime.actions.id deve ser único")
        action_ids.add(action_id)
        start = action.get("start_column")
        count = action.get("frame_count")
        if type(start) is not int or type(count) is not int or start < 1 or count < 1:
            raise ValueError(f"{field} exige start_column e frame_count inteiros positivos")
        action_columns = set(range(start, start + count))
        if max(action_columns) > columns:
            raise ValueError(f"{field} ultrapassa as colunas do atlas")
        if action_columns & claimed_columns:
            raise ValueError("runtime.actions não pode sobrepor colunas")
        claimed_columns.update(action_columns)
        if type(action.get("loop")) is not bool:
            raise ValueError(f"{field}.loop deve ser booleano")

    directions = runtime.get("directions")
    if not isinstance(directions, list) or len(directions) != rows:
        raise ValueError("runtime.directions deve declarar exatamente uma direção por linha")
    direction_ids: set[str] = set()
    direction_rows: set[int] = set()
    for index, direction in enumerate(directions):
        field = f"runtime.directions[{index}]"
        if not isinstance(direction, dict):
            raise ValueError(f"{field} deve ser um objeto")
        _reject_additional_properties(direction, V2_DIRECTION_FIELDS, field)
        direction_id = direction.get("id")
        row = direction.get("row")
        vector = direction.get("vector")
        if not isinstance(direction_id, str) or not direction_id.strip():
            raise ValueError(f"{field}.id é obrigatório")
        if direction_id in direction_ids:
            raise ValueError("runtime.directions.id deve ser único")
        if type(row) is not int or not 1 <= row <= rows:
            raise ValueError(f"{field}.row deve identificar uma linha válida")
        if row in direction_rows:
            raise ValueError("runtime.directions.row deve ser único")
        if not isinstance(vector, list) or len(vector) != 2:
            raise ValueError(f"{field}.vector deve conter duas coordenadas")
        for vector_index, item in enumerate(vector):
            _finite_number(item, f"{field}.vector[{vector_index}]")
        direction_ids.add(direction_id)
        direction_rows.add(row)
    if direction_rows != set(range(1, rows + 1)):
        raise ValueError("runtime.directions deve cobrir todas as linhas")

    _validate_point_v2(runtime.get("pivot"), "pivot", frame_size)
    _validate_point_v2(runtime.get("foot_anchor"), "foot_anchor", frame_size)
    return runtime


def _artifact_paths_v2(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for index, layer in enumerate(manifest["layers"]):
        result.append((f"layers[{index}].file", layer["file"]))
        if layer.get("role") == "component":
            result.append(
                (
                    f"layers[{index}].occlusion.visible_mask",
                    layer["occlusion"]["visible_mask"],
                )
            )
    result.append(("preview", manifest["preview"]))
    return result


def validate_layered_bundle_v2(
    manifest: Any,
    root: Path | None = None,
) -> dict[str, Any]:
    """Validate a modular bundle with one immutable base and N visible layers."""
    if not isinstance(manifest, dict):
        raise ValueError("manifest deve ser um objeto JSON")
    _reject_additional_properties(manifest, V2_MANIFEST_FIELDS, "manifest")
    if manifest.get("schema") != LAYERED_BUNDLE_SCHEMA_V2:
        raise ValueError(f"schema inválido: {manifest.get('schema')}")
    if manifest.get("generation_mode") != GENERATION_MODE_V2:
        raise ValueError(f"generation_mode inválido: {manifest.get('generation_mode')}")

    layout = manifest.get("layout")
    if not isinstance(layout, dict):
        raise ValueError("layout deve ser um objeto")
    _reject_additional_properties(layout, V2_LAYOUT_FIELDS, "layout")
    rows = layout.get("rows")
    columns = layout.get("columns")
    cell_size = layout.get("cell_size")
    if type(rows) is not int or type(columns) is not int or rows < 1 or columns < 1:
        raise ValueError("layout.rows e layout.columns devem ser inteiros positivos")
    if (
        not isinstance(cell_size, list)
        or len(cell_size) != 2
        or any(type(value) is not int or value <= 0 for value in cell_size)
    ):
        raise ValueError("layout.cell_size deve conter dois inteiros positivos")
    _validate_runtime_v2(
        manifest.get("runtime"), rows=rows, columns=columns, cell_size=cell_size
    )

    layers = manifest.get("layers")
    if not isinstance(layers, list) or len(layers) < 2:
        raise ValueError("layers deve declarar uma base e ao menos um componente")
    if any(not isinstance(layer, dict) for layer in layers):
        raise ValueError("cada item de layers deve ser um objeto")
    ids: list[str] = []
    z_values: list[int] = []
    for index, layer in enumerate(layers):
        field = f"layers[{index}]"
        role = layer.get("role")
        allowed = V2_BASE_LAYER_FIELDS if index == 0 else V2_COMPONENT_LAYER_FIELDS
        _reject_additional_properties(layer, allowed, field)
        expected_role = "base" if index == 0 else "component"
        if role != expected_role:
            raise ValueError(f"{field}.role deve ser {expected_role}")
        layer_id = layer.get("id")
        if not isinstance(layer_id, str) or not layer_id.strip():
            raise ValueError(f"{field}.id é obrigatório")
        if layer_id in ids:
            raise ValueError("layers.id deve ser único")
        ids.append(layer_id)
        z = layer.get("z")
        if type(z) is not int:
            raise ValueError(f"{field}.z deve ser inteiro")
        z_values.append(z)
        if layer.get("blend_mode") != "alpha_over":
            raise ValueError(f"{field}.blend_mode deve ser alpha_over")
        layer["file"] = _portable_path(layer.get("file"), f"{field}.file")
        if index == 0:
            if layer.get("immutable") is not True:
                raise ValueError("layers[0].immutable deve ser true")
            continue
        kind = layer.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError(f"{field}.kind é obrigatório")
        occlusion = layer.get("occlusion")
        if not isinstance(occlusion, dict):
            raise ValueError(f"{field}.occlusion deve ser um objeto")
        _reject_additional_properties(occlusion, V2_OCCLUSION_FIELDS, f"{field}.occlusion")
        if occlusion.get("mode") != "baked_visible":
            raise ValueError(f"{field}.occlusion.mode deve ser baked_visible")
        occlusion["visible_mask"] = _portable_path(
            occlusion.get("visible_mask"), f"{field}.occlusion.visible_mask"
        )
        occluders = occlusion.get("occluders")
        if (
            not isinstance(occluders, list)
            or not occluders
            or any(not isinstance(item, str) or not item.strip() for item in occluders)
            or len(set(occluders)) != len(occluders)
        ):
            raise ValueError(f"{field}.occlusion.occluders deve conter ids únicos")

    if len(set(z_values)) != len(z_values) or z_values != sorted(z_values):
        raise ValueError("layers.z deve ser único e estar em ordem crescente")
    known_ids = set(ids)
    for index, layer in enumerate(layers[1:], start=1):
        occluders = layer["occlusion"]["occluders"]
        unknown = sorted(set(occluders) - known_ids)
        if unknown or layer["id"] in occluders:
            raise ValueError(
                f"layers[{index}].occlusion.occluders deve referenciar outras layers válidas"
            )

    manifest["preview"] = _portable_path(manifest.get("preview"), "preview")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise ValueError("source deve ser um objeto")
    try:
        json.dumps(source, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"source deve conter apenas valores JSON: {exc}") from None

    hashes = manifest.get("hashes")
    if not isinstance(hashes, dict):
        raise ValueError("hashes deve ser um objeto")
    artifact_paths = _artifact_paths_v2(manifest)
    expected_paths = [path for _field, path in artifact_paths]
    if len(set(expected_paths)) != len(expected_paths):
        raise ValueError("todos os artefatos v2 devem usar paths únicos")
    if set(hashes) != set(expected_paths):
        raise ValueError("hashes deve cobrir exatamente todos os arquivos do bundle")
    for path in expected_paths:
        digest = hashes.get(path)
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise ValueError(f"hashes[{path}] deve ser um SHA-256 hexadecimal")

    if root is not None:
        root_resolved = root.expanduser().resolve()
        expected_size = (cell_size[0] * columns, cell_size[1] * rows)
        for field, relative in artifact_paths:
            path = (root_resolved / relative).resolve()
            if not path.is_relative_to(root_resolved):
                raise ValueError(f"{field} deve permanecer dentro da raiz do bundle")
            if not path.is_file():
                raise ValueError(f"{field} não existe: {relative}")
            if _png_size(path, field) != expected_size:
                raise ValueError(
                    f"{field} possui dimensões diferentes do layout {expected_size}"
                )
            if _sha256(path) != hashes[relative]:
                raise ValueError(f"hashes[{relative}] não corresponde ao arquivo")
    return manifest


def validate_layered_bundle(
    manifest: Any,
    root: Path | None = None,
) -> dict[str, Any]:
    """Validate a v1 or v2 bundle and optionally verify its files and hashes."""
    if not isinstance(manifest, dict):
        raise ValueError("manifest deve ser um objeto JSON")
    if manifest.get("schema") == LAYERED_BUNDLE_SCHEMA_V2:
        return validate_layered_bundle_v2(manifest, root)
    _reject_additional_properties(manifest, MANIFEST_FIELDS, "manifest")
    if manifest.get("schema") != LAYERED_BUNDLE_SCHEMA:
        raise ValueError(f"schema inválido: {manifest.get('schema')}")
    if manifest.get("generation_mode") != GENERATION_MODE:
        raise ValueError(f"generation_mode inválido: {manifest.get('generation_mode')}")

    layout = manifest.get("layout")
    if not isinstance(layout, dict):
        raise ValueError("layout deve ser um objeto")
    _reject_additional_properties(layout, LAYOUT_FIELDS, "layout")
    if layout.get("rows") != GRID_ROWS or layout.get("columns") != GRID_COLUMNS:
        raise ValueError("layout deve declarar uma grade 8x8")
    cell_size = layout.get("cell_size")
    if (
        not isinstance(cell_size, list)
        or len(cell_size) != 2
        or any(type(value) is not int or value <= 0 for value in cell_size)
    ):
        raise ValueError("layout.cell_size deve conter dois inteiros positivos")

    layers = manifest.get("layers")
    if not isinstance(layers, list) or len(layers) != len(EXPECTED_LAYERS):
        raise ValueError("layers deve declarar weapon e character_holdout")
    if any(not isinstance(layer, dict) for layer in layers):
        raise ValueError("cada item de layers deve ser um objeto")
    for index, layer in enumerate(layers):
        _reject_additional_properties(layer, LAYER_FIELDS[index], f"layers[{index}]")
    layer_ids = [layer.get("id") for layer in layers if isinstance(layer, dict)]
    if layer_ids != [layer_id for layer_id, _ in EXPECTED_LAYERS]:
        raise ValueError("layers deve declarar weapon e character_holdout nessa ordem")
    z_values = [layer.get("z") for layer in layers]
    if any(type(value) is not int for value in z_values) or len(set(z_values)) != len(z_values):
        raise ValueError("layers.z deve ser inteiro e único")
    if z_values != [z for _, z in EXPECTED_LAYERS]:
        raise ValueError("layers deve usar z 0 para weapon e z 1 para character_holdout")

    layers[0]["file"] = _portable_path(layers[0].get("file"), "layers[0].file")
    layers[1]["file"] = _portable_path(layers[1].get("file"), "layers[1].file")
    layers[1]["holdout_source"] = _portable_path(
        layers[1].get("holdout_source"), "layers[1].holdout_source"
    )
    manifest["preview"] = _portable_path(manifest.get("preview"), "preview")

    source = manifest.get("source")
    if not isinstance(source, dict):
        raise ValueError("source deve ser um objeto")
    try:
        json.dumps(source, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"source deve conter apenas valores JSON: {exc}") from None

    hashes = manifest.get("hashes")
    if not isinstance(hashes, dict):
        raise ValueError("hashes deve ser um objeto")
    expected_paths = [path for _, path in _artifact_paths(manifest)]
    if len(set(expected_paths)) != 4:
        raise ValueError("os quatro artefatos devem usar paths únicos")
    if set(hashes) != set(expected_paths):
        raise ValueError("hashes deve cobrir exatamente todos os arquivos do bundle")
    for path in expected_paths:
        digest = hashes.get(path)
        if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
            raise ValueError(f"hashes[{path}] deve ser um SHA-256 hexadecimal")

    if root is not None:
        root_resolved = root.expanduser().resolve()
        expected_size = (
            cell_size[0] * GRID_COLUMNS,
            cell_size[1] * GRID_ROWS,
        )
        for field, relative in _artifact_paths(manifest):
            path = (root_resolved / relative).resolve()
            if not path.is_relative_to(root_resolved):
                raise ValueError(f"{field} deve permanecer dentro da raiz do bundle")
            if not path.is_file():
                raise ValueError(f"{field} não existe: {relative}")
            if _png_size(path, field) != expected_size:
                raise ValueError(
                    f"{field} possui dimensões diferentes do layout {expected_size}"
                )
            if _sha256(path) != hashes[relative]:
                raise ValueError(f"hashes[{relative}] não corresponde ao arquivo")
    return manifest


def build_layered_bundle(
    root: Path,
    *,
    character_holdout: Path | str,
    weapon: Path | str,
    holdout_source: Path | str,
    preview: Path | str,
    source: dict[str, Any],
) -> dict[str, Any]:
    """Build a v1 layered manifest after every required PNG exists."""
    root_resolved = root.expanduser().resolve()
    entries = (
        ("character_holdout", character_holdout),
        ("weapon", weapon),
        ("holdout_source", holdout_source),
        ("preview", preview),
    )
    resolved: dict[str, Path] = {}
    relative: dict[str, str] = {}
    for field, value in entries:
        path, portable = _source_path(root_resolved, value, field)
        if not path.is_file():
            raise ValueError(f"{field} não existe: {portable}")
        resolved[field] = path
        relative[field] = portable

    if len(set(relative.values())) != len(entries):
        raise ValueError("os quatro artefatos devem usar paths únicos")

    dimensions = {field: _png_size(path, field) for field, path in resolved.items()}
    unique_dimensions = set(dimensions.values())
    if len(unique_dimensions) != 1:
        detail = ", ".join(f"{field}={size}" for field, size in dimensions.items())
        raise ValueError(f"os PNGs possuem dimensões diferentes: {detail}")
    width, height = unique_dimensions.pop()
    if width % GRID_COLUMNS or height % GRID_ROWS:
        raise ValueError("as dimensões dos PNGs devem ser divisíveis pela grade 8x8")

    hashes = {
        relative[field]: _sha256(resolved[field])
        for field, _ in entries
    }
    manifest = {
        "schema": LAYERED_BUNDLE_SCHEMA,
        "generation_mode": GENERATION_MODE,
        "layout": {
            "rows": GRID_ROWS,
            "columns": GRID_COLUMNS,
            "cell_size": [width // GRID_COLUMNS, height // GRID_ROWS],
        },
        "layers": [
            {"id": "weapon", "file": relative["weapon"], "z": 0},
            {
                "id": "character_holdout",
                "file": relative["character_holdout"],
                "z": 1,
                "holdout_source": relative["holdout_source"],
            },
        ],
        "preview": relative["preview"],
        "source": copy.deepcopy(source),
        "hashes": hashes,
    }
    return validate_layered_bundle(manifest, root_resolved)


def default_layered_runtime(
    cell_size: Sequence[int],
    *,
    rows: int = GRID_ROWS,
    columns: int = GRID_COLUMNS,
    fps: float = 12.0,
    action_id: str = "default",
    loop: bool = True,
    directions: Sequence[Mapping[str, Any]] | None = None,
    pivot: Sequence[int | float] | None = None,
    foot_anchor: Sequence[int | float] | None = None,
) -> dict[str, Any]:
    """Create explicit runtime metadata for a row-by-direction sprite atlas."""
    size = list(cell_size)
    if len(size) != 2 or any(type(value) is not int or value <= 0 for value in size):
        raise ValueError("cell_size deve conter dois inteiros positivos")
    if type(rows) is not int or type(columns) is not int or rows < 1 or columns < 1:
        raise ValueError("rows e columns devem ser inteiros positivos")
    if directions is None:
        canonical = (
            ("north", [0, 1]),
            ("north_east", [1, 1]),
            ("east", [1, 0]),
            ("south_east", [1, -1]),
            ("south", [0, -1]),
            ("south_west", [-1, -1]),
            ("west", [-1, 0]),
            ("north_west", [-1, 1]),
        )
        direction_values = [
            {
                "id": canonical[index][0] if rows == 8 else f"direction_{index + 1}",
                "row": index + 1,
                "vector": canonical[index][1] if rows == 8 else [0, 0],
            }
            for index in range(rows)
        ]
    else:
        direction_values = [copy.deepcopy(dict(value)) for value in directions]
    runtime = {
        "actions": [
            {
                "id": str(action_id).strip(),
                "start_column": 1,
                "frame_count": columns,
                "loop": loop,
            }
        ],
        "fps": fps,
        "directions": direction_values,
        "frame_size": size,
        "coordinate_space": "cell_pixels_top_left",
        "pivot": list(pivot) if pivot is not None else [size[0] / 2, size[1]],
        "foot_anchor": (
            list(foot_anchor)
            if foot_anchor is not None
            else [size[0] / 2, size[1]]
        ),
    }
    return _validate_runtime_v2(runtime, rows=rows, columns=columns, cell_size=size)


def build_layered_bundle_v2(
    root: Path,
    *,
    base: Mapping[str, Any],
    components: Sequence[Mapping[str, Any]],
    preview: Path | str,
    source: dict[str, Any],
    runtime: Mapping[str, Any] | None = None,
    rows: int = GRID_ROWS,
    columns: int = GRID_COLUMNS,
) -> dict[str, Any]:
    """Build a modular manifest from an immutable base and visible components.

    Each component ``file`` contains only pixels that remain visible after
    holdout.  ``visible_mask`` records that visibility decision independently,
    so consumers never need to cut or mutate the base character at runtime.
    """
    root_resolved = Path(root).expanduser().resolve()
    if not isinstance(base, Mapping):
        raise ValueError("base deve ser um objeto")
    if not isinstance(components, Sequence) or isinstance(components, (str, bytes)) or not components:
        raise ValueError("components deve conter ao menos um componente")
    if any(not isinstance(component, Mapping) for component in components):
        raise ValueError("cada component deve ser um objeto")

    base_id = str(base.get("id") or "").strip()
    if not base_id:
        raise ValueError("base.id é obrigatório")
    base_file = base.get("file")
    if base_file is None:
        raise ValueError("base.file é obrigatório")
    base_path, base_relative = _source_path(root_resolved, base_file, "base.file")
    entries: list[tuple[str, Path, str]] = [("base.file", base_path, base_relative)]
    component_layers: list[dict[str, Any]] = []
    for index, component in enumerate(components):
        field = f"components[{index}]"
        component_id = str(component.get("id") or "").strip()
        kind = str(component.get("kind") or component.get("role") or "").strip()
        if not component_id:
            raise ValueError(f"{field}.id é obrigatório")
        if not kind:
            raise ValueError(f"{field}.kind é obrigatório")
        if component.get("file") is None:
            raise ValueError(f"{field}.file é obrigatório")
        visible_mask = component.get("visible_mask")
        if visible_mask is None and isinstance(component.get("occlusion"), Mapping):
            visible_mask = component["occlusion"].get("visible_mask")
        if visible_mask is None:
            raise ValueError(f"{field}.visible_mask é obrigatório")
        component_path, component_relative = _source_path(
            root_resolved, component["file"], f"{field}.file"
        )
        mask_path, mask_relative = _source_path(
            root_resolved, visible_mask, f"{field}.visible_mask"
        )
        entries.extend(
            (
                (f"{field}.file", component_path, component_relative),
                (f"{field}.visible_mask", mask_path, mask_relative),
            )
        )
        occluders = component.get("occluders")
        if occluders is None and isinstance(component.get("occlusion"), Mapping):
            occluders = component["occlusion"].get("occluders")
        if occluders is None:
            occluders = [base_id]
        component_layers.append(
            {
                "id": component_id,
                "role": "component",
                "kind": kind,
                "file": component_relative,
                "z": component.get("z"),
                "blend_mode": "alpha_over",
                "occlusion": {
                    "mode": "baked_visible",
                    "visible_mask": mask_relative,
                    "occluders": copy.deepcopy(occluders),
                },
            }
        )
    preview_path, preview_relative = _source_path(root_resolved, preview, "preview")
    entries.append(("preview", preview_path, preview_relative))

    for field, path, relative in entries:
        if not path.is_file():
            raise ValueError(f"{field} não existe: {relative}")
    relative_paths = [relative for _field, _path, relative in entries]
    if len(set(relative_paths)) != len(relative_paths):
        raise ValueError("todos os artefatos v2 devem usar paths únicos")
    dimensions = {field: _png_size(path, field) for field, path, _relative in entries}
    if len(set(dimensions.values())) != 1:
        detail = ", ".join(f"{field}={size}" for field, size in dimensions.items())
        raise ValueError(f"os PNGs possuem dimensões diferentes: {detail}")
    width, height = next(iter(dimensions.values()))
    if type(rows) is not int or type(columns) is not int or rows < 1 or columns < 1:
        raise ValueError("rows e columns devem ser inteiros positivos")
    if width % columns or height % rows:
        raise ValueError("as dimensões dos PNGs devem ser divisíveis pela grade")
    cell_size = [width // columns, height // rows]

    runtime_value = (
        copy.deepcopy(dict(runtime))
        if isinstance(runtime, Mapping)
        else default_layered_runtime(cell_size, rows=rows, columns=columns)
    )
    base_z = base.get("z", 0)
    manifest = {
        "schema": LAYERED_BUNDLE_SCHEMA_V2,
        "generation_mode": GENERATION_MODE_V2,
        "layout": {"rows": rows, "columns": columns, "cell_size": cell_size},
        "runtime": runtime_value,
        "layers": [
            {
                "id": base_id,
                "role": "base",
                "file": base_relative,
                "z": base_z,
                "immutable": True,
                "blend_mode": "alpha_over",
            },
            *component_layers,
        ],
        "preview": preview_relative,
        "source": copy.deepcopy(source),
        "hashes": {
            relative: _sha256(path) for _field, path, relative in entries
        },
    }
    return validate_layered_bundle_v2(manifest, root_resolved)


# More descriptive aliases for new consumers.
build_modular_layered_bundle = build_layered_bundle_v2
validate_modular_layered_bundle = validate_layered_bundle_v2


def _publication_source_path(
    root: Path,
    value: Path | str,
    field: str,
    expected_name: str,
) -> Path:
    """Resolve one source artifact while keeping the published contract fixed."""
    if not isinstance(value, (str, Path)):
        raise ValueError(f"{field} deve ser um path relativo")
    raw = value.as_posix() if isinstance(value, Path) else value
    relative = _portable_path(raw, field)
    if PurePosixPath(relative).name != expected_name:
        raise ValueError(
            f"{field} deve usar o nome {expected_name!r} no bundle publicado"
        )
    source = (root / relative).resolve()
    if not source.is_relative_to(root):
        raise ValueError(f"{field} deve permanecer dentro da raiz de publicação")
    if not source.is_file():
        raise ValueError(f"{field} não existe: {relative}")
    return source


def publish_layered_bundle(
    source_root: Path,
    destination: Path,
    *,
    character_holdout: Path | str = PUBLISHED_ARTIFACTS["character_holdout"],
    weapon: Path | str = PUBLISHED_ARTIFACTS["weapon"],
    holdout_source: Path | str = PUBLISHED_ARTIFACTS["holdout_source"],
    preview: Path | str = PUBLISHED_ARTIFACTS["preview"],
    source: dict[str, Any] | None = None,
    provenance: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    config: Any = None,
    hash_file: Callable[[Path], str] | None = None,
) -> dict[str, Any]:
    """Publish a validated layered bundle with an atomic directory promotion.

    ``source_root`` is normally a job's ``work/`` directory.  Inputs are
    copied to canonical filenames in a sibling staging directory, validated
    there, and only then renamed into ``destination``.  Existing destinations
    are never overwritten, and the source tree is read-only from this API.
    """
    root = Path(source_root).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    hasher = hash_file or _sha256
    if not root.is_dir():
        raise ValueError(f"source_root não existe: {root}")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"bundle publicado já existe: {target}")
    if target == root or target.is_relative_to(root):
        raise ValueError("destination deve ser separado da raiz de trabalho")

    entries = (
        ("character_holdout", character_holdout),
        ("weapon", weapon),
        ("holdout_source", holdout_source),
        ("preview", preview),
    )
    resolved = {
        field: _publication_source_path(root, value, field, PUBLISHED_ARTIFACTS[field])
        for field, value in entries
    }
    if len(set(resolved.values())) != len(entries):
        raise ValueError("os quatro artefatos devem usar paths únicos")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=str(target.parent))
    )
    try:
        for field, _value in entries:
            shutil.copy2(resolved[field], staging / PUBLISHED_ARTIFACTS[field])

        manifest = build_layered_bundle(
            staging,
            character_holdout=PUBLISHED_ARTIFACTS["character_holdout"],
            weapon=PUBLISHED_ARTIFACTS["weapon"],
            holdout_source=PUBLISHED_ARTIFACTS["holdout_source"],
            preview=PUBLISHED_ARTIFACTS["preview"],
            source=copy.deepcopy(source) if source is not None else {},
        )
        manifest_path = staging / "layered_sprite_bundle.json"
        manifest_path.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_layered_bundle(stored, staging)

        # Keep the v1 manifest unchanged for existing consumers.  The
        # sidecar records the broader chain (references/channels/raw and
        # processed outputs/masks/preview/manifest/config), with source and
        # promoted roots explicitly separated and no absolute paths.
        provenance_specs = _registry_specs(provenance)
        explicit_provenance_roles = set(provenance_specs)
        publication_records = {
            "character_holdout_output": {
                "path": PUBLISHED_ARTIFACTS["character_holdout"],
                "root": "published",
                "category": "processed_output",
            },
            "weapon_output": {
                "path": PUBLISHED_ARTIFACTS["weapon"],
                "root": "published",
                "category": "processed_output",
            },
            "holdout_mask_output": {
                "path": PUBLISHED_ARTIFACTS["holdout_source"],
                "root": "published",
                "category": "final_mask",
            },
            "preview_output": {
                "path": PUBLISHED_ARTIFACTS["preview"],
                "root": "published",
                "category": "preview",
            },
        }
        for role, spec in publication_records.items():
            provenance_specs.setdefault(role, spec)
        registry = build_artifact_hash_registry(
            root,
            provenance_specs,
            manifest={
                "path": manifest_path.name,
                "root": "published",
                "category": "manifest",
            },
            config=copy.deepcopy(config) if config is not None else copy.deepcopy(source),
            roots={"source": root, "published": staging},
            hash_file=hasher,
            require_complete=provenance is not None,
            require_hashes=provenance is not None,
            discover=provenance is None,
            required_hash_roles=explicit_provenance_roles,
        )
        registry_path = staging / ARTIFACT_HASH_REGISTRY_FILENAME
        registry_path.write_text(
            json.dumps(
                registry,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        stored_registry = json.loads(registry_path.read_text(encoding="utf-8"))
        validate_artifact_hash_registry(
            stored_registry,
            {"source": root, "published": staging},
            hash_file=hasher,
        )
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"bundle publicado já existe: {target}")
        staging.replace(target)
        return stored
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def publish_layered_bundle_v2(
    source_root: Path,
    destination: Path,
    *,
    base: Mapping[str, Any],
    components: Sequence[Mapping[str, Any]],
    preview: Path | str,
    source: dict[str, Any],
    runtime: Mapping[str, Any],
    rows: int = GRID_ROWS,
    columns: int = GRID_COLUMNS,
    provenance: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    config: Any = None,
    hash_file: Callable[[Path], str] | None = None,
) -> dict[str, Any]:
    """Atomically publish a v2 bundle while preserving portable artifact paths."""
    root = Path(source_root).expanduser().resolve()
    target = Path(destination).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"source_root não existe: {root}")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"bundle publicado já existe: {target}")
    if target == root or target.is_relative_to(root):
        raise ValueError("destination deve ser separado da raiz de trabalho")
    if not isinstance(base, Mapping):
        raise ValueError("base deve ser um objeto")
    if not isinstance(components, Sequence) or isinstance(components, (str, bytes)):
        raise ValueError("components deve ser uma lista")

    base_copy = copy.deepcopy(dict(base))
    component_copies = [copy.deepcopy(dict(component)) for component in components]
    specs: list[tuple[str, Any]] = [("base.file", base_copy.get("file"))]
    for index, component in enumerate(component_copies):
        specs.append((f"components[{index}].file", component.get("file")))
        visible_mask = component.get("visible_mask")
        if visible_mask is None and isinstance(component.get("occlusion"), Mapping):
            visible_mask = component["occlusion"].get("visible_mask")
        specs.append((f"components[{index}].visible_mask", visible_mask))
    specs.append(("preview", preview))

    source_paths: dict[str, Path] = {}
    relative_paths: dict[str, str] = {}
    for field, value in specs:
        raw = value.as_posix() if isinstance(value, Path) else value
        relative = _portable_path(raw, field)
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"{field} deve permanecer dentro da raiz de publicação")
        if not path.is_file():
            raise ValueError(f"{field} não existe: {relative}")
        source_paths[field] = path
        relative_paths[field] = relative
    if len(set(relative_paths.values())) != len(relative_paths):
        raise ValueError("todos os artefatos v2 devem usar paths únicos")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=str(target.parent))
    )
    try:
        for field, path in source_paths.items():
            staged = staging / relative_paths[field]
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, staged)
        manifest = build_layered_bundle_v2(
            staging,
            base=base_copy,
            components=component_copies,
            preview=relative_paths["preview"],
            source=copy.deepcopy(source),
            runtime=copy.deepcopy(dict(runtime)),
            rows=rows,
            columns=columns,
        )
        manifest_path = staging / "layered_sprite_bundle.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        validate_layered_bundle_v2(
            json.loads(manifest_path.read_text(encoding="utf-8")), staging
        )

        provenance_specs = _registry_specs(provenance)
        explicit_roles = set(provenance_specs)
        provenance_specs.setdefault(
            "base_output",
            {
                "path": relative_paths["base.file"],
                "root": "published",
                "category": "processed_output",
            },
        )
        for index, component in enumerate(component_copies):
            provenance_specs.setdefault(
                f"component_{index}_output",
                {
                    "path": relative_paths[f"components[{index}].file"],
                    "root": "published",
                    "category": "processed_output",
                },
            )
            provenance_specs.setdefault(
                f"component_{index}_visible_mask",
                {
                    "path": relative_paths[f"components[{index}].visible_mask"],
                    "root": "published",
                    "category": "final_mask",
                },
            )
        provenance_specs.setdefault(
            "preview_output",
            {
                "path": relative_paths["preview"],
                "root": "published",
                "category": "preview",
            },
        )
        hasher = hash_file or _sha256
        registry = build_artifact_hash_registry(
            root,
            provenance_specs,
            manifest={
                "path": manifest_path.name,
                "root": "published",
                "category": "manifest",
            },
            config=copy.deepcopy(config) if config is not None else copy.deepcopy(source),
            roots={"source": root, "published": staging},
            hash_file=hasher,
            require_complete=False,
            require_hashes=provenance is not None,
            required_hash_roles=explicit_roles,
        )
        registry_path = staging / ARTIFACT_HASH_REGISTRY_FILENAME
        registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
        validate_artifact_hash_registry(
            json.loads(registry_path.read_text(encoding="utf-8")),
            {"source": root, "published": staging},
            hash_file=hasher,
        )
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"bundle publicado já existe: {target}")
        staging.replace(target)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


# Keep a concise alias for callers that refer to the operation as publishing.
publish_bundle = publish_layered_bundle
publish_modular_layered_bundle = publish_layered_bundle_v2
