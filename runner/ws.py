"""A small WebSocket client using only the Python standard library (RFC 6455).

Enough for streaming market data: TLS, handshake, text/binary frames, fragmentation,
large frames, ping/pong, close. No package installs needed on the runner.
"""
import base64
import os
import socket
import ssl
import struct
from urllib.parse import urlparse


class WebSocketClosed(Exception):
    pass


class WebSocket:
    def __init__(self, url, timeout=30):
        u = urlparse(url)
        self.host = u.hostname
        self.port = u.port or (443 if u.scheme == "wss" else 80)
        self.path = (u.path or "/") + (("?" + u.query) if u.query else "")
        raw = socket.create_connection((self.host, self.port), timeout=timeout)
        if u.scheme == "wss":
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=self.host)
        self.sock = raw
        self.sock.settimeout(timeout)
        self._buf = b""
        self._parts = []
        self._handshake()

    # ------------------------------------------------------------------ low level
    def _fill(self):
        chunk = self.sock.recv(262144)       # may raise socket.timeout; the buffer stays intact
        if not chunk:
            raise WebSocketClosed("connection closed by server")
        self._buf += chunk

    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {self.path} HTTP/1.1\r\nHost: {self.host}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               f"User-Agent: tape-runner/1.0\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WebSocketClosed("handshake failed: no response")
            head += chunk
        header, rest = head.split(b"\r\n\r\n", 1)
        status = header.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            raise WebSocketClosed(f"handshake failed: {status.decode(errors='replace')}")
        self._buf = rest

    def _send_frame(self, opcode, payload=b""):
        header = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def _parse_frame(self):
        """Take one complete frame off the buffer, or return None if it hasn't fully arrived."""
        buf = self._buf
        if len(buf) < 2:
            return None
        b1, b2 = buf[0], buf[1]
        fin, opcode = b1 & 0x80, b1 & 0x0F
        masked, n = b2 & 0x80, b2 & 0x7F
        i = 2
        if n == 126:
            if len(buf) < 4:
                return None
            n = struct.unpack("!H", buf[2:4])[0]
            i = 4
        elif n == 127:
            if len(buf) < 10:
                return None
            n = struct.unpack("!Q", buf[2:10])[0]
            i = 10
        mask = None
        if masked:
            if len(buf) < i + 4:
                return None
            mask = buf[i:i + 4]
            i += 4
        if len(buf) < i + n:
            return None
        data = buf[i:i + n]
        self._buf = buf[i + n:]
        if mask:
            data = bytes(b ^ mask[k % 4] for k, b in enumerate(data))
        return bool(fin), opcode, data

    # ------------------------------------------------------------------ public
    def send(self, text):
        self._send_frame(0x1, text.encode())

    def recv(self):
        """Next complete text/binary message as str. Answers pings automatically.
        Raises socket.timeout if nothing complete arrives in time (safe to call again)."""
        while True:
            fr = self._parse_frame()
            if fr is None:
                self._fill()
                continue
            fin, op, data = fr
            if op == 0x9:          # ping
                self._send_frame(0xA, data)
                continue
            if op == 0xA:          # pong
                continue
            if op == 0x8:          # close
                try:
                    self._send_frame(0x8, data[:2])
                except OSError:
                    pass
                raise WebSocketClosed("server closed the connection")
            if op in (0x1, 0x2):
                self._parts = [data]
            elif op == 0x0:        # continuation
                self._parts.append(data)
            if fin and self._parts:
                msg, self._parts = b"".join(self._parts), []
                return msg.decode("utf-8", errors="replace")

    def settimeout(self, s):
        self.sock.settimeout(s)

    def close(self):
        try:
            self._send_frame(0x8, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
