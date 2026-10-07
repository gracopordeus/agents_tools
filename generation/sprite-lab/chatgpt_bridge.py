"""Experimental local ChatGPT/Codex task bridge; no API key or session extraction."""
from __future__ import annotations

import json
import os
import base64
import binascii
import shutil
import socket
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path


MAX_FRAME = 8 * 1024 * 1024

# The local app catalog currently identifies GPT-6 Astra as its most capable
# Codex model and accepts Ultra reasoning for it. Keep these explicit so a
# change to the user's app default cannot silently route AI Render to Luna.
CHATGPT_TASK_MODEL = "gpt-6-astra"
CHATGPT_TASK_THINKING = "ultra"


def _bridge_artifact(output: Path, generation_role: str | None, suffix: str) -> Path:
    role = str(generation_role or "").strip().casefold()
    if role in {"character", "weapon"}:
        return output.with_name(f"{role}_full.chatgpt_{suffix}")
    return output.with_name(f"chatgpt_{suffix}")


class LocalAppBridge:
    def __init__(self, pipe: str | None = None, caller: str | None = None):
        self.pipe = pipe or os.environ.get("GENERATION_CHATGPT_PIPE")
        self.caller = caller or os.environ.get("GENERATION_CHATGPT_CALLER_THREAD") or os.environ.get("CODEX_THREAD_ID")

    @staticmethod
    def _read(connection, size):
        chunks = bytearray()
        while len(chunks) < size:
            chunk = connection.recv(size - len(chunks))
            if not chunk:
                raise RuntimeError("ChatGPT fechou a conexão antes da resposta")
            chunks.extend(chunk)
        return bytes(chunks)

    def rpc(self, method, params, timeout=15):
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        if len(payload) > MAX_FRAME:
            raise ValueError("Requisição da ponte excede 8 MiB")
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(timeout)
            connection.connect(self.pipe)
            connection.sendall(struct.pack("<I", len(payload)) + payload)
            size = struct.unpack("<I", self._read(connection, 4))[0]
            if size > MAX_FRAME:
                raise RuntimeError("Resposta da ponte excede 8 MiB")
            response = json.loads(self._read(connection, size))
        if "error" in response:
            raise RuntimeError(response["error"].get("message", "Erro da ponte"))
        return response["result"]

    def discover(self):
        candidates = [Path(self.pipe)] if self.pipe else sorted(Path("/tmp/codex-browser-use").glob("*.sock"))
        for path in candidates:
            self.pipe = str(path)
            try:
                catalog = self.rpc("tools/list", {"threadStartKind": "all"}, timeout=2)
                names = {tool["name"] for tool in catalog.get("tools", [])}
                if {"create_thread", "read_thread"} <= names:
                    return self
            except (OSError, ValueError, RuntimeError):
                continue
        raise RuntimeError("Ponte indisponível: abra o ChatGPT local com uma tarefa local ativa")

    def call(self, tool, arguments, timeout=45):
        if not self.caller:
            raise RuntimeError("Configure GENERATION_CHATGPT_CALLER_THREAD com o ID da tarefa local que hospeda a ponte")
        response = self.rpc("tools/call", {
            "namespace": "codex_app", "tool": tool, "arguments": arguments,
            "threadId": self.caller, "callId": str(uuid.uuid4()), "turnId": str(uuid.uuid4()),
        }, timeout=timeout)
        texts = [item["text"] for item in response.get("contentItems", []) if item.get("type") == "inputText"]
        text = "\n".join(texts)
        if not response.get("success"):
            raise RuntimeError(text or "O aplicativo não executou a operação")
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text}


