# ruff: noqa: E501  # Embedded dashboard HTML/CSS/JS is intentionally kept inline.
"""Command-history dashboard with real-time command details."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST = "127.0.0.1"
PORT = int(os.getenv("REMOTE_CODING_AGENT_DASHBOARD_PORT", "3333"))
STATE_DIR = Path(
    os.getenv(
        "REMOTE_CODING_AGENT_STATE_DIR",
        str(Path.home() / ".local" / "state" / "remote-coding-agent"),
    )
).expanduser()
HISTORY_FILE = STATE_DIR / "command-history.jsonl"
CONNECTED_FILE = STATE_DIR / "control-plane-connected"

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#f5f7fb"><title>Remote Coding Agent</title>
<style>
:root{color-scheme:light;--ink:#172033;--muted:#748096;--line:#e7ebf2;--surface:#fff;--canvas:#f5f7fb;--green:#16845b;--red:#c43e4d;--amber:#a96b13}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--canvas);color:var(--ink);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.shell{max-width:1120px;margin:0 auto;padding:34px 28px 64px}.top{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:30px}
.brand{display:flex;align-items:center;gap:13px}.mark{width:42px;height:42px;border-radius:13px;background:#172033;color:white;display:grid;place-items:center;font-size:19px;font-weight:750}
h1{font-size:19px;line-height:1.2;margin:0;font-weight:720;letter-spacing:-.03em}.subtitle{color:var(--muted);font-size:12px;margin-top:4px}
.live{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;background:#fff;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:12px;font-weight:650}.dot{width:7px;height:7px;border-radius:50%;background:#d49a36}.live.connected{color:var(--green)}.live.connected .dot{background:#20a36d;box-shadow:0 0 0 4px #20a36d18}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:26px}.stat{background:#fff;border:1px solid var(--line);border-radius:15px;padding:17px 19px;box-shadow:0 2px 8px #1a2b4605}.stat-label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:700}.stat-value{font-size:25px;line-height:1.2;font-weight:750;letter-spacing:-.04em;margin-top:7px}
.section-head{display:flex;align-items:end;justify-content:space-between;margin:0 2px 12px}.section-head h2{font-size:15px;margin:0;font-weight:730}.section-head p{font-size:12px;color:var(--muted);margin:3px 0 0}#count{font-size:12px;color:var(--muted)}
.list{display:flex;flex-direction:column;gap:10px}.item{width:100%;text-align:left;background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px 18px;cursor:pointer;transition:border-color .15s,box-shadow .15s,transform .15s;color:inherit;box-shadow:0 2px 8px #1a2b4605}.item:hover{border-color:#c9d4f5;box-shadow:0 7px 22px #24385b0d;transform:translateY(-1px)}
.item-top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:9px}.command{font:600 13px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere;white-space:pre-wrap;color:#26334a}.meta{display:flex;flex-wrap:wrap;align-items:center;gap:7px 14px;color:var(--muted);font-size:11px}
.pill{display:inline-flex;align-items:center;padding:4px 8px;border-radius:999px;font-size:10px;line-height:1.2;font-weight:750;letter-spacing:.04em;text-transform:uppercase;white-space:nowrap}.success{background:#e7f6ef;color:var(--green)}.failed{background:#fff0f0;color:var(--red)}.running{background:#fff5df;color:var(--amber)}
.empty{background:#fff;border:1px dashed #d8deea;border-radius:16px;text-align:center;padding:56px 20px;color:var(--muted)}.empty-icon{width:44px;height:44px;border-radius:14px;background:#f0f3fa;color:#65738d;display:grid;place-items:center;margin:0 auto 12px;font-size:20px}.empty strong{display:block;color:#29364d;font-size:14px;margin-bottom:4px}.empty p{margin:0;font-size:12px}
.overlay{position:fixed;inset:0;background:#10182766;display:none;align-items:center;justify-content:center;padding:24px;z-index:5}.overlay.open{display:flex}.dialog{width:min(850px,100%);max-height:88vh;overflow:auto;background:#fff;border-radius:19px;box-shadow:0 25px 80px #0c173333}
.dialog-head{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--line);padding:18px 22px;display:flex;align-items:center;justify-content:space-between;z-index:1}.dialog-head h3{font-size:15px;margin:0}.close{border:0;background:#f1f3f8;border-radius:9px;width:32px;height:32px;font-size:19px;cursor:pointer;color:#536078}
.details{padding:20px 22px 24px}.detail-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:20px}.detail{padding:11px 13px;background:#f7f8fb;border:1px solid #edf0f5;border-radius:10px;min-width:0}.detail label{display:block;font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:700}.detail div{margin-top:4px;font-size:12px;font-weight:650;overflow-wrap:anywhere}.block{margin-top:17px}.block h4{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#69758b;margin:0 0 8px}.code{margin:0;padding:14px 15px;background:#f6f8fb;border:1px solid #e9edf4;border-radius:11px;color:#253149;font:12px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:340px;overflow:auto;min-height:42px}
@media(max-width:650px){.shell{padding:22px 14px 42px}.stats{gap:8px}.stat{padding:13px}.stat-value{font-size:21px}.top{align-items:flex-start}.detail-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.item{padding:14px}.overlay{padding:10px}.details{padding:16px}}
</style></head><body><main class="shell">
<header class="top"><div class="brand"><div class="mark">⌘</div><div><h1>Remote Coding Agent</h1><div class="subtitle">Command activity · Live session</div></div></div><div id="connection" class="live"><i class="dot"></i><span>Connecting</span></div></header>
<section class="stats"><div class="stat"><div class="stat-label">Total commands</div><div id="total" class="stat-value">0</div></div><div class="stat"><div class="stat-label">Successful</div><div id="success" class="stat-value">0</div></div><div class="stat"><div class="stat-label">Failed</div><div id="failed" class="stat-value">0</div></div></section>
<div class="section-head"><div><h2>Command history</h2><p>Newest activity appears first. Select a command to inspect its full details.</p></div><span id="count">0 commands</span></div>
<section id="list" class="list"><div class="empty"><div class="empty-icon">⌁</div><strong>Waiting for the first command</strong><p>Commands sent through the connected OpenAI control plane will appear here.</p></div></section></main>
<div id="overlay" class="overlay" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><section class="dialog"><header class="dialog-head"><h3 id="dialog-title">Command details</h3><button class="close" id="close" aria-label="Close">×</button></header><div id="details" class="details"></div></section></div>
<script>
const list=document.getElementById('list'),overlay=document.getElementById('overlay'),details=document.getElementById('details'),commands=new Map();let selected=null;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp=v=>{if(!v)return '—';const d=new Date(v);return Number.isNaN(d.getTime())?v:d.toLocaleString()};
const duration=c=>c.duration_seconds==null?'Running':(c.duration_seconds<1?Math.round(c.duration_seconds*1000)+' ms':c.duration_seconds.toFixed(2)+' s');
function merge(e){if(!e||!e.id)return;commands.set(e.id,{...(commands.get(e.id)||{}),...e});render();if(selected===e.id)showDetails(commands.get(e.id));}
function render(){const all=[...commands.values()].sort((a,b)=>String(b.started_at||'').localeCompare(String(a.started_at||'')));document.getElementById('total').textContent=all.length;document.getElementById('success').textContent=all.filter(c=>c.status==='success').length;document.getElementById('failed').textContent=all.filter(c=>c.status==='failed').length;document.getElementById('count').textContent=all.length+' command'+(all.length===1?'':'s');
if(!all.length){list.innerHTML='<div class="empty"><div class="empty-icon">⌁</div><strong>Waiting for the first command</strong><p>Commands sent through the connected OpenAI control plane will appear here.</p></div>';return;}
list.innerHTML=all.map(c=>{const st=c.status||'running',cl=st==='success'?'success':st==='failed'?'failed':'running';return '<button class="item" data-id="'+esc(c.id)+'"><div class="item-top"><code class="command">'+esc(c.command||'Command')+'</code><span class="pill '+cl+'">'+esc(st)+'</span></div><div class="meta"><span>◷ '+esc(stamp(c.started_at))+'</span><span>⌁ '+esc(c.cwd||'workspace root')+'</span><span>◴ '+esc(duration(c))+'</span>'+(c.exit_code==null?'':'<span>Exit '+esc(c.exit_code)+'</span>')+'</div></button>';}).join('');
list.querySelectorAll('[data-id]').forEach(el=>el.addEventListener('click',()=>{selected=el.dataset.id;showDetails(commands.get(selected));overlay.classList.add('open');}));}
function block(title,value){return '<section class="block"><h4>'+title+'</h4><pre class="code">'+esc(value||'No output')+'</pre></section>';}
function showDetails(c){if(!c)return;document.getElementById('dialog-title').textContent='Command · '+c.id;details.innerHTML='<div class="detail-grid">'+[['Status',c.status||'running'],['Started',stamp(c.started_at)],['Finished',stamp(c.finished_at)],['Working directory',c.cwd||'workspace root'],['Timeout',c.timeout_seconds==null?'—':c.timeout_seconds+' s'],['Duration',duration(c)],['Exit code',c.exit_code==null?'—':c.exit_code],['Timed out',c.timed_out?'Yes':'No'],['Output truncated',c.output_truncated?'Yes':'No']].map(([k,v])=>'<div class="detail"><label>'+k+'</label><div>'+esc(v)+'</div></div>').join('')+'</div>'+block('Exact command',c.command)+block('Standard output',c.stdout)+block('Standard error',c.stderr);}
function close(){overlay.classList.remove('open');selected=null;}document.getElementById('close').onclick=close;overlay.addEventListener('click',e=>{if(e.target===overlay)close();});document.addEventListener('keydown',e=>{if(e.key==='Escape')close();});
const stream=new EventSource('/command-events');stream.onopen=()=>{const e=document.getElementById('connection');e.classList.add('connected');e.querySelector('span').textContent='Live updates';};stream.onerror=()=>{const e=document.getElementById('connection');e.classList.remove('connected');e.querySelector('span').textContent='Reconnecting';};stream.onmessage=e=>{try{merge(JSON.parse(e.data));}catch{}};
</script></body></html>"""


