"""Build an auditable provider request for the isolated weapon pass."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Sequence

from image_generation_provider import GenerationRequest, _create_openai_cell_mask


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _role_item(manifest: list[dict[str, Any]], *roles: str) -> dict[str, Any]:
    wanted = {role.casefold() for role in roles}
    item = next(
        (entry for entry in manifest if str(entry.get("type", "")).casefold() in wanted),
        None,
    )
    if item is None:
        raise ValueError(f"manifest deve conter {next(iter(roles))}")
    return item


def _normalize_openai_reference(
    source: Path,
    destination: Path,
    output_size: int,
) -> tuple[Path, tuple[int, int]]:
    """Contain an arbitrary uploaded reference in the provider canvas.

    OpenAI requires the edit mask to have the same dimensions as its base
    image. The uploaded weapon remains an input/reference with its original
    hash; a separate RGBA canvas is used only for the edit request.
    """
    from PIL import Image, ImageOps

    with Image.open(source) as opened:
        original_size = opened.size
        normalized = Image.new("RGBA", (output_size, output_size), (0, 0, 0, 0))
        contained = ImageOps.contain(
            opened.convert("RGBA"),
            (output_size, output_size),
            method=Image.Resampling.LANCZOS,
        )
        offset = (
            (output_size - contained.width) // 2,
            (output_size - contained.height) // 2,
        )
        normalized.alpha_composite(contained, dest=offset)
        contained.close()
        destination.parent.mkdir(parents=True, exist_ok=True)
        normalized.save(destination, format="PNG")
        normalized.close()
    return destination, original_size


def build_weapon_layer_request(
    *,
    job_id: str,
    prompt: str,
    reference_manifest: list[dict[str, Any]],
    input_images: Sequence[Path],
    output_dir: Path,
    model: str,
    output_size: int,
    source_contract: dict[str, Any],
    supports_editing: bool = False,
) -> GenerationRequest:
    """Map weapon, character and guide references to a reproducible request.

    ``input_images`` remains in manifest order for legacy adapters. The weapon
    visual is the edit/base image, while ``character_full`` is an explicit
    context reference and dependency; this prevents the second pass from
    accidentally being treated as a continuation of the character output.
    """
    if output_size not in (1024, 2048):
        raise ValueError("output_size deve ser 1024 ou 2048")
    if len(reference_manifest) != len(input_images):
        raise ValueError("manifesto e input_images devem ter a mesma cardinalidade")
    indexes = [item.get("index") for item in reference_manifest]
    if indexes != list(range(1, len(reference_manifest) + 1)):
        raise ValueError("ordem do manifesto deve ser contínua a partir de 1")
    paths = tuple(Path(path) for path in input_images)
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)

    weapon_item = _role_item(
        reference_manifest,
        "weapon_reference",
        "weapon_identity",
        "component_reference",
        "component_identity",
    )
    character_item = _role_item(reference_manifest, "character_full", "character")
    weapon_index = reference_manifest.index(weapon_item)
    character_index = reference_manifest.index(character_item)
    weapon_image = paths[weapon_index]
    character_image = paths[character_index]
    references = paths[:weapon_index] + paths[weapon_index + 1 :]

    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_weapon_image = weapon_image
    original_weapon_size: tuple[int, int] | None = None
    mask_path = None
    if supports_editing:
        normalized_weapon_image, original_weapon_size = _normalize_openai_reference(
            weapon_image,
            output_dir / "weapon_reference_canvas.png",
            output_size,
        )
        mask_path = output_dir / "weapon_edit_mask.png"
        _create_openai_cell_mask(normalized_weapon_image, mask_path)
        from PIL import Image

        with Image.open(mask_path) as mask:
            if mask.size != (output_size, output_size):
                raise ValueError("máscara e canvas devem ter dimensões iguais")

    input_hashes = [
        {
            "index": item["index"],
            "type": item.get("type"),
            "path": str(path),
            "sha256": _sha256(path),
        }
        for item, path in zip(reference_manifest, paths)
    ]
    metadata = {
        "output_size": [output_size, output_size],
        "materialize_output": True,
        "reference_manifest": copy.deepcopy(reference_manifest),
        "input_hashes": input_hashes,
        "source_contract": copy.deepcopy(source_contract),
        "character_dependency": {
            "path": str(character_image),
            "sha256": _sha256(character_image),
            "manifest_index": character_item["index"],
            "status": "character_complete",
        },
        "weapon_reference": {
            "path": str(weapon_image),
            "sha256": _sha256(weapon_image),
            "manifest_index": weapon_item["index"],
        },
    }
    if normalized_weapon_image != weapon_image:
        metadata["weapon_reference"].update({
            "original_path": str(weapon_image),
            "original_size": list(original_weapon_size or ()),
            "normalized_path": str(normalized_weapon_image),
            "normalized_size": [output_size, output_size],
            "normalized_sha256": _sha256(normalized_weapon_image),
        })
    return GenerationRequest(
        job_id=job_id,
        prompt=prompt,
        input_images=paths,
        output_path=output_dir / "weapon_full.png",
        model=model,
        metadata=metadata,
        generation_role="weapon",
        base_image=normalized_weapon_image,
        reference_images=references,
        mask_path=mask_path,
    )
