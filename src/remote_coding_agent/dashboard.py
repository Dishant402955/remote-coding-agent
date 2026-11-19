"""Tiny, dependency-free local dashboard for the Remote Coding Agent."""

# ruff: noqa: E501  # The embedded HTML/CSS/JS is intentionally kept in one static string.

from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

STATE_DIR = Path(os.getenv(
    "REMOTE_CODING_AGENT_STATE_DIR",
    str(Path.home() / ".local" / "state" / "remote-coding-agent"),
)).expanduser()
CONFIG_FILE = STATE_DIR / "dashboard.json"
HISTORY_FILE = STATE_DIR / "command-history.jsonl"
START_FILE = STATE_DIR / "start-requested"
STOP_FILE = STATE_DIR / "stop-requested"
TUNNEL_USER = "remote-coding-agent-tunnel"
STORE_HELPER = "/usr/local/lib/remote-coding-agent/store-tunnel-credentials.sh"
SESSION_FILE = STATE_DIR / "agent-session.json"
SESSION_HISTORY_FILE = STATE_DIR / "session-history.jsonl"
HOST = "127.0.0.1"
PORT = int(os.getenv("REMOTE_CODING_AGENT_DASHBOARD_PORT", "3333"))
MCP_PORT = int(os.getenv("REMOTE_CODING_AGENT_PORT", "8000"))
HEALTH_PORT = int(os.getenv("REMOTE_CODING_AGENT_HEALTH_PORT", "18080"))
MAX_HISTORY = 500
SESSIONS: dict[str, float] = {}
LOCK = threading.RLock()

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#090d16"><title>Remote Agent</title>
<style>
:root{color-scheme:dark;--bg:#090d16;--panel:#101827;--panel2:#0d1421;--line:#202c40;--muted:#8996aa;--text:#e8eef8;--accent:#8b9cff;--green:#4adea1;--red:#fb7185;--amber:#fbbf24}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 50% -30%,#1b2b49 0,transparent 55%),var(--bg);color:var(--text);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
button,input{font:inherit}button{cursor:pointer}.shell{max-width:1180px;margin:auto;padding:28px 22px 60px}.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:28px}.brand{display:flex;align-items:center;gap:12px}.logo{width:38px;height:38px;border-radius:12px;background:linear-gradient(145deg,#8295ff,#5965d8);display:grid;place-items:center;color:white;font-weight:800;font-size:17px;box-shadow:0 8px 28px #6675ff35}.brand h1{font-size:16px;margin:0;letter-spacing:.2px}.brand small{display:block;color:var(--muted);font-size:12px}.topright{display:flex;gap:10px;align-items:center}.pill{border:1px solid var(--line);border-radius:999px;padding:6px 11px;color:var(--muted);font-size:12px}.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--amber);margin-right:7px}.dot.ok{background:var(--green);box-shadow:0 0 12px #4adea166}.dot.bad{background:var(--red)}
h2{font-size:21px;margin:0 0 5px}.sub{color:var(--muted);margin:0 0 22px}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.card{background:linear-gradient(180deg,#121d2d,#0f1725);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:0 10px 30px #0002}.metric-label{color:var(--muted);font-size:12px}.metric{font-size:23px;font-weight:650;margin-top:8px;letter-spacing:-.5px}.metric-note{color:var(--muted);font-size:12px;margin-top:2px}.section{margin-top:24px}.sectionhead{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}.sectionhead h3{font-size:14px;margin:0}.tools{display:flex;gap:8px;align-items:center}.search{background:var(--panel2);border:1px solid var(--line);color:var(--text);padding:9px 12px;border-radius:9px;outline:none;width:220px}.search:focus,input:focus{border-color:#7183ff}.btn{background:#7484ff;color:white;border:0;padding:9px 13px;border-radius:9px;font-weight:600}.btn.secondary{background:#1a2639;color:#dbe4f5;border:1px solid var(--line)}.btn:hover{filter:brightness(1.1)}
.history{overflow:hidden;padding:0}.historyrow{display:grid;grid-template-columns:150px minmax(0,1fr) 105px;gap:16px;align-items:center;padding:14px 18px;border-bottom:1px solid #1c293b}.historyrow:last-child{border:0}.time{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}.cmd{font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere;color:#dbe5f6}.cwd{font:11px ui-monospace,monospace;color:#71819b;margin-top:3px}.state{font-size:11px;text-align:right;color:var(--muted)}.state.ok{color:var(--green)}.state.fail{color:var(--red)}.empty{padding:34px;text-align:center;color:var(--muted)}.foot{color:#63728a;font-size:11px;margin-top:16px;display:flex;justify-content:space-between}
.authwrap{min-height:100vh;display:grid;place-items:center;padding:20px}.auth{width:min(440px,100%);background:#101827;border:1px solid var(--line);border-radius:18px;padding:28px;box-shadow:0 25px 80px #0007}.auth h2{margin-bottom:4px}.auth p{color:var(--muted);margin:0 0 20px}.field{display:block;margin:13px 0}.field span{display:block;color:#aab7ca;font-size:12px;margin-bottom:6px}.field input{width:100%;background:#0a1220;border:1px solid var(--line);border-radius:9px;padding:11px 12px;color:var(--text);outline:none}.hint{font-size:11px;color:#7f8ca1;margin-top:5px}.wide{width:100%;margin-top:10px}.error{color:var(--red);font-size:12px;min-height:18px;margin-top:8px}.tabs{display:flex;gap:7px}.tab{border:1px solid var(--line);background:#0c1422;color:#aab7ca;padding:7px 11px;border-radius:8px;font-size:12px}.tab.active{background:#202b4b;color:#dce3ff;border-color:#5365bf}
@media(max-width:700px){.shell{padding:18px 14px}.grid{grid-template-columns:1fr}.historyrow{grid-template-columns:1fr auto;gap:5px}.time{grid-column:1/-1}.cmd{grid-column:1}.state{grid-column:2;grid-row:2}.top{align-items:flex-start}.search{width:145px}.sectionhead{align-items:flex-start;gap:10px;flex-wrap:wrap}}
</style></head><body><div id="app"></div>
<script>
const app=document.getElementById('app');let tokenReady=false,items=[],statusData={};
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'Content-Type':'application/json',...(opts.headers||{})}});let d={};try{d=await r.json()}catch{}if(!r.ok)throw Error(d.error||('Request failed ('+r.status+')'));return d}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function authPage(setup){app.innerHTML='<div class="authwrap"><div class="auth"><div class="brand"><div class="logo">R</div><div><h1>Remote Agent</h1><small>Local control center</small></div></div><h2 style="margin-top:26px">'+(setup?'Set up your dashboard':'Welcome back')+'</h2><p>'+(setup?'Create a dashboard password and add your existing Secure MCP Tunnel credentials.':'Sign in to manage your agent and view command history.')+'</p><form id="authform">'+(setup?'<label class="field"><span>Dashboard password</span><input name="password" type="password" minlength="12" autocomplete="new-password" required><div class="hint">Use at least 12 characters. This protects the local dashboard.</div></label><label class="field"><span>Confirm password</span><input name="confirm" type="password" minlength="12" required></label>':'<label class="field"><span>Dashboard password</span><input name="password" type="password" autocomplete="current-password" required></label>')+(setup?'<label class="field"><span>Existing Tunnel ID</span><input name="tunnel_id" autocomplete="off" required></label><label class="field"><span>Runtime API key</span><input name="api_key" type="password" autocomplete="off" required><div class="hint">Stored under the isolated tunnel service account. Never committed or placed in your home directory.</div></label>':'')+'<button class="btn wide" type="submit">'+(setup?'Save securely and continue':'Unlock dashboard')+'</button><div class="error" id="error"></div></form></div></div>';
document.getElementById('authform').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target),body={password:f.get('password')};if(setup){if(body.password!==f.get('confirm')){document.getElementById('error').textContent='Passwords do not match';return}body.tunnel_id=f.get('tunnel_id');body.api_key=f.get('api_key')}try{await api(setup?'/api/setup':'/api/login',{method:'POST',body:JSON.stringify(body)});await boot()}catch(x){document.getElementById('error').textContent=x.message}}
}
let startRequested=false;function dashboard(){app.innerHTML='<div class="shell"><header class="top"><div class="brand"><div class="logo">R</div><div><h1>Remote Agent</h1><small>Local control center</small></div></div><div class="topright"><span class="pill" id="clock">--:--</span><button class="btn secondary" id="settings">Settings</button><button class="btn" id="start">Start agent</button><button class="btn secondary" id="stop" hidden>Stop agent</button><button class="btn secondary" id="logout">Lock</button></div></header><h2>Agent overview</h2><p class="sub">Live runtime health and a searchable history of commands executed through MCP.</p><div class="grid"><div class="card"><div class="metric-label">Agent session</div><div class="metric" id="session">Checking</div><div class="metric-note" id="sessionnote">Session uptime</div></div><div class="card"><div class="metric-label">MCP server</div><div class="metric" id="mcp">Checking</div><div class="metric-note" id="mcpnote">127.0.0.1</div></div><div class="card"><div class="metric-label">Secure tunnel</div><div class="metric" id="tunnel">Checking</div><div class="metric-note" id="tunnelnote">Control-plane health</div></div><div class="card"><div class="metric-label">Commands recorded</div><div class="metric" id="count">0</div><div class="metric-note" id="last">No command activity yet</div></div></div><section class="section"><div class="sectionhead"><h3>Command history</h3><div class="tools"><input id="search" class="search" placeholder="Filter commands…"><button id="refresh" class="btn secondary">Refresh</button></div></div><div class="card history" id="history"><div class="empty">Loading command history…</div></div></section><section class="section"><div class="sectionhead"><h3>Recent sessions</h3></div><div class="card history" id="sessions"><div class="empty">Loading session history…</div></div></section><div class="foot"><span>Dashboard is bound to localhost only</span><span>Auto-refreshes every 3 seconds</span></div></div>';
document.getElementById('logout').onclick=async()=>{await api('/api/logout',{method:'POST',body:'{}'});authPage(false)};
document.getElementById('settings').onclick=settingsPage;document.getElementById('start').onclick=async()=>{try{await api('/api/start',{method:'POST',body:'{}'});startRequested=true;document.getElementById('start').textContent='Starting…';document.getElementById('start').disabled=true}catch(e){alert(e.message)}};document.getElementById('stop').onclick=async()=>{if(!confirm('Stop the agent and its Secure MCP Tunnel?'))return;try{await api('/api/stop',{method:'POST',body:'{}'});document.getElementById('stop').textContent='Stopping…';document.getElementById('stop').disabled=true}catch(e){alert(e.message)}};document.getElementById('refresh').onclick=refresh;document.getElementById('search').oninput=renderHistory;refresh();setInterval(refresh,3000);setInterval(()=>document.getElementById('clock').textContent=new Date().toLocaleTimeString(),1000)}
function renderHistory(){let q=(document.getElementById('search')?.value||'').toLowerCase();let rows=items.filter(x=>(x.command+' '+x.cwd).toLowerCase().includes(q));let el=document.getElementById('history');if(!rows.length){el.innerHTML='<div class="empty">No matching commands yet. Run a command through your MCP agent and it will appear here.</div>';return}el.innerHTML=rows.map(x=>'<div class="historyrow"><div class="time">'+esc(new Date(x.started_at).toLocaleString())+'</div><div><div class="cmd">$ '+esc(x.command)+'</div><div class="cwd">'+esc(x.cwd||'workspace root')+' · '+(x.duration_seconds==null?'running':esc(Number(x.duration_seconds).toFixed(2)+'s'))+'</div></div><div class="state '+(x.status==='success'?'ok':x.status==='running'?'':'fail')+'">'+(x.status==='success'?'EXIT 0':x.status==='running'?'RUNNING':'EXIT '+esc(x.exit_code??'ERR'))+'</div></div>').join('')}
function renderSessions(sessions){let el=document.getElementById('sessions');if(!el)return;if(!sessions.length){el.innerHTML='<div class="empty">No previous sessions recorded.</div>';return}el.innerHTML=sessions.map(x=>'<div class="historyrow"><div class="time">'+esc(new Date(x.started_at).toLocaleString())+'</div><div><div class="cmd">'+esc(x.session_id)+'</div><div class="cwd">Updated '+esc(new Date(x.updated_at).toLocaleString())+'</div></div><div class="state '+(x.status==='running'?'ok':x.status==='stopped'?'':'fail')+'">'+esc(String(x.status||'unknown').toUpperCase())+'</div></div>').join('')}
function durationLabel(seconds){if(seconds==null)return 'Not running';let h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),v=seconds%60;return h+'h '+m+'m '+v+'s'}
async function refresh(){try{let d=await api('/api/status');statusData=d;if(d.session?.status==='stopped'&&!d.mcp)startRequested=false;let sessionStatus=d.session?.status||'stopped';document.getElementById('start').hidden=!!d.mcp||sessionStatus==='running'||sessionStatus==='starting'||startRequested;document.getElementById('start').disabled=sessionStatus==='starting'||startRequested;document.getElementById('stop').hidden=!(d.mcp||d.tunnel||sessionStatus==='running'||sessionStatus==='starting');document.getElementById('stop').disabled=sessionStatus==='stopping';let sessionTone=sessionStatus==='running'?'ok':sessionStatus==='starting'?'':'bad';document.getElementById('session').innerHTML='<span class="dot '+sessionTone+'"></span>'+(sessionStatus==='running'?'Running':sessionStatus==='starting'?'Starting':'Stopped');document.getElementById('sessionnote').textContent=sessionStatus==='running'?'Uptime '+durationLabel(d.uptime_seconds):sessionStatus==='starting'?'Launcher is preparing the agent':'No active agent session';document.getElementById('mcp').innerHTML='<span class="dot '+(d.mcp?'ok':'bad')+'"></span>'+(d.mcp?'Online':'Offline');document.getElementById('mcpnote').textContent='127.0.0.1:'+d.mcp_port;document.getElementById('tunnel').innerHTML='<span class="dot '+(d.tunnel?'ok':'bad')+'"></span>'+(d.tunnel?'Connected':'Offline');document.getElementById('tunnelnote').textContent=d.control_plane?'Control plane healthy':'Waiting for tunnel';items=d.history||[];renderSessions(d.sessions||[]);document.getElementById('count').textContent=d.total_commands;document.getElementById('last').textContent=d.last_command?'Last activity '+new Date(d.last_command).toLocaleTimeString():'No command activity yet';renderHistory()}catch(e){if(e.message.includes('Unauthorized'))authPage(false)}}
function settingsPage(){app.innerHTML='<div class="shell"><header class="top"><div class="brand"><div class="logo">R</div><div><h1>Remote Agent</h1><small>Settings</small></div></div><button class="btn secondary" id="back">Back to dashboard</button></header><h2>Dashboard settings</h2><p class="sub">Update your local dashboard password or Secure MCP Tunnel credentials.</p><div class="card" style="max-width:620px"><form id="settingsform"><label class="field"><span>Current dashboard password</span><input name="current_password" type="password" required></label><label class="field"><span>New dashboard password (optional)</span><input name="new_password" type="password" minlength="12" placeholder="Leave blank to keep current"></label><label class="field"><span>Tunnel ID</span><input name="tunnel_id" autocomplete="off" required></label><label class="field"><span>Runtime API key</span><input name="api_key" type="password" placeholder="Leave blank to keep saved key"></label><div class="hint">Tunnel credentials are stored under the isolated tunnel service account, not your normal home directory. Key or tunnel ID changes are saved immediately and take effect after the next agent restart. Password changes take effect immediately.</div><button class="btn wide">Save settings</button><div class="error" id="error"></div></form></div></div>';api('/api/settings').then(d=>{document.querySelector('[name=tunnel_id]').value=d.tunnel_id||''}).catch(()=>{});document.getElementById('back').onclick=dashboard;document.getElementById('settingsform').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target),b=Object.fromEntries(f.entries());try{await api('/api/settings',{method:'POST',body:JSON.stringify(b)});dashboard()}catch(x){document.getElementById('error').textContent=x.message}}}
async function boot(){try{let d=await api('/api/auth-state');if(!d.configured){authPage(true);return}if(!d.authenticated){authPage(false);return}dashboard()}catch(e){authPage(false)}}
boot();
</script></body></html>"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _load_config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_config(data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    temp = CONFIG_FILE.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, CONFIG_FILE)
    os.chmod(CONFIG_FILE, 0o600)


