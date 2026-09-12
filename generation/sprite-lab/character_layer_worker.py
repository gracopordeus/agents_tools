"""Orchestrate the approved character-layer prompt, request and checkpoint."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

import ai_render_spec
import character_layer_persistence
import character_layer_request


def run_character_layer(
    *,
    job_id: str,
    render_spec: dict[str, Any],
    reference_manifest: list[dict[str, Any]],
    input_images: Sequence[Path],
    output_dir: Path,
    model: str,
    provider: Any,
    update_state: Callable[[dict[str, Any]], None],
    additional_instructions: str = "",
) -> dict[str, Any]:
    """Run or resume the character layer through the real provider boundary."""
    if (
        render_spec.get("generation_mode")
        == ai_render_spec.GENERATION_MODE_CHARACTER_COMPONENT_HOLDOUT
    ):
        prompt = ai_render_spec.compile_modular_layer_prompt(
            render_spec,
            reference_manifest,
            layer_id=render_spec["layer_contract"]["base_id"],
            additional_instructions=additional_instructions,
        )
    else:
        prompt = ai_render_spec.compile_layer_prompt(
            render_spec,
            reference_manifest,
            layer="character",
            additional_instructions=additional_instructions,
        )
    request = character_layer_request.build_character_layer_request(
        job_id=job_id,
        prompt=prompt,
        reference_manifest=reference_manifest,
        input_images=input_images,
        output_dir=output_dir,
        model=model,
        output_size=int(render_spec["output"]["width"]),
        source_contract=render_spec.get("source_contract") or {},
        supports_editing=getattr(provider, "name", "") == "openai",
    )
    update_state({"stage": "generating_character", "percent": 15})
    try:
        response = character_layer_persistence.run_character_stage(
            output_dir, request, provider.generate, update_state=update_state
        )
    except Exception:
        update_state({"stage": "character_failed", "percent": 15})
        raise
    update_state({
        "stage": "character_complete",
        "percent": 50,
        "resumed": response["resumed"],
    })
    return {**response, "prompt": prompt}
