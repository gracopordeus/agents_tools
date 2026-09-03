"""Create and execute single-frame generation jobs from a conditioning pack."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import conditioning_schema as schema
from image_generation_provider import GenerationRequest, create_provider, default_model


def _condition_channels(manifest: dict[str, Any], condition: str) -> list[str]:
    if condition not in schema.CONDITIONS:
        raise ValueError(f"condition desconhecida: {condition!r}")
    channels = ["beauty"]
    if condition in {"silhouette", "segmentation", "depth", "skeleton"}:
        channels.append("silhouette")
    if condition in {"segmentation", "depth", "skeleton"}:
        channels.append("segmentation")
    if condition in {"depth", "skeleton"}:
        channels.append(condition)
    available = set(manifest["channels"])
    missing = sorted(set(channels) - available)
    if missing:
        raise ValueError(f"condition {condition} exige canais ausentes: {', '.join(missing)}")
    return channels


def request_for_frame(
    manifest_path: Path,
    frame: dict[str, Any],
    *,
    condition: str,
    output_dir: Path,
    model: str,
) -> GenerationRequest:
    manifest = schema.load_manifest(manifest_path)
    root = manifest_path.parent
    channels = _condition_channels(manifest, condition)
    inputs = [root / manifest["target_reference"]["path"]]
    inputs.extend(root / frame["channels"][channel] for channel in channels)
    prompt = manifest["prompt"]["template"]
    prompt = (
        f"{prompt}\nGenerate only the subject for frame {frame['id']} of "
        f"{manifest['action']} in direction {manifest['direction']}."
    )
    return GenerationRequest(
        job_id=f"{manifest['id']}-{condition}-{frame['id']}",
        prompt=prompt,
        input_images=tuple(inputs),
        output_path=output_dir / f"{frame['id']}.png",
        model=model,
        metadata={
            "manifest": str(manifest_path.resolve()),
            "condition": condition,
            "frame_id": frame["id"],
            "channels": channels,
        },
    )


def run(
    manifest_path: Path,
    *,
    provider_name: str,
    model: str,
    condition: str,
    output_dir: Path,
    frame_ids: list[str] | None = None,
) -> dict[str, Any]:
    manifest = schema.load_manifest(manifest_path)
    selected = set(frame_ids or [frame["id"] for frame in manifest["frames"]])
    valid_ids = {frame["id"] for frame in manifest["frames"]}
    unknown = sorted(selected - valid_ids)
    if unknown:
        raise ValueError(f"frame IDs desconhecidos: {', '.join(unknown)}")
    provider = create_provider(provider_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for frame in manifest["frames"]:
        if frame["id"] not in selected:
            continue
        request = request_for_frame(
            manifest_path,
            frame,
            condition=condition,
            output_dir=output_dir,
            model=model,
        )
        result = provider.generate(request)
        results.append(
            {
                "frame_id": frame["id"],
                "job_id": request.job_id,
                "status": result.status,
                "provider": result.provider,
                "model": result.model,
                "output": str(result.output_path) if result.output_path else None,
                "response_metadata": result.response_metadata,
            }
        )
    report = {
        "schema": "generation.conditioning_runner/v1",
        "manifest": str(manifest_path.resolve()),
        "provider": provider_name,
        "model": model,
        "condition": condition,
        "output_dir": str(output_dir.resolve()),
        "results": results,
    }
    (output_dir / "run.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--provider", default="dry-run", choices=("dry-run", "openai", "google", "qwen"))
    parser.add_argument("--model")
    parser.add_argument("--condition", default="segmentation", choices=schema.CONDITIONS)
    parser.add_argument("--frames", help="IDs separados por vírgula; padrão: todos")
    args = parser.parse_args(argv)
    frame_ids = [item.strip() for item in args.frames.split(",") if item.strip()] if args.frames else None
    report = run(
        args.manifest,
        provider_name=args.provider,
        model=args.model or default_model(args.provider),
        condition=args.condition,
        output_dir=args.output_dir,
        frame_ids=frame_ids,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
