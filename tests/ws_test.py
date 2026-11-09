"""Tests the stdlib WebSocket client against a tiny local server: fragments, big frames,
pings, frames split across reads + timeouts, and close."""
import base64, hashlib, json, os, socket, struct, sys, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))
from ws import WebSocket, WebSocketClosed

def frame(op, data, fin=True):
    h = bytes([(0x80 if fin else 0) | op]); n = len(data)
    h += bytes([n]) if n < 126 else (bytes([126]) + struct.pack("!H", n) if n < 65536 else bytes([127]) + struct.pack("!Q", n))
    return h + data

def server(sock, got):
    c, _ = sock.accept()
    req = b""
    while b"\r\n\r\n" not in req: req += c.recv(4096)
    key = [l.split(b": ")[1] for l in req.split(b"\r\n") if l.lower().startswith(b"sec-websocket-key")][0]
    acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
    c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + acc + b"\r\n\r\n" + frame(1, b"hello"))
    # read client's masked text frame
    d = c.recv(4096); n = d[1] & 0x7F; mask = d[2:6]; got.append(bytes(b ^ mask[i % 4] for i, b in enumerate(d[6:6+n])).decode())
    c.sendall(frame(0x9, b"pp"))                                       # ping (client must pong)
    c.sendall(frame(1, b"frag-", fin=False) + frame(0, b"ment"))       # fragmented message
    big = json.dumps({"x": "y" * 200000}).encode(); c.sendall(frame(1, big))   # 64-bit length
    f = frame(1, b"split-across-reads"); c.sendall(f[:5]); time.sleep(0.4); c.sendall(f[5:])
    pong = c.recv(4096); got.append(("pong", pong[0] & 0x0F))
    c.sendall(frame(0x8, struct.pack("!H", 1000)))
    time.sleep(0.2); c.close()

s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
got = []; th = threading.Thread(target=server, args=(s, got), daemon=True); th.start()
ws = WebSocket(f"ws://127.0.0.1:{s.getsockname()[1]}/", timeout=5)
assert ws.recv() == "hello"
ws.send("subscribe!")
assert ws.recv() == "frag-ment"
assert len(ws.recv()) > 200000
ws.settimeout(0.1); timeouts = 0
while True:
    try: m = ws.recv(); break
    except socket.timeout: timeouts += 1
assert m == "split-across-reads" and timeouts >= 1, (m, timeouts)
try:
    ws.settimeout(2); ws.recv(); raise AssertionError("expected close")
except WebSocketClosed: pass
th.join(2)
assert got[0] == "subscribe!" and ("pong", 0xA) in got, got
print("websocket client OK: text, masked send, ping->pong, fragments, 200KB frame, split frame across timeouts", timeouts, "timeouts, close")
