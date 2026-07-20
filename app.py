import json
import os
import signal
import subprocess
import hashlib
import hmac
import time
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, session, redirect, url_for, render_template_string

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "kick-streamer-secret-change-me")

CONFIG_FILE = Path(__file__).parent / "config.json"
STOP_FILE = Path("/tmp/kick-stream.stop")
PROCESS = None

PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "admin")
KICK_RTMP = os.environ.get("KICK_RTMP", "rtmp://push.kick.com/live")


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {
        "stream_key": "",
        "video_url": "",
        "kick_rtmp": KICK_RTMP,
        "kick_username": "",
    }


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


LOGIN_HTML = """<!DOCTYPE html><html><head><title>Kick Streamer - Login</title>
<style>
body{background:#0a0a0a;color:#fff;font-family:system-ui;display:flex;justify-content:center;align-items:center;height:100vh;margin:0}
.login-box{background:#111;padding:40px;border-radius:12px;width:340px;box-shadow:0 0 30px rgba(53,221,110,.15)}
h1{text-align:center;font-size:1.4rem;margin-bottom:8px;color:#53dd6e}
p{text-align:center;color:#888;font-size:.85rem;margin-bottom:24px}
input{width:100%;padding:12px;border:1px solid #333;border-radius:8px;background:#1a1a2e;color:#fff;font-size:1rem;margin-bottom:16px;box-sizing:border-box}
button{width:100%;padding:12px;background:#53dd6e;color:#000;border:none;border-radius:8px;font-size:1rem;cursor:pointer;font-weight:600}
button:hover{background:#3fcf5c}
.err{color:#ef4444;text-align:center;margin-bottom:12px;font-size:.85rem}
</style></head><body>
<div class="login-box">
<h1>Kick Streamer</h1><p>Enter password to continue</p>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<form method="POST"><input name="password" type="password" placeholder="Password" autofocus>
<button type="submit">Login</button></form></div></body></html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == PANEL_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("dashboard"))
        error = "Wrong password"
    return render_template_string(LOGIN_HTML, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


DASHBOARD_HTML = """<!DOCTYPE html><html><head><title>Kick Streamer Panel</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0a0a;color:#fff;font-family:system-ui}
.header{background:#111;padding:16px 24px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #222}
.header h1{font-size:1.2rem;color:#53dd6e}.header a{color:#888;text-decoration:none;font-size:.85rem}
.container{max-width:640px;margin:40px auto;padding:0 24px}
.card{background:#111;border-radius:12px;padding:24px;border:1px solid #222;margin-bottom:20px}
.card h2{font-size:1.1rem;margin-bottom:16px;color:#53dd6e}
label{display:block;font-size:.75rem;color:#888;margin-bottom:4px;margin-top:14px}
input,select{width:100%;padding:10px;border:1px solid #333;border-radius:6px;background:#1a1a2e;color:#fff;font-size:.9rem;box-sizing:border-box}
input:focus{outline:none;border-color:#53dd6e}
.btn-row{display:flex;gap:10px;margin-top:18px}
.btn{padding:12px 24px;border:none;border-radius:8px;font-size:.9rem;cursor:pointer;font-weight:600;flex:1}
.btn-start{background:#53dd6e;color:#000}.btn-start:hover{background:#3fcf5c}
.btn-stop{background:#ef4444;color:#fff}.btn-stop:hover{background:#dc2626}
.status{text-align:center;margin-top:14px;font-size:.85rem;color:#888}
.status .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot.off{background:#555}.dot.on{background:#53dd6e;box-shadow:0 0 8px #53dd6e}
</style></head><body>
<div class="header"><h1>Kick Streamer</h1><a href="/logout">Logout</a></div>
<div class="container">
<div class="card"><h2>Stream Config</h2>
<label>Kick Stream Key</label><input id="key" type="password" placeholder="your-kick-stream-key">
<label>Kick Username (for follower count)</label><input id="username" placeholder="your-kick-username">
<label>Video URL (mp4/m3u8)</label><input id="video" placeholder="https://example.com/video.mp4">
<label>Kick RTMP URL</label><input id="rtmp" placeholder="rtmp://push.kick.com/live">
<div class="btn-row">
<button class="btn btn-start" onclick="startStream()">Start Stream</button>
<button class="btn btn-stop" onclick="stopStream()">Stop Stream</button>
</div>
<div class="status" id="status"><span class="dot off"></span>Idle</div>
</div>
<div class="card"><h2>Info</h2>
<p style="color:#888;font-size:.85rem;line-height:1.6">
The streamer downloads your video once and loops it to Kick.com endlessly.<br>
FFmpeg auto-restarts on crash.<br>
Stop from here or cancel the GitHub Actions workflow.
</p></div>
</div>
<script>
let cfg={};
async function load(){
  const r=await fetch('/api/config');cfg=await r.json();
  document.getElementById('key').value=cfg.stream_key||'';
  document.getElementById('username').value=cfg.kick_username||'';
  document.getElementById('video').value=cfg.video_url||'';
  document.getElementById('rtmp').value=cfg.kick_rtmp||'rtmp://push.kick.com/live';
  update();
}
async function save(){
  cfg.stream_key=document.getElementById('key').value;
  cfg.kick_username=document.getElementById('username').value;
  cfg.video_url=document.getElementById('video').value;
  cfg.kick_rtmp=document.getElementById('rtmp').value;
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
}
async function startStream(){
  await save();
  const r=await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
  const d=await r.json();
  if(d.ok){alert('Stream started!')}else{alert('Error: '+d.error)}
  update();
}
async function stopStream(){
  await fetch('/api/stop',{method:'POST'});
  update();
}
async function update(){
  const s=await(await fetch('/api/status')).json();
  const el=document.getElementById('status');
  el.innerHTML=s.running?`<span class="dot on"></span>Streaming (PID ${s.pid})`:`<span class="dot off"></span>Idle`;
}
load();setInterval(update,5000);
</script></body></html>"""


@app.route("/")
@login_required
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@app.route("/api/config", methods=["GET"])
@login_required
def get_config():
    return jsonify(load_config())


@app.route("/api/config", methods=["POST"])
@login_required
def post_config():
    cfg = request.json
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/start", methods=["POST"])
@login_required
def start_stream():
    global PROCESS
    if PROCESS and PROCESS.poll() is None:
        return jsonify({"error": "Stream already running"}), 409

    data = request.json or {}
    stream_key = data.get("stream_key", "")
    video_url = data.get("video_url", "")
    kick_rtmp = data.get("kick_rtmp", KICK_RTMP)
    kick_username = data.get("kick_username", "")

    if not stream_key:
        return jsonify({"error": "No stream key"}), 400
    if not video_url:
        return jsonify({"error": "No video URL"}), 400

    save_config({"stream_key": stream_key, "video_url": video_url, "kick_rtmp": kick_rtmp, "kick_username": kick_username})

    STOP_FILE.unlink(missing_ok=True)
    env = os.environ.copy()
    env["KICK_RTMP"] = kick_rtmp
    script = os.path.join(os.path.dirname(__file__), "restream.sh")
    cmd = ["bash", script, stream_key, video_url]
    if kick_username:
        cmd.append(kick_username)
    PROCESS = subprocess.Popen(
        cmd,
        preexec_fn=os.setsid,
        env=env,
    )
    return jsonify({"ok": True, "pid": PROCESS.pid})


@app.route("/api/stop", methods=["POST"])
@login_required
def stop_stream():
    global PROCESS
    STOP_FILE.touch()
    if PROCESS and PROCESS.poll() is None:
        os.killpg(os.getpgid(PROCESS.pid), signal.SIGTERM)
        try:
            PROCESS.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(PROCESS.pid), signal.SIGKILL)
            PROCESS.wait(timeout=5)
        PROCESS = None
        return jsonify({"ok": True})
    PROCESS = None
    return jsonify({"ok": True, "message": "No process running"})


@app.route("/api/status")
@login_required
def status():
    running = PROCESS is not None and PROCESS.poll() is None
    return jsonify({"running": running, "pid": PROCESS.pid if running else None})


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"Kick Streamer Panel running on http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
