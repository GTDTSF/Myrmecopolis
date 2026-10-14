from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .sim import World

STATIC = Path(__file__).resolve().parent.parent.parent / "static"


class Handler(BaseHTTPRequestHandler):
    world: World

    def log_message(self, *args) -> None:
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")  # 静态页/脚本不缓存：改前端后普通刷新即生效
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/sprites":
            self._send(200, (STATIC / "sprites.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/gallery":
            self._send(200, (STATIC / "gallery.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/ants.js":
            self._send(200, (STATIC / "ants.js").read_bytes(), "text/javascript; charset=utf-8")
        elif path == "/terrain":
            w = self.world
            body = json.dumps({"v": w.terrain_version, "t": w.terrain_str()}, ensure_ascii=False)
            self._send(200, body.encode(), "application/json")
        elif path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            while True:
                try:
                    data = json.dumps(self.world.frame(), ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(1 / 30)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
        else:
            self._send(404, b"not found", "text/plain")


def serve(world: World, port: int = 8000) -> None:
    Handler.world = world
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"地下蚁国 running: http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