def _timestamp(value: object) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _history_events() -> list[dict[str, object]]:
    if not HISTORY_FILE.exists():
        return []
    merged: dict[str, dict[str, object]] = {}
    try:
        with HISTORY_FILE.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and isinstance(event.get("id"), str):
                    merged.setdefault(event["id"], {}).update(event)
    except OSError:
        return []
    try:
        cutoff = CONNECTED_FILE.stat().st_mtime
    except OSError:
        return []
    return [event for event in merged.values() if _timestamp(event.get("started_at")) >= cutoff]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RemoteAgentDashboard/2"

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
        elif path == "/healthz":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/command-events":
            self._stream_commands()
        else:
            self.send_error(404)

    def _send_event(self, event: dict[str, object]) -> None:
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(f"data: {payload}\n\n".encode())
        self.wfile.flush()

    def _stream_commands(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            delivered: set[tuple[str, str]] = set()
            for event in _history_events():
                self._send_event(event)
                delivered.add((str(event.get("id")), str(event.get("finished_at", ""))))
            heartbeat = time.monotonic()
            while True:
                for event in _history_events():
                    key = (str(event.get("id")), str(event.get("finished_at", "")))
                    if key not in delivered:
                        self._send_event(event)
                        delivered.add(key)
                if len(delivered) > 3000:
                    delivered = set(list(delivered)[-1500:])
                now = time.monotonic()
                if now - heartbeat >= 15:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    heartbeat = now
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def _serve() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
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

