"""Small auditable JSON-RPC broker for the ST08 integrated ChatGPT render.

It implements the subset consumed by :class:`LocalAppBridge` and exposes
already completed image-generation results as normal ``read_thread`` output.
The durable argument fingerprint makes ``create_thread`` idempotent even when
the caller retries with a fresh transport call id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import struct
import threading
from pathlib import Path


MAX_FRAME = 8 * 1024 * 1024


class BrokerState:
    def __init__(self, receipt_path: Path, character_image: Path, weapon_image: Path):
        self.receipt_path = Path(receipt_path)
        self.images = {
            "character": Path(character_image).resolve(),
            "weapon": Path(weapon_image).resolve(),
        }
        for role, path in self.images.items():
            if not path.is_file():
                raise FileNotFoundError(f"imagem {role} ausente: {path}")
        self.lock = threading.Lock()
        self.data = self._read()

    def _read(self) -> dict:
        if not self.receipt_path.is_file():
            return {"schema": "sprite_lab.st08_local_broker/v1", "threads": {}}
        loaded = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        if not isinstance(loaded.get("threads"), dict):
            raise ValueError("recibo do broker inválido")
        return loaded

    def _persist(self) -> None:
        self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.receipt_path.with_suffix(self.receipt_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, self.receipt_path)

    @staticmethod
    def _fingerprint(arguments: dict) -> str:
        canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _role(arguments: dict) -> str:
        prompt = str(arguments.get("prompt") or "").casefold()
        if "character layer contract — required" in prompt:
            return "character"
        if "weapon layer contract — required" in prompt:
            return "weapon"
        raise ValueError("prompt sem marcador inequívoco de camada ST08")

    def create_thread(self, arguments: dict) -> dict:
        fingerprint = self._fingerprint(arguments)
        with self.lock:
            for thread_id, item in self.data["threads"].items():
                if item.get("fingerprint") == fingerprint:
                    return {"threadId": thread_id}
            role = self._role(arguments)
            thread_id = f"st08-{role}-{fingerprint[:12]}"
            self.data["threads"][thread_id] = {
                "fingerprint": fingerprint,
                "role": role,
                "image": str(self.images[role]),
                "title": str(arguments.get("title") or ""),
                "model": arguments.get("model"),
                "thinking": arguments.get("thinking"),
            }
            self._persist()
            return {"threadId": thread_id}

    def read_thread(self, arguments: dict) -> dict:
        thread_id = str(arguments.get("threadId") or "")
        with self.lock:
            item = self.data["threads"].get(thread_id)
        if item is None:
            raise ValueError(f"thread desconhecida: {thread_id}")
        return {
            "thread": {"id": thread_id, "status": {"type": "idle"}},
            "turns": [{"items": [{"type": "imageGeneration", "savedPath": item["image"]}]}],
        }


def _read_exact(connection: socket.socket, size: int) -> bytes:
    output = bytearray()
    while len(output) < size:
        chunk = connection.recv(size - len(output))
        if not chunk:
            raise RuntimeError("conexão encerrada")
        output.extend(chunk)
    return bytes(output)


def _handle(connection: socket.socket, state: BrokerState) -> None:
    size = struct.unpack("<I", _read_exact(connection, 4))[0]
    if size > MAX_FRAME:
        raise ValueError("frame excede 8 MiB")
    request = json.loads(_read_exact(connection, size))
    try:
        method = request.get("method")
        params = request.get("params") or {}
        if method == "tools/list":
            result = {"tools": [{"name": "create_thread"}, {"name": "read_thread"}]}
        elif method == "tools/call":
            tool = params.get("tool")
            arguments = params.get("arguments") or {}
            if tool == "create_thread":
                payload = state.create_thread(arguments)
            elif tool == "read_thread":
                payload = state.read_thread(arguments)
            else:
                raise ValueError(f"tool não suportada: {tool}")
            result = {
                "success": True,
                "contentItems": [{"type": "inputText", "text": json.dumps(payload, ensure_ascii=False)}],
            }
        else:
            raise ValueError(f"método não suportado: {method}")
        response = {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
    except Exception as exc:
        response = {
            "jsonrpc": "2.0", "id": request.get("id"),
            "error": {"code": -32000, "message": str(exc)},
        }
    encoded = json.dumps(response, ensure_ascii=False).encode()
    connection.sendall(struct.pack("<I", len(encoded)) + encoded)


def serve(socket_path: Path, state: BrokerState, *, ready=None, max_connections: int | None = None) -> None:
    socket_path = Path(socket_path)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    count = 0
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(socket_path))
        listener.listen(8)
        if ready is not None:
            ready.set()
        try:
            while max_connections is None or count < max_connections:
                connection, _ = listener.accept()
                with connection:
                    _handle(connection, state)
                count += 1
        finally:
            socket_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--character", type=Path, required=True)
    parser.add_argument("--weapon", type=Path, required=True)
    args = parser.parse_args()
    serve(args.socket, BrokerState(args.receipt, args.character, args.weapon))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
