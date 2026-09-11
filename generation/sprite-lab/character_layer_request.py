"""Build an auditable provider request for the isolated character layer."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any, Sequence

from image_generation_provider import GenerationRequest, _create_openai_cell_mask


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_character_layer_request(
    *,
    job_id: str,
    prompt: str,
    reference_manifest: list[dict[str, Any]],
    input_images: Sequence[Path],
    output_dir: Path,
    model: str,
    output_size: int,
    source_contract: dict[str, Any],
    supports_editing: bool,
) -> GenerationRequest:
    """Map the ordered manifest to both modern roles and legacy input_images."""
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
    beauty_indexes = [
        index for index, item in enumerate(reference_manifest)
        if item.get("type") == "beauty"
    ]
    base_index = beauty_indexes[0] if len(beauty_indexes) == 1 else 0
    base_image = paths[base_index]
    references = paths[:base_index] + paths[base_index + 1:]
    output_dir.mkdir(parents=True, exist_ok=True)
    mask_path = None
    if supports_editing:
        mask_path = output_dir / "character_edit_mask.png"
        _create_openai_cell_mask(base_image, mask_path)
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
    return GenerationRequest(
        job_id=job_id,
        prompt=prompt,
        input_images=paths,
        output_path=output_dir / "character_full.png",
        model=model,
        metadata={
            "output_size": [output_size, output_size],
            "materialize_output": True,
            "reference_manifest": copy.deepcopy(reference_manifest),
            "input_hashes": input_hashes,
            "source_contract": copy.deepcopy(source_contract),
        },
        generation_role="character",
        base_image=base_image,
        reference_images=references,
        mask_path=mask_path,
    )
