"""Orchestrate the approved weapon prompt, request and checkpoint."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

import ai_render_spec
import weapon_layer_persistence
import weapon_layer_request


def run_weapon_layer(
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
    """Run or resume the weapon layer through the provider boundary."""
    # Check the approval boundary before request construction. This keeps an
    # out-of-order call durable and unambiguous even when character_full.png
    # is absent from the request's input list.
    try:
        weapon_layer_persistence.require_approved_character(output_dir)
    except weapon_layer_persistence.WeaponPrerequisiteError as exc:
        weapon_layer_persistence.record_weapon_blocked(output_dir, str(exc))
        update_state({"stage": "weapon_blocked", "percent": 50})
        raise
    prompt = ai_render_spec.compile_layer_prompt(
        render_spec,
        reference_manifest,
        layer="weapon",
        additional_instructions=additional_instructions,
    )
    try:
        request = weapon_layer_request.build_weapon_layer_request(
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
    except Exception as exc:
        weapon_layer_persistence.record_weapon_failed(output_dir, str(exc))
        update_state({"stage": "weapon_failed", "percent": 65})
        raise
    update_state({"stage": "generating_weapon", "percent": 65})
    try:
        response = weapon_layer_persistence.run_weapon_stage(
            output_dir, request, provider.generate, update_state=update_state
        )
    except weapon_layer_persistence.WeaponPrerequisiteError:
        update_state({"stage": "weapon_blocked", "percent": 50})
        raise
    except Exception:
        update_state({"stage": "weapon_failed", "percent": 65})
        raise
    update_state({
        "stage": "weapon_complete",
        "percent": 100,
        "resumed": response["resumed"],
    })
    return {**response, "prompt": prompt}
