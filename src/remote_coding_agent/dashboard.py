"""Minimal localhost dashboard that streams the live launcher log."""

# ruff: noqa: E501  # The embedded HTML/CSS/JS is intentionally kept in one static string.

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST = "127.0.0.1"
PORT = int(os.getenv("REMOTE_CODING_AGENT_DASHBOARD_PORT", "3333"))
LIVE_LOG_FILE = Path(os.getenv(
    "REMOTE_CODING_AGENT_LIVE_LOG_FILE",
    str(Path.home() / ".local" / "state" / "remote-coding-agent" / "live.log"),
)).expanduser()

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#000000">
<title>Remote Coding Agent | Live Logs</title>
<style>
:root{color-scheme:dark;background:#000;color:#fff}
*{box-sizing:border-box}
html,body{height:100%;margin:0;background:#000;color:#fff}
body{font:13px/1.55 ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace}
main{height:100%;display:flex;flex-direction:column;background:#000}
header{height:48px;min-height:48px;padding:0 18px;border-bottom:1px solid #333;display:flex;align-items:center;justify-content:space-between;background:#000}
h1{font:600 12px/1 ui-monospace,SFMono-Regular,Consolas,monospace;letter-spacing:.12em;margin:0;color:#fff}
#connection{font-size:11px;color:#fff;letter-spacing:.08em}
#logs{flex:1;overflow:auto;margin:0;padding:16px 18px 28px;white-space:pre-wrap;overflow-wrap:anywhere;color:#fff;background:#000;scrollbar-color:#555 #000}
@media(max-width:600px){header{padding:0 12px}#logs{padding:12px;font-size:12px}}
</style>
</head>
<body>
<main>
<header><h1>REMOTE CODING AGENT / LIVE LOG</h1><span id="connection">CONNECTING</span></header>
<pre id="logs" aria-live="off"></pre>
</main>
<script>
const output=document.getElementById('logs');
const connection=document.getElementById('connection');
const stream=new EventSource('/events');
stream.onopen=()=>{connection.textContent='LIVE'};
stream.onmessage=(event)=>{
  let line;
  try{line=JSON.parse(event.data)}catch{return}
  output.textContent+=line+'\n';
  const lines=output.textContent.split('\n');
  if(lines.length>5000)output.textContent=lines.slice(-4000).join('\n');
  output.scrollTop=output.scrollHeight;
};
stream.onerror=()=>{connection.textContent='RECONNECTING'};
</script>
</body>
</html>"""


def _initial_offset(log_path: Path, last_event_id: str | None) -> int:
    """Resume after the last delivered line, or start with a recent log tail."""
    size = log_path.stat().st_size
    if last_event_id:
        try:
            offset = int(last_event_id)
        except ValueError:
            offset = -1
        if 0 <= offset <= size:
            return offset
    start = max(0, size - 65_536)
    if start == 0:
        return 0
    with log_path.open("rb") as stream:
        stream.seek(start)
        stream.readline()
        return stream.tell()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RemoteAgentLogs/1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/healthz":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/events":
            self._stream_logs()
            return
        self.send_error(404)

    def _stream_logs(self) -> None:
        LIVE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LIVE_LOG_FILE.touch(mode=0o600, exist_ok=True)
        try:
            os.chmod(LIVE_LOG_FILE, 0o600)
            offset = _initial_offset(
                LIVE_LOG_FILE, self.headers.get("Last-Event-ID")
            )
            with LIVE_LOG_FILE.open("rb") as stream:
                stream.seek(offset)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                heartbeat = time.monotonic()
                while True:
                    current_size = LIVE_LOG_FILE.stat().st_size
                    if current_size < offset:
                        stream.seek(0)
                        offset = 0
                    line = stream.readline()
                    if line:
                        offset = stream.tell()
                        value = line.decode("utf-8", errors="replace").rstrip("\r\n")
                        payload = json.dumps(value, ensure_ascii=False)
                        event = f"id: {offset}\ndata: {payload}\n\n".encode()
                        self.wfile.write(event)
                        self.wfile.flush()
                        heartbeat = time.monotonic()
                    else:
                        now = time.monotonic()
                        if now - heartbeat >= 15:
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                            heartbeat = now
                        time.sleep(0.02)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def _serve() -> None:
    LIVE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIVE_LOG_FILE.touch(mode=0o600, exist_ok=True)
    os.chmod(LIVE_LOG_FILE, 0o600)
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(4, 0, 0, 0, 0) != 0:
            raise OSError("could not disable dashboard process inspection")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    server.serve_forever(poll_interval=0.1)



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    _serve()


if __name__ == "__main__":
    main()