class ChatGPTBridgeProvider:
    name = "chatgpt-local"

    def generate(self, request):
        from image_generation_provider import GenerationResult, _requested_output_size, _write_request
        from PIL import Image

        size = _requested_output_size(request)
        for reference in request.input_images:
            if not reference.is_file():
                raise FileNotFoundError(reference)
        output = request.output_path.resolve()
        receipt = _bridge_artifact(output, request.generation_role, "bridge.json")
        if receipt.exists():
            raise RuntimeError("Este job já teve uma tentativa de envio; consulte chatgpt_bridge.json antes de reenviar")
        bridge = LocalAppBridge().discover()
        if not bridge.caller:
            raise RuntimeError("Configure GENERATION_CHATGPT_CALLER_THREAD antes de iniciar o servidor")
        _write_request(request, self.name)
        raw = _bridge_artifact(output, request.generation_role, "original.png")
        instructions = (
            "Execute esta solicitação do Generation AI Render usando $imagegen, no modo integrado da sessão. "
            "Não use API paga nem crie outra tarefa. Leia as imagens locais abaixo na ordem listada, "
            "preservando seus papéis. Gere uma única imagem seguindo o contrato. "
            "Deixe a imagem gerada disponível no resultado da tarefa para que o sistema chamador a persista. "
            "Não escreva arquivos, não modifique o código e não peça autorização de escrita.\n\n"
            "Referências locais (ordem física):\n" + "\n".join(
                f"{i}. {path.resolve()}" for i, path in enumerate(request.input_images, 1)
            ) + "\n\nContrato de geração:\n" + request.prompt
        )
        state = {"status": "dispatching", "job_id": request.job_id,
                 "requested_size": list(size), "pipe": bridge.pipe, "original": str(raw),
                 "task_model": CHATGPT_TASK_MODEL, "task_thinking": CHATGPT_TASK_THINKING}

        def persist():
            receipt.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        persist()
        try:
            state["dispatch"] = bridge.call("create_thread", {
                "title": "Generation AI Render " + request.job_id,
                "prompt": instructions, "target": {"type": "projectless"},
                "model": CHATGPT_TASK_MODEL, "thinking": CHATGPT_TASK_THINKING,
            })
        except Exception as exc:
            state.update(status="dispatch_uncertain", error=str(exc))
            persist()
            raise RuntimeError("Envio não confirmado; confira a tarefa no ChatGPT antes de criar outro job. " + str(exc)) from exc
        state["status"] = "waiting_for_image"
        persist()
        child_thread_id = state["dispatch"].get("threadId")
        if not child_thread_id:
            state.update(status="dispatch_invalid", error="create_thread não retornou threadId")
            persist()
            raise RuntimeError("A ponte criou uma tarefa sem retornar o ID dela")
        state["child_thread_id"] = child_thread_id
        persist()
        deadline = time.monotonic() + float(os.environ.get("GENERATION_CHATGPT_TIMEOUT", "900"))
        while time.monotonic() < deadline:
            snapshot = bridge.call("read_thread", {
                "threadId": child_thread_id,
                "turnLimit": 3,
                "includeOutputs": True,
                "maxOutputCharsPerItem": 20000,
            }, timeout=45)
            remote = snapshot.get("thread", {})
            state["remote_status"] = remote.get("status")
            state["remote_updated_at"] = remote.get("updatedAt")
            source = _find_generated_image(snapshot)
            if source is not None:
                _persist_source_image(source, raw)
                with Image.open(raw) as image:
                    image.load()
                    actual = image.size
                    if actual[0] != actual[1]:
                        state.update(status="invalid_aspect_ratio", actual_size=list(actual))
                        persist()
                        raise RuntimeError(f"ChatGPT retornou {actual}; spritesheet exige imagem quadrada. Original preservado em {raw}")
                    rgba = image.convert("RGBA")
                    alpha_extrema = rgba.getchannel("A").getextrema()
                    alpha_1024 = rgba.getchannel("A").resize(
                        (1024, 1024), Image.Resampling.LANCZOS
                    )
                    resized_1024 = _bridge_artifact(output, request.generation_role, "1024.png")
                    rgba.convert("RGB").resize(
                        (1024, 1024), Image.Resampling.LANCZOS
                    ).save(resized_1024, format="PNG")
                swinir_output = _bridge_artifact(output, request.generation_role, "swinir_2048.png")
                swinir_report = _run_swinir(resized_1024, swinir_output)
                with Image.open(swinir_output) as enhanced:
                    enhanced.load()
                    if enhanced.size != (2048, 2048):
                        raise RuntimeError(f"SwinIR retornou {enhanced.size}; esperado (2048, 2048)")
                    final_rgb = enhanced.convert("RGB")
                    if size != enhanced.size:
                        final_rgb = final_rgb.resize(size, Image.Resampling.LANCZOS)
                    final_image = final_rgb.convert("RGBA")
                    final_alpha = alpha_1024.resize(size, Image.Resampling.LANCZOS)
                    final_image.putalpha(final_alpha)
                    invisible = final_alpha.point(lambda alpha: 255 if alpha == 0 else 0)
                    final_image.paste((0, 0, 0, 0), mask=invisible)
                    final_image.save(output, format="PNG")
                state.update(status="done", actual_size=list(actual), pre_resize_size=[1024, 1024], upscale_output_size=[2048, 2048], output_size=list(size), resized=True,
                             has_transparency=alpha_extrema[0] < 255, upscale=swinir_report)
                persist()
                return GenerationResult("done", self.name, CHATGPT_TASK_MODEL, output, state)
            if isinstance(remote.get("status"), dict) and remote["status"].get("type") == "waitingOnApproval":
                state["status"] = "waiting_on_approval"
            persist()
            time.sleep(2)
        state["status"] = "timeout"
        persist()
        raise RuntimeError("Tempo esgotado aguardando a imagem; a tarefa pode continuar no ChatGPT. Consulte chatgpt_bridge.json")