def _store_tunnel_credentials(tunnel_id: str, api_key: str) -> None:
    """Delegate credential writes to a root-installed helper running as tunnel user."""
    payload = json.dumps({"tunnel_id": tunnel_id, "api_key": api_key})
    try:
        result = subprocess.run(
            ["sudo", "-n", "-u", TUNNEL_USER, "--", STORE_HELPER, "write"],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Could not securely store tunnel credentials") from exc
    if result.returncode != 0:
        # Surface the helper's actionable diagnostic (for example, sudoers or
        # directory ownership) instead of sending users in a blind reinstall loop.
        detail = (result.stderr or result.stdout or "").strip()
        detail = detail[:400] if detail else f"helper exited with status {result.returncode}"
        raise RuntimeError(f"Could not securely store tunnel credentials: {detail}")


def _read_tunnel_credentials() -> dict[str, str]:
    """Read the isolated service account's credentials through sudo."""
    try:
        result = subprocess.run(
            ["sudo", "-n", "-u", TUNNEL_USER, "--", STORE_HELPER, "read"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Tunnel credentials are not available") from exc
    if result.returncode != 0:
        raise RuntimeError("Tunnel credentials are not configured")
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Tunnel credentials file is invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Tunnel credentials file is invalid")
    if not value.get("tunnel_id") or not value.get("api_key"):
        raise RuntimeError("Tunnel credentials are incomplete")
    return {"tunnel_id": str(value["tunnel_id"]), "api_key": str(value["api_key"])}


def _password_hash(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return salt.hex(), digest.hex()


def _verify_password(password: str, config: dict[str, Any]) -> bool:
    try:
        _, digest = _password_hash(password, bytes.fromhex(config["password_salt"]))
        return hmac.compare_digest(digest, config["password_hash"])
    except (KeyError, ValueError):
        return False


def _session_history() -> list[dict[str, Any]]:
    """Merge lifecycle events into the latest persistent agent sessions."""
    try:
        lines = SESSION_HISTORY_FILE.read_text(encoding="utf-8").splitlines()[-200:]
    except OSError:
        return []
    sessions: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or not event.get("session_id"):
            continue
        item = sessions.setdefault(str(event["session_id"]), {})
        item.update(event)
    result = list(sessions.values())
    result.sort(key=lambda item: item.get("started_at", ""), reverse=True)
    return result[:20]


def _history() -> list[dict[str, Any]]:
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()[-MAX_HISTORY * 2:]
    except OSError:
        return []
    commands: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = commands.setdefault(event.get("id", secrets.token_hex(4)), {})
        item.update(event)
    result = list(commands.values())
    result.sort(key=lambda item: item.get("started_at", ""), reverse=True)
    return result[:MAX_HISTORY]


def _probe(url: str) -> bool:
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=0.8) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def _status() -> dict[str, Any]:
    history = _history()
    mcp = _probe(f"http://127.0.0.1:{MCP_PORT}/healthz")
    tunnel_live = _probe(f"http://127.0.0.1:{HEALTH_PORT}/readyz")
    control = False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{HEALTH_PORT}/health/control-plane", timeout=0.8) as response:
            payload = json.loads(response.read(65536))
            details = payload.get("details") or {}
            control = payload.get("status") == "ok" and bool(details.get("last_success")) and details.get("consecutive_failures", 0) == 0
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        pass
    try:
        session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        session = {"status": "stopped"}
    started_at = session.get("started_at")
    uptime_seconds = None
    if session.get("status") == "running" and started_at:
        try:
            uptime_seconds = max(
                0,
                int((datetime.now(UTC) - datetime.fromisoformat(started_at)).total_seconds()),
            )
        except ValueError:
            pass
    return {
        "mcp": mcp, "tunnel": tunnel_live and control, "control_plane": control,
        "mcp_port": MCP_PORT, "history": history, "total_commands": len(history),
        "last_command": history[0].get("started_at") if history else None,
        "session": session, "uptime_seconds": uptime_seconds,
        "sessions": _session_history(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "RemoteAgentDashboard/1"

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _json(self, status: int, data: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 16_384:
            raise ValueError("Request body is too large")
        if length <= 0:
            return {}
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def _session(self) -> bool:
        cookie = self.headers.get("Cookie", "")
        value = next((part.strip()[11:] for part in cookie.split(";") if part.strip().startswith("ra_session=")), "")
        if not value:
            return False
        with LOCK:
            expiry = SESSIONS.get(value)
            if expiry and expiry > time.time():
                SESSIONS[value] = time.time() + 3600
                return True
            SESSIONS.pop(value, None)
        return False

    def _cookie(self) -> str:
        sid = secrets.token_urlsafe(32)
        with LOCK:
            SESSIONS[sid] = time.time() + 3600
        return f"ra_session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600"

    def _clear_cookie(self) -> str:
        return "ra_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        return origin in (f"http://{host}", f"https://{host}") and host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/auth-state":
            config = _load_config()
            self._json(200, {"configured": bool(config.get("password_hash") and config.get("tunnel_id")), "authenticated": self._session()})
            return
        if not self._session():
            self._json(401, {"error": "Unauthorized"})
            return
        if path == "/api/status":
            self._json(200, _status())
        elif path == "/api/settings":
            self._json(200, {"tunnel_id": _load_config().get("tunnel_id", "")})
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not self._same_origin():
            self._json(403, {"error": "Invalid request origin"})
            return
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "Invalid JSON"})
            return
        config = _load_config()
        if path == "/api/setup":
            if config.get("password_hash"):
                self._json(409, {"error": "Dashboard is already configured"})
                return
            password, tunnel_id, api_key = (str(body.get(k, "")) for k in ("password", "tunnel_id", "api_key"))
            if len(password) < 12 or not tunnel_id.strip() or len(api_key) < 16:
                self._json(400, {"error": "Use a 12+ character password and provide a valid tunnel ID and runtime API key"})
                return
            try:
                _store_tunnel_credentials(tunnel_id.strip(), api_key.strip())
            except RuntimeError as exc:
                self._json(503, {"error": str(exc)})
                return
            salt, digest = _password_hash(password)
            _save_config({
                "password_salt": salt,
                "password_hash": digest,
                "tunnel_id": tunnel_id.strip(),
                "updated_at": _now(),
            })
            self._json(200, {"ok": True}, {"Set-Cookie": self._cookie()})
            return
        if path == "/api/login":
            if not config.get("password_hash") or not _verify_password(str(body.get("password", "")), config):
                self._json(401, {"error": "Incorrect password"})
                return
            self._json(200, {"ok": True}, {"Set-Cookie": self._cookie()})
            return
        if not self._session():
            self._json(401, {"error": "Unauthorized"})
            return
        if path == "/api/start":
            try:
                _read_tunnel_credentials()
            except RuntimeError as exc:
                self._json(503, {"error": str(exc)})
                return
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            START_FILE.write_text(_now(), encoding="utf-8")
            os.chmod(START_FILE, 0o600)
            self._json(200, {"ok": True})
            return
        if path == "/api/stop":
            try:
                session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                session = {"status": "stopped"}
            if session.get("status") not in ("starting", "running"):
                self._json(409, {"error": "Agent is not running"})
                return
            session["status"] = "stopping"
            session["updated_at"] = _now()
            temporary = SESSION_FILE.with_suffix(".tmp")
            temporary.write_text(json.dumps(session), encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, SESSION_FILE)
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            STOP_FILE.write_text(_now(), encoding="utf-8")
            os.chmod(STOP_FILE, 0o600)
            self._json(200, {"ok": True})
            return
        if path == "/api/logout":
            cookie = self.headers.get("Cookie", "")
            sid = next((part.strip()[11:] for part in cookie.split(";") if part.strip().startswith("ra_session=")), "")
            with LOCK:
                SESSIONS.pop(sid, None)
            self._json(200, {"ok": True}, {"Set-Cookie": self._clear_cookie()})
            return
        if path == "/api/settings":
            if not _verify_password(str(body.get("current_password", "")), config):
                self._json(401, {"error": "Current password is incorrect"})
                return
            tunnel_id = str(body.get("tunnel_id", "")).strip()
            api_key_input = str(body.get("api_key", "")).strip()
            new_password = str(body.get("new_password", ""))
            if not tunnel_id or (api_key_input and len(api_key_input) < 16) or (
                new_password and len(new_password) < 12
            ):
                self._json(
                    400,
                    {"error": "Tunnel ID is required; API key must be valid; new password needs 12+ characters"},
                )
                return
            if tunnel_id != config.get("tunnel_id") or api_key_input:
                try:
                    _store_tunnel_credentials(tunnel_id, api_key_input)
                except RuntimeError as exc:
                    self._json(503, {"error": str(exc)})
                    return
            updated = dict(config, tunnel_id=tunnel_id, updated_at=_now())
            if new_password:
                salt, digest = _password_hash(new_password)
                updated.update(password_salt=salt, password_hash=digest)
            _save_config(updated)
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "Not found"})


def _serve() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(4, 0, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "could not disable dashboard process inspection")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    server.serve_forever(poll_interval=0.5)


def _wait_for_start(timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if STOP_FILE.exists():
            STOP_FILE.unlink(missing_ok=True)
            raise SystemExit(130)
        if START_FILE.exists():
            START_FILE.unlink(missing_ok=True)
            return
        time.sleep(0.5)
    raise SystemExit("Dashboard start request timed out.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-for-start", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--read-credentials", action="store_true")
    args = parser.parse_args()
    if args.read_credentials:
        credentials = _read_tunnel_credentials()
        print(credentials["tunnel_id"])
        print(credentials["api_key"])
    elif args.wait_for_start:
        _wait_for_start(args.timeout_seconds)
    else:
        _serve()


if __name__ == "__main__":
    main()
