"""HTTP middleware for request-body limits."""
from __future__ import annotations

import asyncio
import json
import tempfile

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _UploadRequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int, receive_timeout: float = 60.0):
        self.app = app
        self.max_body_bytes = max_body_bytes
        if receive_timeout <= 0:
            raise ValueError("receive_timeout must be greater than 0")
        self.receive_timeout = receive_timeout

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] != "/v1/documents"
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", ()))
        authorization = headers.get(b"authorization", b"")
        if not authorization.lower().startswith(b"bearer ") or not authorization.strip()[7:].strip():
            await self._send_error(send, 401, "A Supabase Bearer access token is required")
            return

        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                await self._send_error(send, 400, "Invalid Content-Length")
                return
            if content_length < 0:
                await self._send_error(send, 400, "Invalid Content-Length")
                return
            if content_length > self.max_body_bytes:
                await self._send_too_large(send)
                return

        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as body_file:
            body_size = 0
            started = asyncio.get_running_loop().time()
            while True:
                elapsed = asyncio.get_running_loop().time() - started
                remaining = self.receive_timeout - elapsed
                if remaining <= 0:
                    await self._send_error(send, 408, "Upload request timed out")
                    return
                try:
                    message = await asyncio.wait_for(receive(), timeout=remaining)
                except asyncio.TimeoutError:
                    await self._send_error(send, 408, "Upload request timed out")
                    return
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    continue
                body = message.get("body", b"")
                body_size += len(body)
                if body_size > self.max_body_bytes:
                    await self._send_too_large(send)
                    return
                body_file.write(body)
                if not message.get("more_body", False):
                    break

            body_file.seek(0)

            async def replay_body() -> Message:
                chunk = body_file.read(64 * 1024)
                return {
                    "type": "http.request",
                    "body": chunk,
                    "more_body": body_file.tell() < body_size,
                }

            await self.app(scope, replay_body, send)

    async def _send_too_large(self, send: Send) -> None:
        await self._send_error(
            send,
            413,
            "Upload request exceeds the configured request-body limit",
        )

    @staticmethod
    async def _send_error(send: Send, status_code: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