def _swinir_python() -> str:
    configured = os.environ.get("GENERATION_SWINIR_PYTHON", "").strip()
    if configured:
        return configured
    bundled = Path("/home/ggnp/pose-venv/bin/python")
    return str(bundled) if bundled.is_file() else sys.executable


def _run_swinir(source: Path, output: Path) -> dict:
    profile = os.environ.get("GENERATION_SWINIR_PROFILE", "swinir_m_classical_df2k_x2").strip()
    device = os.environ.get("GENERATION_SWINIR_DEVICE", "auto").strip()
    precision = os.environ.get("GENERATION_SWINIR_PRECISION", "fp32").strip()
    command = [
        _swinir_python(), str(Path(__file__).with_name("swinir_upscale.py")),
        str(source), str(output), "--profile", profile, "--device", device, "--precision", precision,
    ]
    try:
        completed = subprocess.run(
            command, check=True, capture_output=True, text=True,
            timeout=float(os.environ.get("GENERATION_SWINIR_TIMEOUT", "900")),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError(f"SwinIR falhou: {detail[-2000:]}") from exc
    if not output.is_file():
        raise RuntimeError("SwinIR terminou sem produzir o arquivo de saída")
    return {
        "backend": "SwinIR",
        "profile": profile,
        "python": _swinir_python(),
        "device": device,
        "precision": precision,
        "source": str(source),
        "output": str(output),
        "stdout": completed.stdout[-1000:],
    }


def _find_generated_image(snapshot):
    """Extract the first image-generation result from a read_thread payload."""
    for turn in snapshot.get("turns", []):
        for item in turn.get("items", []):
            if item.get("type") != "imageGeneration":
                continue
            result = item.get("result")
            if isinstance(result, str) and result.strip():
                return result
            path = item.get("savedPath")
            if isinstance(path, str) and path.strip():
                return path
    return None


def _persist_source_image(source, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(source, str) and source.startswith("data:"):
        _, encoded = source.split(",", 1)
        destination.write_bytes(base64.b64decode(encoded))
        return
    if isinstance(source, str):
        try:
            destination.write_bytes(base64.b64decode(source, validate=True))
            return
        except (ValueError, binascii.Error):
            source_path = Path(source)
            if source_path.is_file():
                shutil.copy2(source_path, destination)
                return
    raise RuntimeError("A tarefa do ChatGPT retornou imagem em formato não suportado")
