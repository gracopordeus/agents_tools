"""Extract deterministic animation metadata from FBX files inside Blender.

This file is intentionally a small Blender-side worker.  It receives a JSON
request and writes JSON results so the catalog orchestrator can remain usable
with the normal Python interpreter.  It does not render images and never
modifies the source FBX.

Usage::

    blender --background --python blender_animation_probe.py -- \
      --input probe_request.json --output probe_result.json
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import bpy


BONE_PATH_RE = re.compile(r"pose\.bones\[(?:\"([^\"]+)\"|'([^']+)')\]")
ROOT_BONE_TOKENS = ("root", "hips", "pelvis", "master")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _action_fcurves(action: bpy.types.Action) -> list[Any]:
    """Return legacy and current Blender action curves when available."""
    curves = getattr(action, "fcurves", None)
    if curves is not None:
        try:
            return list(curves)
        except (TypeError, RuntimeError):
            pass
    # Blender versions with layered Actions expose curves through slots.  The
    # fallback keeps the probe useful even when a file uses the new API.
    result: list[Any] = []
    for layer in getattr(action, "layers", []) or []:
        for strip in getattr(layer, "strips", []) or []:
            channelbag_method = getattr(strip, "channelbag", None)
            if channelbag_method is None:
                continue
            for slot in getattr(action, "slots", []) or []:
                try:
                    channelbag = channelbag_method(slot)
                except (TypeError, RuntimeError):
                    continue
                if channelbag is not None:
                    result.extend(list(getattr(channelbag, "fcurves", []) or []))
    return result


def _bone_names(curves: list[Any]) -> list[str]:
    names: set[str] = set()
    for curve in curves:
        path = str(getattr(curve, "data_path", ""))
        match = BONE_PATH_RE.search(path)
        if match:
            names.add(match.group(1) or match.group(2))
    return sorted(names, key=str.casefold)


def _keyframe_count(curves: list[Any]) -> int:
    total = 0
    for curve in curves:
        try:
            total += len(curve.keyframe_points)
        except (AttributeError, TypeError, RuntimeError):
            continue
    return total


def _is_cyclic(curves: list[Any]) -> bool:
    for curve in curves:
        for modifier in getattr(curve, "modifiers", []) or []:
            if str(getattr(modifier, "type", "")).upper() == "CYCLES":
                return True
    return False


def _root_motion(action: bpy.types.Action, curves: list[Any], start: float, end: float) -> dict[str, Any]:
    candidates: list[tuple[str, Any]] = []
    for curve in curves:
        path = str(getattr(curve, "data_path", ""))
        match = BONE_PATH_RE.search(path)
        if not match or not path.endswith("location"):
            continue
        bone_name = match.group(1) or match.group(2)
        lowered = bone_name.casefold()
        if any(token in lowered for token in ROOT_BONE_TOKENS):
            candidates.append((bone_name, curve))

    if not candidates:
        return {
            "present": False,
            "bone": None,
            "distance": 0.0,
            "delta": [0.0, 0.0, 0.0],
        }

    bone_name = sorted(candidates, key=lambda item: (len(item[0]), item[0].casefold()))[0][0]
    selected = [curve for name, curve in candidates if name == bone_name]
    delta = [0.0, 0.0, 0.0]
    for curve in selected:
        axis = int(getattr(curve, "array_index", 0))
        if axis not in (0, 1, 2):
            continue
        try:
            delta[axis] = _safe_float(curve.evaluate(end) - curve.evaluate(start))
        except (AttributeError, RuntimeError, TypeError):
            continue
    distance = math.sqrt(sum(value * value for value in delta))
    return {
        "present": distance > 1e-5,
        "bone": bone_name,
        "distance": round(distance, 6),
        "delta": [round(value, 6) for value in delta],
    }


def _rig_fingerprint(armature: bpy.types.Object | None) -> str | None:
    if armature is None or armature.data is None:
        return None
    bones = []
    for bone in armature.data.bones:
        parent = bone.parent.name if bone.parent else ""
        bones.append(f"{bone.name}\t{parent}")
    payload = "\n".join(sorted(bones, key=str.casefold)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _action_record(action: bpy.types.Action, scene: bpy.types.Scene) -> dict[str, Any]:
    curves = _action_fcurves(action)
    frame_start = _safe_float(action.frame_range[0], 1.0)
    frame_end = _safe_float(action.frame_range[1], frame_start)
    frame_start_int = max(1, math.floor(frame_start))
    frame_end_int = max(frame_start_int, math.ceil(frame_end))
    fps = _safe_float(scene.render.fps, 30.0) / max(
        _safe_float(scene.render.fps_base, 1.0), 1e-6
    )
    return {
        "name": str(action.name),
        "frame_start": frame_start_int,
        "frame_end": frame_end_int,
        "frame_count": frame_end_int - frame_start_int + 1,
        "duration_seconds": round(max(0.0, frame_end - frame_start) / max(fps, 1e-6), 6),
        "fps": round(fps, 6),
        "fcurve_count": len(curves),
        "keyframe_count": _keyframe_count(curves),
        "animated_bone_count": len(_bone_names(curves)),
        "animated_bones": _bone_names(curves),
        "loop": _is_cyclic(curves),
        "root_motion": _root_motion(action, curves, frame_start, frame_end),
    }


def _probe_file(item: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(item["path"])).expanduser().resolve()
    result: dict[str, Any] = {
        "asset_id": item.get("asset_id"),
        "source_sha256": item.get("source_sha256"),
        "path": str(path),
        "status": "error",
        "actions": [],
        "warnings": [],
        "errors": [],
    }
    if not path.is_file():
        result["errors"].append(f"arquivo ausente: {path}")
        return result

    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.fbx(filepath=str(path))
        scene = bpy.context.scene
        armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
        meshes = [obj for obj in bpy.data.objects if obj.type == "MESH"]
        primary = armatures[0] if armatures else None
        actions = list(bpy.data.actions)
        result.update(
            {
                "status": "ok" if actions else "no_actions",
                "blender_version": ".".join(str(part) for part in bpy.app.version),
                "fps": round(
                    _safe_float(scene.render.fps, 30.0)
                    / max(_safe_float(scene.render.fps_base, 1.0), 1e-6),
                    6,
                ),
                "armature_count": len(armatures),
                "armatures": [obj.name for obj in armatures],
                "bone_count": len(primary.data.bones) if primary and primary.data else 0,
                "rig_fingerprint": _rig_fingerprint(primary),
                "mesh_count": len(meshes),
                "vertex_count": sum(len(obj.data.vertices) for obj in meshes),
                "actions": [_action_record(action, scene) for action in actions],
            }
        )
        if not armatures:
            result["warnings"].append("FBX sem armature")
        if meshes and not actions:
            result["warnings"].append("FBX possui mesh, mas nenhuma Action")
    except Exception as exc:  # Blender import errors vary by file and version.
        result["errors"].append(f"{type(exc).__name__}: {exc}")
    return result


def _args() -> tuple[Path, Path]:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    input_path = None
    output_path = None
    for index, value in enumerate(argv):
        if value == "--input" and index + 1 < len(argv):
            input_path = Path(argv[index + 1])
        elif value == "--output" and index + 1 < len(argv):
            output_path = Path(argv[index + 1])
    if input_path is None or output_path is None:
        raise SystemExit("uso: --input REQUEST.json --output RESULT.json")
    return input_path, output_path


def main() -> int:
    input_path, output_path = _args()
    request = json.loads(input_path.read_text(encoding="utf-8"))
    results = [_probe_file(item) for item in request.get("files", [])]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "schema": "sprite_lab.blender_animation_probe/v1",
                "blender_version": ".".join(str(part) for part in bpy.app.version),
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"PROBED_FBX files={len(results)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
