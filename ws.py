"""Minimal RFC 6455 WebSocket client - stdlib sockets only.

Just enough for local CDP (Chrome DevTools Protocol) conversations:
client handshake, outgoing masked text frames, incoming text frames,
ping/pong handling. No extensions, no compression, no TLS.

This exists so the agent can talk to Chromium's debugging endpoint with
zero third-party dependencies.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WsError(Exception):
    pass


class WsClient:
    def __init__(self, host: str, port: int, resource: str = "/"):
        self.sock = socket.create_connection((host, port), timeout=30)
        self.buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {resource} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())

        # read headers
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WsError("connection closed during handshake")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        status = head.split(b"\r\n")[0].decode()
        if "101" not in status:
            raise WsError(f"handshake refused: {status}")
        expected = base64.b64encode(
            hashlib.sha1((key + GUID).encode()).digest()
        ).decode()

    # ---- sending ---------------------------------------------------------

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode())

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
        elif length < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
        self.sock.sendall(header + mask + masked)

    # ---- receiving -------------------------------------------------------

    def _read_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WsError("connection closed mid-frame")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv_text(self, timeout: float | None = None) -> str:
        """Receive one complete text message (handles fragmentation)."""
        if timeout is not None:
            self.sock.settimeout(timeout)
        message = b""
        opcode0 = None
        while True:
            b1, b2 = self._read_exact(2)
            fin = b1 & 0x80
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                (length,) = struct.unpack("!H", self._read_exact(2))
            elif length == 127:
                (length,) = struct.unpack("!Q", self._read_exact(8))
            if masked:  # servers should not mask; tolerate anyway
                mask = self._read_exact(4)
                data = bytes(
                    b ^ mask[i % 4] for i, b in enumerate(self._read_exact(length))
                )
            else:
                data = self._read_exact(length)

            if opcode == 0x8:  # close
                self._send_frame(0x8, data[:2] if len(data) >= 2 else b"")
                raise WsError("peer sent close")
            if opcode == 0x9:  # ping -> pong, keep reading
                self._send_frame(0xA, data)
                continue
            if opcode == 0xA:  # pong
                continue

            opcode0 = opcode0 or opcode
            message += data
            if fin:
                return message.decode()

    def close(self) -> None:
        try:
            self._send_frame(0x8, struct.pack("!H", 1000))
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


def ws_url_parse(url: str) -> tuple[str, int, str]:
    """ws://127.0.0.1:9222/devtools/page/ABC -> ("127.0.0.1", 9222, path)"""
    rest = url.split("://", 1)[1]
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    return host, int(port), "/" + path


def send_json(ws: WsClient, obj: dict) -> None:
    ws.send_text(json.dumps(obj))


def recv_json(ws: WsClient, timeout: float | None = None) -> dict:
    return json.loads(ws.recv_text(timeout))
