#!/usr/bin/env python3
# -*- coding: utf-8 -*-

########################## SQUIRT ########################
## Spectra‑Qualified Uncomplicated Inky Rendering Tools ##
###################### Web Dashboard #####################

from __future__ import annotations

import io
import os
import re
import sys
import json
import time
import uuid
import signal
import shutil
import secrets
import threading
import subprocess
import socket
import logging
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# ── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("squirt")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ── Minimal auto‑pip (fallback only) ─────────────────────────────────────
def _pip_install(*pkgs: str) -> None:
    if not pkgs:
        return
    exe = shutil.which(sys.executable) or sys.executable
    try:
        subprocess.run(
            [exe, "-m", "pip", "install", "--quiet", "--user", "--break-system-packages", *pkgs],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass

for mod, pkg in (("flask", "flask"), ("PIL", "pillow")):
    try:
        __import__(mod)
    except ModuleNotFoundError:
        print(f"Installing {pkg} …", file=sys.stderr)
        _pip_install(pkg)

from flask import Flask, flash, redirect, render_template_string, request, url_for
from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

# ── Feature flags / PiSugar ─────────────────────────────────────────────
def _truthy(val: Optional[str]) -> bool:
    return (val or "").strip().lower() in {"1", "true", "yes", "on"}

PISUGAR = ("--pisugar" in sys.argv) or _truthy(os.environ.get("PISUGAR"))
HOST_SHORT = (socket.gethostname() or "raspberrypi").split(".")[0]
PISUGAR_BASE_LOCAL = f"http://{HOST_SHORT}.local:8421"
PISUGAR_BASE_LOOP = "http://127.0.0.1:8421"

# ── Inky detection ───────────────────────────────────────────────────────
INKY_TYPE = os.environ.get("INKY_TYPE", "el133uf1")
INKY_COLOUR: Optional[str] = os.environ.get("INKY_COLOUR") or None
HEADLESS_RES: Tuple[int, int] = (
    int(os.environ.get("HEADLESS_WIDTH", "1600")),
    int(os.environ.get("HEADLESS_HEIGHT", "1200")),
)

def init_inky() -> Tuple[object | None, int, int]:
    try:
        import inky  # noqa: F401
        import numpy  # noqa: F401
    except ModuleNotFoundError:
        _pip_install("inky>=2.1.0", "numpy")
    try:
        from inky.auto import auto
        dev = auto()
        w, h = getattr(dev, "resolution", (None, None))
        if not (isinstance(w, int) and isinstance(h, int)):
            w, h = HEADLESS_RES
        return dev, int(w), int(h)
    except Exception:
        pass
    class_map = {
        "el133uf1": "InkyEL133UF1", "spectra13": "InkyEL133UF1", "impression13": "InkyEL133UF1",
        "phat": "InkyPHAT", "what": "InkyWHAT",
    }
    key = (INKY_TYPE or "").lower()
    if key not in class_map:
        return None, *HEADLESS_RES
    try:
        module = __import__("inky", fromlist=[class_map[key]])
        cls = getattr(module, class_map[key])
        dev = cls(INKY_COLOUR) if key in {"phat", "what"} else cls()
        w, h = getattr(dev, "resolution", HEADLESS_RES)
        return dev, int(w), int(h)
    except Exception:
        return None, *HEADLESS_RES

INKY, WIDTH, HEIGHT = init_inky()

# ── Paths / FS ───────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
ROOT = (BASE_DIR / "static").resolve()
UPLOAD_DIR = (ROOT / "uploads").resolve()
PATTERN_DIR = (ROOT / "patterns").resolve()

for d in (ROOT, UPLOAD_DIR, PATTERN_DIR):
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log.warning("Ensure dir %s failed: %s", d, e)

ALLOWED = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = 40_000_000

# ── External scripts / logs / timeouts ───────────────────────────────────
def _env_int(name: str, default: int, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    try:
        v = int(os.environ.get(name, str(default)))
        if lo is not None and v < lo: v = lo
        if hi is not None and v > hi: v = hi
        return v
    except Exception:
        return default

LOG_FILE = os.environ.get("UNISON_LOG", str(Path.home() / "unison_backup.log"))
WIFI_IF = os.environ.get("WIFI_IF", "wlan0")
SCRIPTS: Dict[str, List[str]] = {
    "xkcd":      [os.environ.get("XKCD_CMD", "python3"), os.environ.get("XKCD_SCRIPT", "xkcd.py")],
    "nasa":      [os.environ.get("NASA_CMD", "python3"), os.environ.get("NASA_SCRIPT", "nasa.py")],
    "landscape": [os.environ.get("LAND_CMD", "python3"), os.environ.get("LAND_SCRIPT", "landscape.py")],
    "sync":      [os.environ.get("SYNC_CMD", "bash"),   os.environ.get("SYNC_SCRIPT", "../sync.sh")],
}
SCRIPT_TIMEOUT = _env_int("SCRIPT_TIMEOUT", 120, 5, 600)

# ── Flask app ────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=str(ROOT), static_url_path="/static")
app.secret_key = os.environ.get("FLASK_SECRET") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024  # 64 MB

@app.after_request
def _secure_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    xf_proto = (request.headers.get("X-Forwarded-Proto") or "").lower()
    if xf_proto == "https" or _truthy(os.environ.get("FORCE_HSTS")):
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store"
    return resp

# ── Idle terminator ──────────────────────────────────────────────────────
IDLE_TIMEOUT = _env_int("IDLE_TIMEOUT", 120, 30, 3600)

class _IdleGuard:
    def __init__(self, t: int):
        self.timeout = int(t)
        self._last = time.monotonic()
        self._active = 0
        self._lk = threading.Lock()
        threading.Thread(target=self._watch, name="idle-terminator", daemon=True).start()
    def enter(self):
        with self._lk:
            self._active += 1
            self._last = time.monotonic()
    def exit(self, exc=None):
        with self._lk:
            self._active = max(0, self._active - 1)
            self._last = time.monotonic()
    def _watch(self):
        while True:
            time.sleep(1)
            with self._lk:
                if self._active == 0 and (time.monotonic() - self._last) >= self.timeout:
                    try:
                        os.kill(os.getpid(), signal.SIGTERM)
                    except Exception:
                        os._exit(0)

_idle = _IdleGuard(IDLE_TIMEOUT)
app.before_request(_idle.enter)
app.teardown_request(_idle.exit)

# ── Helpers: run, net, images ────────────────────────────────────────────
def _run(cmd: List[str]) -> str:
    if not cmd or not isinstance(cmd, list):
        return ""
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""

def _cmd_is_usable(cmd: List[str]) -> Tuple[bool, str]:
    if not cmd or not all(isinstance(x, str) and x for x in cmd):
        return False, "Invalid command vector."
    if shutil.which(cmd[0]) is None:
        return False, f"Program not found: {cmd[0]}"
    if len(cmd) > 1:
        p = Path(cmd[1])
        if p.suffix and not (p.is_file() or (BASE_DIR / p).is_file()):
            return False, f"Script not found: {p}"
    return True, ""

def _tcp_port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    import socket as _s
    try:
        with _s.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def _http_get(url: str, timeout: float = 2.8) -> tuple[int, bytes, str]:
    try:
        req = Request(url, headers={"User-Agent": "Squirt/1.1"})
        with urlopen(req, timeout=timeout) as r:
            return int(r.status or 0), r.read(262144), r.headers.get_content_type() or ""
    except Exception:
        return 0, b"", ""

def _http_head_ok(url: str, timeout: float = 1.6) -> bool:
    try:
        req = Request(url, headers={"User-Agent": "Squirt/1.1"}, method="HEAD")
        with urlopen(req, timeout=timeout) as r:
            return 200 <= int(r.status or 0) < 400
    except Exception:
        return False

def _safe_image_ext(fmt: Optional[str]) -> str:
    return {"JPEG": ".jpg", "JPG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp", "BMP": ".bmp"}.get((fmt or "").upper(), ".png")

def _apply_exif(im: Image.Image) -> Image.Image:
    try:
        return ImageOps.exif_transpose(im)
    except Exception:
        return im

def _clamp_mode(val: str) -> str:
    return "fill" if (val or "").lower() == "fill" else "fit"

def _clamp_matte(val: str) -> str:
    return "white" if (val or "").lower() == "white" else "black"

def scale_fit(im: Image.Image, bg: Tuple[int, int, int]) -> Image.Image:
    im = _apply_exif(im.convert("RGB"))
    s = min(WIDTH / im.width, HEIGHT / im.height)
    if s != 1:
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), bg)
    canvas.paste(im, ((WIDTH - im.width) // 2, (HEIGHT - im.height) // 2))
    return canvas

def scale_fill(im: Image.Image) -> Image.Image:
    im = _apply_exif(im.convert("RGB"))
    s = max(WIDTH / im.width, HEIGHT / im.height)
    if s != 1:
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    l = max(0, (im.width - WIDTH) // 2)
    t = max(0, (im.height - HEIGHT) // 2)
    return im.crop((l, t, l + WIDTH, t + HEIGHT))

# ── In‑memory preview buffer (ephemeral per process) ─────────────────────
class TempImageBuffer:
    def __init__(self, max_items: int = 12, max_bytes: int = 64 * 1024 * 1024):
        self._data: "OrderedDict[str, bytes]" = OrderedDict()
        self._lock = threading.Lock()
        self.max_items = int(max_items)
        self.max_bytes = int(max_bytes)
        self.bytes = 0
    def put(self, image: Image.Image, fmt: str = "PNG") -> str:
        bio = io.BytesIO()
        image.save(bio, format=fmt)
        data = bio.getvalue()
        key = uuid.uuid4().hex
        with self._lock:
            self._data[key] = data
            self.bytes += len(data)
            while len(self._data) > self.max_items or self.bytes > self.max_bytes:
                k, v = self._data.popitem(last=False)
                self.bytes -= len(v)
        return key
    def get(self, key: str) -> Optional[bytes]:
        with self._lock:
            data = self._data.get(key)
            if data is None:
                return None
            self._data.move_to_end(key)
            return data
    def latest(self) -> Optional[str]:
        with self._lock:
            if not self._data:
                return None
            return next(reversed(self._data))

BUF = TempImageBuffer()

def display_and_preview(src_path: Path, matte: str, mode: str) -> str:
    matte = _clamp_matte(matte)
    mode = _clamp_mode(mode)
    bg = (255, 255, 255) if matte == "white" else (0, 0, 0)
    with Image.open(src_path) as raw:
        frame = scale_fill(raw) if mode == "fill" else scale_fit(raw, bg)
    if INKY:
        try:
            INKY.set_image(frame)
            INKY.show()
        except Exception as e:
            raise RuntimeError(f"Inky display error: {e}") from e
    return BUF.put(frame, fmt="PNG")

# ── File helpers ─────────────────────────────────────────────────────────
def allowed_file(name: str) -> bool:
    return Path(name).suffix.lower() in ALLOWED

def save_upload(fs) -> Path:
    raw_name = fs.filename or ""
    if not raw_name:
        raise UnidentifiedImageError("Empty filename.")
    data = fs.read()
    if not data:
        raise UnidentifiedImageError("Empty upload.")
    tmp = UPLOAD_DIR / f".{uuid.uuid4().hex}.part"
    tmp.write_bytes(data)
    try:
        with Image.open(tmp) as im:
            im.verify()
        with Image.open(tmp) as im2:
            ext2 = _safe_image_ext(im2.format)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    final = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext2}"
    tmp.replace(final)
    return final

MAX_FETCH_BYTES = _env_int("MAX_FETCH_BYTES", 64 * 1024 * 1024, 16 * 1024, 1024 * 1024 * 1024)

def fetch_image_to_uploads(url: str) -> Path:
    u = urlparse(url)
    if u.scheme not in {"http", "https"} or not u.netloc:
        raise ValueError("Only http/https URLs are allowed.")
    status, _, _ = _http_get(url, timeout=5.0)
    if status < 200 or status >= 400:
        raise ValueError(f"URL not reachable (status {status}).")
    req = Request(url, headers={"User-Agent": "Squirt/1.1"})
    tmp = UPLOAD_DIR / f".fetch-{uuid.uuid4().hex}.part"
    total = 0
    try:
        with urlopen(req, timeout=20) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_FETCH_BYTES:
                    raise ValueError("Remote file too large.")
                f.write(chunk)
        with Image.open(tmp) as im:
            im.verify()
        with Image.open(tmp) as im2:
            ext = _safe_image_ext(im2.format)
        final = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
        tmp.replace(final)
        return final
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

# ── Background pattern selection ─────────────────────────────────────────
def select_bg_pattern() -> Tuple[Optional[str], int]:
    try:
        cands = [p for p in PATTERN_DIR.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED]
        if not cands:
            return None, 0
        pick = secrets.choice(cands)
        try:
            with Image.open(pick) as im:
                blur = max(1, min(12, round(min(im.width, im.height) * 0.10)))
        except Exception:
            blur = 4
        rel = pick.resolve().relative_to(ROOT.resolve()).as_posix()
        return rel, blur
    except Exception:
        return None, 0

# ── System / Unison / PiSugar ────────────────────────────────────────────
def get_uptime() -> str:
    out = _run(["uptime", "-p"])
    if out:
        return out
    try:
        secs = float(Path("/proc/uptime").read_text().split()[0])
        mins = int(secs // 60)
        hrs = mins // 60
        days = hrs // 24
        if days:
            return f"up {days} days, {hrs%24} hours"
        if hrs:
            return f"up {hrs} hours, {mins%60} minutes"
        return f"up {mins} minutes"
    except Exception:
        return "N/A"

def get_disk() -> str:
    out = _run(["df", "-h", str(Path.home())])
    try:
        return out.splitlines()[1].split()[3] + " free"
    except Exception:
        return "N/A"

def get_mem() -> str:
    out = _run(["free", "-h"])
    try:
        for line in out.splitlines():
            if line.startswith("Mem:"):
                parts = line.split()
                avail = parts[6] if len(parts) >= 7 else parts[3]
                total = parts[1]
                return f"{avail} available of {total}"
        return "N/A"
    except Exception:
        return "N/A"

def get_cpu_load_pct() -> str:
    try:
        load1 = os.getloadavg()[0]
        cores = os.cpu_count() or 1
        return f"{(load1 / cores) * 100:.1f}%"
    except Exception:
        return "N/A"

def get_cpu_temp_c() -> str:
    p = Path("/sys/class/thermal/thermal_zone0/temp")
    if p.exists():
        try:
            return f"{float(p.read_text()) / 1000:.1f}°C"
        except Exception:
            pass
    return "N/A"

def get_wifi_rssi() -> str:
    out = _run(["iw", "dev", WIFI_IF, "link"])
    m = re.search(r"signal:\s*(-?\d+)\s*dBm", out)
    return f"{m.group(1)} dBm" if m else "N/A"

def _read_last_lines(path: Path, n: int) -> List[str]:
    try:
        with path.open("rb") as f:
            dq: deque[bytes] = deque(maxlen=n)
            for line in f:
                dq.append(line.rstrip(b"\r\n"))
        return [b.decode("utf-8", "replace") for b in dq]
    except Exception:
        try:
            out = subprocess.check_output(["tail", "-n", str(n), str(path)], text=True, stderr=subprocess.DEVNULL)
            return out.splitlines()[-n:]
        except Exception:
            return ["<unable to read log>"]

@dataclass
class BackupStatus:
    last_ok: str = "N/A"
    last_any: str = "N/A"
    status: str = "N/A"
    reason: str = ""
    rssi: str = "N/A"
    loss: str = "N/A"
    lat: str = "N/A"

def parse_unison_log(path: Path = Path(LOG_FILE)) -> BackupStatus:
    if not path.exists():
        return BackupStatus()
    lines = _read_last_lines(path, 2000)
    last_ok = next((l for l in reversed(lines) if ("status=OK" in l or '"status":"OK"' in l)), None)
    last_any = next((l for l in reversed(lines) if ("Result:" in l or '"status":' in l)), None)
    st = BackupStatus(last_ok=(last_ok.split()[0] if last_ok else "N/A"),
                      last_any=(last_any.split()[0] if last_any else "N/A"))
    if not last_any:
        return st
    line = last_any
    if "Result:" in line:
        def g(tag: str) -> str:
            m = re.search(rf"{tag}=([^ ]+)", line)
            return m.group(1) if m else ""
        st.status = g("status") or "N/A"
        st.reason = g("reason")
        st.rssi = g("RSSI") or "N/A"
        st.loss = g("loss") or "N/A"
        st.lat = g("latency") or "N/A"
    else:
        def gx(rx: str) -> str:
            m = re.search(rx, line)
            return m.group(1) if m else ""
        st.status = gx(r'"status":"([^"]+)"') or "N/A"
        st.reason = gx(r'"reason":"([^"]+)"')
        st.rssi = gx(r'"rssi":(-?\d+)') or "N/A"
        st.loss = gx(r'"loss":(\d+)') or "N/A"
        st.lat = gx(r'"lat":(\d+)') or "N/A"
    return st

# ── PiSugar battery: socket + HTTP, robust parsing ───────────────────────
def _read_all(sock: socket.socket, timeout: float = 0.7, max_bytes: int = 65536) -> bytes:
    sock.settimeout(timeout)
    chunks: List[bytes] = []
    total = 0
    try:
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            total += len(data)
            if total >= max_bytes:
                break
    except Exception:
        pass
    return b"".join(chunks)

def _pisugar_via_socket(host: str = "127.0.0.1", port: int = 8423) -> Optional[bytes]:
    try:
        with socket.create_connection((host, port), timeout=0.6) as s:
            try:
                s.sendall(b"get battery\n")
            except Exception:
                return None
            return _read_all(s, timeout=0.6)
    except Exception:
        return None

def _normalize_bool(val) -> Optional[bool]:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        t = val.strip().lower()
        if t in {"1","true","yes","y","charging","on"}:
            return True
        if t in {"0","false","no","n","idle","off"}:
            return False
    return None

def _parse_pairs(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or len(line) > 300:
            continue
        if ":" in line:
            k, v = line.split(":", 1)
        elif "=" in line:
            k, v = line.split("=", 1)
        else:
            continue
        out[k.strip().lower()] = v.strip()
    return out

def _parse_pisugar_payload(data: bytes | str) -> Dict[str, str]:
    info: Dict[str, str] = {}
    text = data.decode("utf-8", "ignore") if isinstance(data, (bytes, bytearray)) else str(data)

    # Try JSON first
    try:
        obj = json.loads(text)
    except Exception:
        obj = None

    if isinstance(obj, dict):
        stack = [obj]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                for k, v in cur.items():
                    lk = str(k).lower().strip()
                    if any(x in lk for x in ("percent","percentage","level","soc","battery","power")):
                        try:
                            info["level"] = f"{int(round(float(v)))}%"
                        except Exception:
                            pass
                    if "volt" in lk or lk in {"vbat","battery_voltage"}:
                        try:
                            info["voltage"] = f"{float(v):.2f}V"
                        except Exception:
                            try:
                                m = re.search(r'([0-9]+(?:\.[0-9]+)?)', str(v))
                                if m: info["voltage"] = f"{float(m.group(1)):.2f}V"
                            except Exception:
                                pass
                    if "charg" in lk or lk in {"is_charging","charging","charge_status"}:
                        b = _normalize_bool(v)
                        if b is True: info["charging"] = "charging"
                        elif b is False: info["charging"] = "idle"
                        else:
                            s = str(v).strip()
                            if s: info["charging"] = s
                for v in cur.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(cur, list):
                for v in cur:
                    if isinstance(v, (dict, list)):
                        stack.append(v)

    # Fallback: key:value lines
    if not info:
        kv = _parse_pairs(text)
        if kv:
            for k, v in kv.items():
                lk = k.lower()
                if any(x in lk for x in ("percent","percentage","level","soc","battery","power")) and "level" not in info:
                    try:
                        info["level"] = f"{int(round(float(re.sub(r'[^0-9.]+','', v) or 0)))}%"
                    except Exception:
                        pass
                if ("volt" in lk or lk in {"vbat","battery_voltage"}) and "voltage" not in info:
                    m = re.search(r'([0-9]+(?:\.[0-9]+)?)', v)
                    if m:
                        try:
                            info["voltage"] = f"{float(m.group(1)):.2f}V"
                        except Exception:
                            pass
                if "charg" in lk or lk in {"is_charging","charging","charge_status"}:
                    b = _normalize_bool(v)
                    if b is True: info["charging"] = "charging"
                    elif b is False: info["charging"] = "idle"
                    elif v.strip(): info["charging"] = v.strip()

    # Last-resort: regex scan
    if "level" not in info:
        m = re.search(r'(\d{1,3})\s*%', text)
        if m:
            info["level"] = f"{int(m.group(1))}%"
    if "voltage" not in info:
        m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*[Vv]\b', text)
        if m:
            try:
                info["voltage"] = f"{float(m.group(1)):.2f}V"
            except Exception:
                pass
    if "charging" not in info:
        if re.search(r'\bcharging\b', text, re.I):
            info["charging"] = "charging"
        elif re.search(r'\bidle\b|\bdischarg', text, re.I):
            info["charging"] = "idle"

    # Normalize clamped values
    if "level" in info:
        try:
            n = int(re.sub(r'[^0-9]', '', info["level"]) or 0)
            info["level"] = f"{max(0, min(100, n))}%"
        except Exception:
            pass
    if "voltage" in info:
        m = re.search(r'([0-9]+(?:\.[0-9]+)?)', info["voltage"])
        if m:
            try:
                info["voltage"] = f"{float(m.group(1)):.2f}V"
            except Exception:
                pass

    return info

def probe_pisugar_status() -> dict:
    """
    Tries local text socket (8423 → "get battery") first, then HTTP on 8421.
    Returns {reachable, level, voltage, charging}.
    """
    info = {"reachable": False, "level": "N/A", "voltage": "N/A", "charging": "N/A"}
    if not PISUGAR:
        return info

    # 1) Local control socket (fast path)
    data = _pisugar_via_socket("127.0.0.1", 8423)
    if data:
        parsed = _parse_pisugar_payload(data)
        if parsed:
            info.update(parsed)
            info["reachable"] = True
            return info

    # 2) HTTP fallback on 8421
    bases = (PISUGAR_BASE_LOOP, PISUGAR_BASE_LOCAL)
    paths = ("/api/v1/getAll", "/api/getAll", "/api/getBattery", "/api/status",
             "/api/v1/status", "/status", "/battery", "/api/battery")

    tcp_ok = _tcp_port_open("127.0.0.1", 8421) or _tcp_port_open(f"{HOST_SHORT}.local", 8421)
    for base in bases:
        for path in paths:
            status, body, _ = _http_get(base + path, timeout=2.5)
            if not status or status >= 500 or not body:
                continue
            parsed = _parse_pisugar_payload(body)
            if parsed:
                info.update(parsed)
                info["reachable"] = True
                return info

    info["reachable"] = bool(tcp_ok)
    return info

# ── Browser helpers ──────────────────────────────────────────────────────
def _safe_in_static(p: Path) -> bool:
    root = ROOT.resolve()
    try:
        return p.resolve().is_relative_to(root)  # py3.9+
    except AttributeError:
        return str(p.resolve()).startswith(str(root))

def _subpath_to_dir(subpath: str) -> Path:
    p = (ROOT / (subpath or "")).resolve()
    if not _safe_in_static(p):
        raise FileNotFoundError("Out of bounds")
    p.mkdir(parents=True, exist_ok=True)
    return p

def _fmt_bytes(n: int) -> str:
    try:
        units = ["B", "KB", "MB", "GB", "TB", "PB"]
        f = float(n)
        for u in units:
            if f < 1024.0 or u == units[-1]:
                return f"{f:.0f} {u}" if u == "B" else f"{f:.1f} {u}"
            f /= 1024.0
    except Exception:
        pass
    return "N/A"

def _fmt_time(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except Exception:
        return "N/A"

def _list_dir(dirpath: Path):
    try:
        entries = list(dirpath.iterdir())
    except Exception:
        entries = []
    dirs = [{"name": d.name, "link": (dirpath / d.name).relative_to(ROOT).as_posix()}
            for d in sorted([x for x in entries if x.is_dir()], key=lambda x: x.name.lower())]
    imgs = []
    for f in sorted([x for x in entries if x.is_file() and x.suffix.lower() in ALLOWED], key=lambda x: x.name.lower()):
        try:
            st = f.stat()
            size = _fmt_bytes(st.st_size)
            mtime = _fmt_time(st.st_mtime)
        except Exception:
            size = "N/A"; mtime = "N/A"
        imgs.append({"name": f.name, "rel": f.relative_to(ROOT).as_posix(), "size": size, "mtime": mtime})
    return dirs, imgs

# ── Build safe command vectors from forms ────────────────────────────────
def _build_cmd_with_opts(name: str, form) -> List[str]:
    base = SCRIPTS.get(name)
    if not base:
        return []
    cmd = list(base)
    if name == "xkcd":
        matte = _clamp_matte(form.get("xk_matte") or "white")
        orient = (form.get("xk_orient") or "auto").lower()
        cmd.append("--black" if matte == "black" else "--white")
        if orient == "landscape": cmd.append("--landscape")
        elif orient == "portrait": cmd.append("--portrait")
    elif name == "landscape":
        src = (form.get("land_src") or "all").lower()
        orient = (form.get("land_orient") or "any").lower()
        mode = _clamp_mode(form.get("land_mode") or "fit")
        matte = _clamp_matte(form.get("land_matte") or "black")
        if src in {"met", "aic", "cma"}: cmd.append(f"--{src}")
        if orient == "wide": cmd.append("--wide")
        elif orient == "tall": cmd.append("--tall")
        cmd.extend(["--mode", mode])
        if matte == "white": cmd.append("--white")
    elif name == "nasa":
        """
        Build APOD commands compliant with the NASA script.  The underlying script only supports the
        `--apod` flag, an optional `--batch <n>` for multiple random images, and orientation hints
        `--portrait` or `--landscape`.  Unsupported options like date selection, HD, mode and matte
        are deliberately ignored here to avoid passing invalid flags.  See nasa.py for details.
        """
        # Always fetch the Astronomy Picture of the Day.
        cmd.append("--apod")
        # Orientation: map the user selection to orientation flags.  Fall back to no flag (auto).
        orient = (form.get("nasa_orient") or "auto").lower()
        if orient == "portrait":
            cmd.append("--portrait")
        elif orient == "landscape":
            cmd.append("--landscape")
        # Random: if the user requests random images, request a batch of 5.
        if (form.get("nasa_random") or "no").lower() == "yes":
            cmd.extend(["--batch", "5"])
    return cmd

# ── Templates ────────────────────────────────────────────────────────────
BASE = """
<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SQUIRT DASHBOARD</title>
<style>
:root{
  --accent:#C85500; --accent-2:#A44900;
  --panel:#1b120b; --card:#1f140c; --text:#f4efe9; --muted:#dacfc5; --border:#3c2614;
  --ok:#22c55e; --warn:#f59e0b; --bad:#ef4444; --info:#C85500;
  --bg-image:none; --bg-blur:0px;
  --wrap: min(96vw, 1600px);
}
*{box-sizing:border-box}
html,body{height:100%}
body{
  margin:0;
  background:linear-gradient(180deg,#20140b 0%, #120c07 100%);
  color:var(--text);
  font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  position:relative;
  /* Prevent horizontal scrollbars when zooming */
  overflow-x: hidden;
}
body::before{content:"";position:fixed;inset:0;pointer-events:none;background-image:var(--bg-image);background-repeat:repeat;background-position:top left;background-size:auto;filter:blur(var(--bg-blur));opacity:.9;z-index:-1}
body > * { position: relative; z-index: 1; }
header{position:sticky;top:0;z-index:10;background:rgba(36,22,12,.85);border-bottom:1px solid var(--border);backdrop-filter:saturate(1.1) blur(6px)}
.header-inner{max-width:var(--wrap);margin:0 auto;padding:.6rem .8rem;display:flex;align-items:center;justify-content:space-between;gap:.6rem;flex-wrap:wrap}
.brand{display:flex;align-items:flex-end; flex:1 1 auto;}
/* Bigger, colored SQUIRT title without changing bar height */
.brand .logo{
  font-weight:900; letter-spacing:.06em; font-size:1.15rem; display:inline-flex; gap:.08rem;
  transform: scale(1.32); transform-origin: left center; line-height:1;
}
.brand .s{color:#e5e7eb}.brand .q{color:#ef4444}.brand .u{color:#f59e0b}.brand .i{color:#f97316}.brand .r{color:#22c55e}.brand .t{color:#3b82f6}
.meta{color:var(--muted);font-size:.9rem;display:flex;gap:.6rem;align-items:center;flex-wrap:wrap; flex:0 0 auto;}
/* Tabs */
.tabs{background:linear-gradient(90deg,#26180d 0%, #22160d 100%);border-bottom:1px solid var(--border);overflow-x:auto}
.tabs-inner{max-width:var(--wrap);margin:0 auto;padding:0 .6rem;display:flex;gap:.4rem;white-space:nowrap}
.tab{display:inline-block;padding:.7rem 1rem;border:1px solid transparent;border-bottom:none;border-radius:.5rem .5rem 0 0;color:#f1e6db;text-decoration:none;text-transform:uppercase;letter-spacing:.03em}
.tab.active{background:var(--card);border-color:var(--accent);color:#fff}
/* Layout */
.container{max-width:var(--wrap);margin:0 auto;padding:1rem}
h2,h3{margin:.25rem 0 .6rem 0;text-transform:uppercase;letter-spacing:.03em}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1rem;margin-bottom:1rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(380px,100%),1fr));gap:1rem}
.grid-fluid{--cols:1; grid-template-columns:repeat(var(--cols), minmax(min(320px,100%),1fr))}
@media(min-width:1200px){ .grid{ grid-template-columns: 1fr 1fr; } }

.row{display:flex;flex-wrap:wrap;gap:.6rem 1rem;align-items:center}
.actions-row{display:flex;flex-wrap:wrap;gap:.6rem 1rem;align-items:center}
.btn{padding:.45rem .8rem;border:1px solid var(--border);border-radius:.5rem;background:#2a1a0e;color:#fdfbf8;cursor:pointer;white-space:nowrap}
.btn:hover{background:var(--accent-2);border-color:var(--accent)}
.badge{display:inline-block;padding:.15rem .45rem;border-radius:.4rem;font-size:.85rem;border:1px solid #5a3a20;background:rgba(200,85,0,.12);color:#ffd7bf}
.badge.ok{background:rgba(34,197,94,.15);border-color:rgba(34,197,94,.45);color:#d9ffe6}
.badge.warn{background:rgba(245,158,11,.15);border-color:rgba(245,158,11,.45);color:#fff0ce}
.badge.bad{background:rgba(239,68,68,.15);border-color:rgba(239,68,68,.45);color:#ffe1e1}
.badge.info{background:rgba(200,85,0,.18);border-color:rgba(200,85,0,.45);color:#ffd7bf}
.kv{width:100%;border-collapse:collapse}
.kv td{padding:.25rem .35rem;border-bottom:1px dashed #3c2614;vertical-align:top}
.k{color:var(--muted);width:44%}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:.75rem}
.thumb{width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:8px;border:1px solid var(--border)}
.notice{color:var(--muted)}
ul.flash{list-style:none;padding-left:1rem;margin:.2rem 0}
ul.flash li{background:#1f140c;border:1px solid var(--border);padding:.5rem .7rem;border-radius:.5rem;margin:.3rem 0}
label{display:flex;flex-direction:column;gap:.25rem}
select,input[type=file],input[type=url],input[type=date]{padding:.35rem .45rem;border:1px solid var(--border);border-radius:.4rem;background:#160e08;color:#f4efe9;min-width:12ch}
.form-row{display:flex;flex-wrap:wrap;gap:.8rem 1.2rem;align-items:center}
.form-row.align-right{justify-content:flex-end}
.logbox{height:260px;overflow:auto;background:#160e08;border:1px solid var(--border);border-radius:8px;padding:.6rem;
  font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono","Courier New",monospace;font-size:.85rem;line-height:1.3;white-space:pre-wrap}

/* ---- Quick‑Actions master grid ---- */
.qa-grid{ /* Master grid for quick actions.  Allow cards to shrink gracefully on narrow or zoomed screens */
  display:grid;
  gap:1rem;
  /* Each card can shrink down to 480px or 100% of its available width, whichever is smaller.  This prevents horizontal
     overflow at high zoom levels while still allowing multiple columns on wide displays. */
  grid-template-columns:repeat(auto-fit, minmax(min(480px, 100%), 1fr));
}

/* ---- Inline QA forms: stack controls; optional 2×2 on wide screens ---- */
.form-stack{display:flex;flex-direction:column;gap:.6rem;min-width:0}
.inline-grid-qa{
  width:100%;
  display:grid;
  gap:.6rem;
  align-items:end;
  grid-template-columns: 1fr;             /* stack one per row by default */
}
/* When a quick-action form has four fields, arrange controls in two columns on large screens.
   Use a flexible minmax() to allow the controls to shrink when zooming in.  The min() wrapper ensures
   each column never exceeds the available width.  On small screens, collapse back to a single column. */
.inline-grid-qa.cols-2{
  grid-template-columns: repeat(2, minmax(min(16rem, 100%), 1fr));
}
@media(max-width:900px){
  .inline-grid-qa.cols-2{ grid-template-columns: 1fr; }
}
.inline-grid-qa > button.btn{
  grid-column: 1 / -1;    /* full row */
  justify-self: end;      /* right edge of the form */
}

/* Corner menu (no marker/boxing) */
details.menu{ position:relative; }
details.menu > summary.menu-btn{
  list-style:none; cursor:pointer; user-select:none; display:inline-flex; align-items:center; justify-content:center;
  background:transparent; border:0; padding:0; margin:0; font-size:1.15rem; line-height:1;
}
details.menu > summary.menu-btn::-webkit-details-marker{ display:none; }
details.menu > summary.menu-btn::marker{ content:""; }
details.menu .panel{
  position:absolute; right:0; top:calc(100% + .4rem);
  min-width:220px; padding:.35rem; border:1px solid var(--border); border-radius:12px;
  background:var(--card); box-shadow:0 10px 24px rgba(0,0,0,.45);
}
details.menu .panel .item{
  display:flex; justify-content:space-between; gap:.6rem; align-items:center;
  padding:.45rem .55rem; border-radius:.4rem; text-decoration:none; color:var(--text);
}
details.menu .panel .item:hover{ background:#2a1a0e; }
details.menu .panel .sep{ height:1px; background:#3c2614; margin:.25rem .2rem; }
details.menu .panel form{ margin:0; }
details.menu .panel button.item{
  width:100%; text-align:left; background:none; border:0; font:inherit; color:inherit;
  padding:.45rem .55rem; border-radius:.4rem; cursor:pointer;
}

/* --- Global overflow safety & wrapping --- */
.grid > *, .container, .card, .row, .form-row, .actions-row { min-width: 0; }
.kv{ table-layout: fixed; width: 100%; }
.kv td{ word-break: break-word; overflow-wrap: anywhere; }
.inline-grid-qa label{ min-width: 0; }
.inline-grid-qa select, .inline-grid-qa input{
  /* Allow controls to shrink and wrap properly on narrow or zoomed screens */
  min-width: 0;
  max-width: 100%;
  width: 100%;
}
input[type=url]{ width: min(26ch, 100%); }
img{ max-width: 100%; height: auto; }

/* Offline status indicator for the top bar */
.status-offline{
  color:#ef4444;
  font-weight:600;
}

/* Layout row for dashboard main controls: quick actions and preview/upload.
   Stacks into one column on narrow screens and splits into two columns on larger screens. */
.dashboard-row{
  display:grid;
  gap:1rem;
  grid-template-columns: 1fr;
  /* Span the dashboard row across all columns in the parent grid so that its two columns do not
     get separated when the outer grid has multiple columns. */
  grid-column:1 / -1;
}
@media(min-width:900px){
  .dashboard-row{
    grid-template-columns: 1fr 1fr;
  }
}

/* Horizontal scrolling gallery for recent uploads. Use a flex row with scroll snapping
   so thumbnails flow horizontally instead of wrapping to new rows. This avoids the grid
   layout used by file browser galleries. */
.recent-gallery{
  display:flex;
  gap:.75rem;
  overflow-x:auto;
  padding-bottom:.25rem;
  scroll-snap-type:x mandatory;
}
.recent-gallery > *{
  flex:0 0 auto;
  scroll-snap-align:start;
  /* Maintain the same thumbnail width as in the default gallery. */
  width:180px;
}

/* Make a card span the full width of the grid. Useful for the system health card at the top of the dashboard. */
.card.full-width{
  grid-column:1 / -1;
}
</style>
{% if bg_url %}
<style>:root{ --bg-image: url("{{ bg_url }}"); --bg-blur: {{ bg_blur }}px; } body{ background:none !important; } body::before{ z-index:0 !important; opacity:1 !important; }</style>
{% endif %}
<header>
  <div class="header-inner">
    <div class="brand">
      <div class="logo" title="Spectra‑Qualified Uncomplicated Inky Rendering Tools">
        <span class="s">S</span><span class="q">Q</span><span class="u">U</span><span class="i">I</span><span class="r">R</span><span class="t">T</span>
      </div>
    </div>
    <div class="meta">
      {# Show a compact status indicator. If the Inky display is connected, just show its resolution. Otherwise show a red "DISCONNECTED" in place of the resolution. #}
      {% if inky %}
        <span>{{ width }}×{{ height }}</span>
      {% else %}
        <span class="status-offline">DISCONNECTED</span>
      {% endif %}
      <details class="menu" id="hdrMenu">
        <summary class="menu-btn" aria-haspopup="true" aria-controls="hdrMenuPanel" title="Settings">⚙️</summary>
        <div id="hdrMenuPanel" class="panel" role="menu" aria-label="Quick actions">
          {% if pisugar and pisugar_web %}
            <a class="item" href="{{ pisugar_web }}" target="_blank" rel="noreferrer noopener" role="menuitem">🔋 PiSugar Web ↗</a>
          {% endif %}
          {% if pisugar and battery_json %}
            <a class="item" href="{{ battery_json }}" target="_blank" rel="noreferrer noopener" role="menuitem">📄 Battery JSON ↗</a>
          {% endif %}
          <div class="sep"></div>
          <form method="post" action="{{ url_for('power') }}">
            <input type="hidden" name="action" value="reboot">
            <button class="item" type="submit" role="menuitem">🔄 Reboot</button>
          </form>
          <form method="post" action="{{ url_for('power') }}">
            <input type="hidden" name="action" value="shutdown">
            <button class="item" type="submit" role="menuitem">🛑 Shutdown</button>
          </form>
          {% if pisugar %}
          <form method="post" action="{{ url_for('power') }}">
            <input type="hidden" name="action" value="sleep">
            <button class="item" type="submit" role="menuitem">😴 Sleep (PiSugar)</button>
          </form>
          {% endif %}
        </div>
      </details>
    </div>
  </div>
  <nav class="tabs">
    <div class="tabs-inner">
      <a class="tab {{ 'active' if request.path == '/' else '' }}" href="{{ url_for('index') }}">DASHBOARD</a>
      <a class="tab {{ 'active' if request.path.startswith('/browser') else '' }}" href="{{ url_for('browser', subpath='') }}">BROWSER</a>
      <a class="tab {{ 'active' if request.path.startswith('/sync') else '' }}" href="{{ url_for('sync_page') }}">SYNC</a>
    </div>
  </nav>
</header>
<div class="container" aria-live="polite">
  {% with messages = get_flashed_messages() %}
    {% if messages %}<ul class="flash">{% for m in messages %}<li>{{ m }}</li>{% endfor %}</ul>{% endif %}
  {% endwith %}
  {{ body|safe }}
</div>
"""

INDEX = """<div class="grid">
  <div class="card full-width">
    <h2>🛠️ SYSTEM HEALTH</h2>
    <table class="kv" aria-label="system health">
      <tr><td class="k">⏱️ Uptime</td><td>{{ uptime }}</td></tr>
      <tr><td class="k">💾 Disk</td><td>{{ disk }}</td></tr>
      <tr><td class="k">🧠 Memory</td><td>{{ mem }}</td></tr>
      <tr><td class="k">📈 CPU Load</td><td><span class="badge {{ 'ok' if cpu_load_pct_num < 60 else ('warn' if cpu_load_pct_num < 90 else 'bad') }}">{{ cpu_load }}</span></td></tr>
      <tr><td class="k">🌡️ CPU Temp</td><td><span class="badge {{ 'ok' if cpu_temp_num <= 70 else ('warn' if cpu_temp_num <= 80 else 'bad') }}">{{ cpu_temp }}</span></td></tr>
      <tr><td class="k">📶 Wi‑Fi RSSI ({{ wifi_if }})</td><td>{{ wifi }}</td></tr>
      {% if pisugar %}
      <tr><td class="k">🔋 Battery (PiSugar)</td>
        <td>
          {% if bat.reachable %}
            {% set ns = namespace(shown=0) %}
            {% if bat.level and bat.level != 'N/A' %}<span class="badge info">🔋 {{ bat.level }}</span>{% set ns.shown = ns.shown + 1 %}{% endif %}
            {% if bat.voltage and bat.voltage != 'N/A' %}<span class="badge">⚡ {{ bat.voltage }}</span>{% set ns.shown = ns.shown + 1 %}{% endif %}
            {% if bat.charging and bat.charging != 'N/A' %}<span class="badge">🔌 {{ bat.charging }}</span>{% set ns.shown = ns.shown + 1 %}{% endif %}
            {% if ns.shown == 0 %}<span class="badge warn">N/A</span>{% endif %}
          {% else %}
            <span class="badge warn">offline</span>
          {% endif %}
        </td>
      </tr>
      {% endif %}
    </table>
  </div>

  <!-- Start dashboard row: quick actions and preview/upload -->
  <div class="dashboard-row">
    <!-- Quick actions live in their own card on the left (on wide screens) -->
    <div class="card">
      <h2>⚡ QUICK ACTIONS</h2>

      <div class="qa-grid">
      <div class="form-stack">
        <h3>🚀 NASA (APOD)</h3>
        <!-- Simplified APOD form: only orientation and random are relevant to nasa.py -->
        <form class="inline-grid-qa" method="post" action="{{ url_for('run_script', name='nasa') }}">
          <input type="hidden" name="with_opts" value="1">
          <label>Random
            <select name="nasa_random">
              <option value="no" selected>no</option>
              <option value="yes">yes</option>
            </select>
          </label>
          <label>Orientation
            <select name="nasa_orient">
              <option value="auto" selected>auto</option>
              <option value="landscape">landscape</option>
              <option value="portrait">portrait</option>
            </select>
          </label>
          <button class="btn" type="submit">RUN NASA</button>
        </form>
      </div>

      <div class="form-stack">
        <h3>🤖 XKCD</h3>
        <form class="inline-grid-qa" method="post" action="{{ url_for('run_script', name='xkcd') }}">
          <input type="hidden" name="with_opts" value="1">
          <label>Matte <select name="xk_matte"><option value="white">white (default)</option><option value="black" selected>black</option></select></label>
          <label>Orientation <select name="xk_orient"><option value="auto" selected>auto</option><option value="landscape">landscape</option><option value="portrait">portrait</option></select></label>
          <button class="btn" type="submit">RUN XKCD</button>
        </form>
      </div>

      <div class="form-stack">
        <h3>🖼️ LANDSCAPE</h3>
        <form class="inline-grid-qa cols-2" method="post" action="{{ url_for('run_script', name='landscape') }}">
          <input type="hidden" name="with_opts" value="1">
          <label>Source <select name="land_src"><option value="all" selected>All</option><option value="met">MET</option><option value="aic">AIC</option><option value="cma">CMA</option></select></label>
          <label>Orientation <select name="land_orient"><option value="any" selected>any</option><option value="wide">wide</option><option value="tall">tall</option></select></label>
          <label>Mode <select name="land_mode"><option value="fit" selected>fit</option><option value="fill">fill</option></select></label>
          <label>Matte <select name="land_matte"><option value="black" selected>black</option><option value="white">white</option></select></label>
          <button class="btn" type="submit">RUN LANDSCAPE</button>
        </form>
      </div>
      </div>
    </div>
    <!-- Preview & upload live in their own card on the right (on wide screens).  This card remains directly under the system health card on narrow screens. -->
    <div class="card">
      {% if latest_preview %}
        <h2>🖼️ LATEST PREVIEW</h2>
        <img class="thumb" src="{{ latest_url }}" alt="latest preview" loading="lazy" decoding="async">
      {% endif %}
      <h2 style="margin-top: {% if latest_preview %}.8rem{% else %}0{% endif %};">⬆️ / 🌐 UPLOAD OR FETCH &amp; DISPLAY</h2>
      <div class="form-stack">
        <form action="{{ url_for('upload') }}" method="post" enctype="multipart/form-data" class="form-row align-right" title="Upload an image and display it">
          <label>Image <input type="file" name="file" accept="image/*" required></label>
          <label>Mode <select name="mode"><option value="fit" selected>fit</option><option value="fill">fill</option></select></label>
          <label>Matte <select name="matte"><option value="black" selected>black</option><option value="white">white</option></select></label>
          <button class="btn" type="submit">UPLOAD &amp; DISPLAY</button>
        </form>
        <form action="{{ url_for('fetch_url') }}" method="post" class="form-row align-right" title="Fetch an image by URL and display it">
          <label>Image URL <input type="url" name="image_url" required></label>
          <label>Mode <select name="mode"><option value="fit" selected>fit</option><option value="fill">fill</option></select></label>
          <label>Matte <select name="matte"><option value="black" selected>black</option><option value="white">white</option></select></label>
          <button class="btn" type="submit">FETCH &amp; DISPLAY</button>
        </form>
      </div>
    </div>
  </div>

<div class="card">
  <h2>📂 RECENT UPLOADS</h2>
  <div class="gallery recent-gallery">
    {% for name in uploads %}
      <a href="{{ url_for('display_existing', filename=name) }}?mode=fill" title="Display {{ name }}"><img class="thumb" src="{{ url_for('static', filename='uploads/' + name) }}" alt="{{ name }}" loading="lazy" decoding="async"></a>
    {% else %}
      <p class="notice">No uploads yet.</p>
    {% endfor %}
  </div>
</div>"""

SYNC = """
<div class="grid grid-fluid">
  <div class="card">
    <h2>🔁 UNISON BACKUP</h2>
    <div class="actions-row" style="margin-bottom:.5rem">
      <span class="badge info">Last OK: {{ bkp.last_ok }}</span>
      <span class="badge {{ 'ok' if bkp.status=='OK' else ('warn' if bkp.status=='N/A' else 'bad') }}">Last Attempt: {{ bkp.last_any }} ({{ bkp.status }})</span>
      {% if bkp.reason %}<span class="badge warn">Reason: {{ bkp.reason }}</span>{% endif %}
      <span class="badge">RSSI {{ bkp.rssi }}</span>
      <span class="badge">Loss {{ bkp.loss }}</span>
      <span class="badge">Lat {{ bkp.lat }}</span>
      <form method="post" action="{{ url_for('run_script', name='sync') }}"><button class="btn">SYNC NOW</button></form>
    </div>
    <div class="notice">Log file: {{ log_path }}</div>
  </div>

  <div class="card">
    <h2>📜 LIVE LOG</h2>
    <div id="logbox" class="logbox" aria-live="polite">Loading…</div>
    <script>
    (function(){
      const box = document.getElementById('logbox'); let lastHash = "";
      async function tick(){
        try{
          const r = await fetch('{{ url_for("logfeed") }}?n=300', {cache:"no-store"});
          if(!r.ok) return;
          const data = await r.json();
          if(data.hash && data.hash === lastHash) return;
          lastHash = data.hash || "";
          box.textContent = (data.lines || []).join('\\n');
          box.scrollTop = box.scrollHeight;
        }catch(e){}
      }
      window.addEventListener('load', tick); setInterval(tick, 4000);
    })();
    </script>
  </div>
</div>
"""

BROWSER = """
<div class="card">
  <h2>🗂️ FILE BROWSER</h2>
  <p class="notice">Browsing: <code>static/{{ subpath }}</code></p>
  <div class="row">
    <form method="get" action="{{ url_for('browser', subpath='') }}"><button class="btn">ROOT</button></form>
    {% if parent_link %}
      <form method="get" action="{{ parent_link }}"><button class="btn">UP</button></form>
    {% endif %}
    <form method="post" action="{{ url_for('mkdir', subpath=subpath) }}"><label>New Folder <input name="name" required></label><button class="btn" type="submit">CREATE</button></form>
    <form method="post" action="{{ url_for('upload_to', subpath=subpath) }}" enctype="multipart/form-data"><label>Upload <input type="file" name="file" accept="image/*" required></label><button class="btn" type="submit">UPLOAD</button></form>
  </div>
  <hr>
  <h3>FOLDERS</h3>
  <div class="row">
    {% for d in dirs %}
      <form method="get" action="{{ url_for('browser', subpath=d.link) }}"><button class="btn">{{ d.name }}/</button></form>
    {% else %}
      <span class="notice">No subfolders.</span>
    {% endfor %}
  </div>
  <hr>
  <h3>IMAGES</h3>
  <div class="gallery">
    {% for f in imgs %}
      <div>
        <a href="{{ url_for('static', filename=f.rel) }}" target="_blank" title="{{ f.name }}"><img class="thumb" src="{{ url_for('static', filename=f.rel) }}" alt="{{ f.name }}"></a>
        <div class="row" style="margin-top:.3rem">
          <form method="post" action="{{ url_for('display_from_browser', subpath=subpath) }}">
            <input type="hidden" name="name" value="{{ f.name }}">
            <select name="mode"><option value="fit" selected>fit</option><option value="fill">fill</option></select>
            <select name="matte"><option value="black" selected>black</option><option value="white">white</option></select>
            <button class="btn" type="submit">DISPLAY</button>
          </form>
          <form method="post" action="{{ url_for('delete_file', subpath=subpath) }}" onsubmit="return confirm('Delete {{ f.name }}?');">
            <input type="hidden" name="name" value="{{ f.name }}">
            <button class="btn" type="submit">DELETE</button>
          </form>
        </div>
      </div>
    {% else %}
      <p class="notice">No images here.</p>
    {% endfor %}
  </div>
</div>
"""

# ── Routes & helpers ─────────────────────────────────────────────────────
def _bg_vars():
    rel, blur = select_bg_pattern()
    return (url_for("static", filename=rel) if rel else None, blur)

def _resolve_pisugar_links() -> Tuple[Optional[str], Optional[str]]:
    if not PISUGAR:
        return None, None
    bases = (PISUGAR_BASE_LOCAL, PISUGAR_BASE_LOOP)
    base_ok: Optional[str] = None
    for b in bases:
        u = urlparse(b)
        if _tcp_port_open(u.hostname or "127.0.0.1", int(u.port or 8421)):
            base_ok = b
            break
    if not base_ok:
        return None, None
    for path in ("/api/v1/getAll", "/api/getAll"):
        url = base_ok + path
        if _http_head_ok(url) or _http_get(url, timeout=1.5)[0] in range(200, 400):
            return base_ok, url
    return base_ok, None

@app.route("/buffer/<key>.png", methods=["GET"])
def buffer_image(key: str):
    data = BUF.get(key)
    if not data:
        return app.response_class(response=b"Not Found", status=404, mimetype="text/plain")
    resp = app.response_class(response=data, status=200, mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.route("/logfeed", methods=["GET"])
def logfeed():
    path = Path(LOG_FILE)
    try:
        n = int(request.args.get("n", "200"))
    except Exception:
        n = 200
    n = max(10, min(n, 1000))
    if not path.exists() or not path.is_file():
        payload = {"lines": ["Log file not found."], "hash": "0-0"}
    else:
        lines = _read_last_lines(path, n)
        try:
            st = path.stat()
            h = f"{st.st_mtime_ns}-{st.st_size}"
        except Exception:
            h = f"{int(time.time())}-0"
        payload = {"lines": lines, "hash": h}
    return app.response_class(
        response=json.dumps(payload),
        status=200, mimetype="application/json", headers={"Cache-Control": "no-store"}
    )

@app.route("/healthz", methods=["GET"])
def healthz():
    return app.response_class(
        response=json.dumps({"ok": True, "inky": bool(INKY), "width": WIDTH, "height": HEIGHT}),
        status=200, mimetype="application/json"
    )

# ── Main pages ───────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    uptime = get_uptime()
    disk = get_disk()
    mem = get_mem()
    cpu_load = get_cpu_load_pct()
    cpu_temp = get_cpu_temp_c()
    wifi = get_wifi_rssi()

    def _num(s: str) -> float:
        try:
            return float(s.strip("%°C"))
        except Exception:
            return 100.0

    try:
        uploads = sorted(
            (p.name for p in UPLOAD_DIR.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED),
            reverse=True
        )[:24]
    except Exception:
        uploads = []

    bat = probe_pisugar_status() if PISUGAR else {"reachable": False, "level": "N/A", "voltage": "N/A", "charging": "N/A"}

    latest_key = BUF.latest()
    latest_preview = bool(latest_key)
    latest_url = (url_for('buffer_image', key=latest_key) if latest_key else "")

    body = render_template_string(
        INDEX,
        uptime=uptime, disk=disk, mem=mem,
        cpu_load=cpu_load, cpu_load_pct_num=_num(cpu_load),
        cpu_temp=cpu_temp, cpu_temp_num=_num(cpu_temp),
        wifi=wifi, wifi_if=WIFI_IF,
        latest_preview=latest_preview, latest_url=latest_url,
        uploads=uploads, pisugar=PISUGAR, bat=bat,
    )
    bg_url, bg_blur = _bg_vars()
    pisugar_web, battery_json = _resolve_pisugar_links()
    return render_template_string(
        BASE, body=body, inky=bool(INKY), width=WIDTH, height=HEIGHT,
        bg_url=bg_url, bg_blur=bg_blur,
        pisugar=PISUGAR, pisugar_web=pisugar_web, battery_json=battery_json
    )

@app.route("/sync", methods=["GET"])
def sync_page():
    bkp = parse_unison_log()
    body = render_template_string(SYNC, bkp=bkp, log_path=str(LOG_FILE))
    bg_url, bg_blur = _bg_vars()
    pisugar_web, battery_json = _resolve_pisugar_links()
    return render_template_string(
        BASE, body=body, inky=bool(INKY), width=WIDTH, height=HEIGHT,
        bg_url=bg_url, bg_blur=bg_blur,
        pisugar=PISUGAR, pisugar_web=pisugar_web, battery_json=battery_json
    )

# ── Upload / fetch / display ─────────────────────────────────────────────
@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    mode = _clamp_mode(request.form.get("mode", "fit"))
    matte = _clamp_matte(request.form.get("matte", "black"))
    if not f or not f.filename:
        flash("No file provided."); return redirect(url_for("index"))
    if not allowed_file(f.filename):
        flash("Unsupported file type."); return redirect(url_for("index"))
    saved: Optional[Path] = None
    try:
        saved = save_upload(f)
        with Image.open(saved) as im: im.verify()
        display_and_preview(saved, matte=matte, mode=mode)
        flash(f"Displayed {saved.name}")
    except (UnidentifiedImageError, OSError, ValueError) as e:
        try:
            if saved: saved.unlink(missing_ok=True)
        except Exception:
            pass
        flash(f"Upload failed: {e}")
    except RuntimeError as e:
        flash(str(e))
    return redirect(url_for("index"))

@app.route("/fetch-url", methods=["POST"])
def fetch_url():
    url_val = (request.form.get("image_url") or "").strip()
    mode = _clamp_mode(request.form.get("mode", "fit"))
    matte = _clamp_matte(request.form.get("matte", "black"))
    if not url_val:
        flash("No URL provided."); return redirect(url_for("index"))
    try:
        saved = fetch_image_to_uploads(url_val)
        with Image.open(saved) as im: im.verify()
        display_and_preview(saved, matte=matte, mode=mode)
        flash(f"Fetched & displayed {saved.name}")
    except (UnidentifiedImageError, OSError, ValueError) as e:
        flash(f"Fetch failed: {e}")
    except RuntimeError as e:
        flash(str(e))
    return redirect(url_for("index"))

# backward‑compat alias
app.add_url_rule("/fetch", view_func=fetch_url, methods=["POST"])

@app.route("/display/<path:filename>", methods=["GET"])
def display_existing(filename: str):
    file_path = (UPLOAD_DIR / filename).resolve()
    if not (_safe_in_static(file_path) and file_path.is_file()):
        flash("File not found."); return redirect(url_for("index"))
    mode = _clamp_mode(request.args.get("mode", "fit"))
    matte = _clamp_matte(request.args.get("matte", "black"))
    try:
        display_and_preview(file_path, matte=matte, mode=mode)
        flash(f"Displayed {file_path.name}")
    except Exception as e:
        flash(f"Display failed: {e}")
    return redirect(url_for("index"))

# ── Script runner ────────────────────────────────────────────────────────
@app.route("/run/<name>", methods=["POST"])
def run_script(name: str):
    base_cmd = SCRIPTS.get(name)
    if not base_cmd:
        flash(f"Unknown script: {name}")
        return redirect(url_for("index"))
    cmd = _build_cmd_with_opts(name, request.form) if request.form.get("with_opts") else list(base_cmd)
    ok, why = _cmd_is_usable(cmd)
    if not ok:
        if name == "landscape":
            alt = (BASE_DIR / "landscapes.py")
            if alt.exists() and len(cmd) >= 2:
                cmd[1] = str(alt.name)
                ok, why = _cmd_is_usable(cmd)
        if not ok:
            flash(why)
            return redirect(url_for("sync_page") if name == "sync" else url_for("index"))
    try:
        out = subprocess.run(cmd, cwd=str(BASE_DIR), timeout=SCRIPT_TIMEOUT, text=True, capture_output=True)
        if out.returncode != 0:
            err = (out.stderr or out.stdout or "").strip()
            flash(f"{name} exited {out.returncode}: {err[:200]}")
        else:
            used = " ".join(cmd[2:]) if len(cmd) > 2 else ""
            flash(f"Ran {name}{(' ('+used+')') if used else ''}.")
    except subprocess.TimeoutExpired:
        flash(f"{name} timed out after {SCRIPT_TIMEOUT}s")
    except Exception as e:
        flash(f"{name} failed: {e}")
    return redirect(url_for("sync_page") if name == "sync" else url_for("index"))

# ── Power ────────────────────────────────────────────────────────────────
def _systemctl_call(*args: str) -> int:
    for p in (shutil.which("systemctl"), "/usr/bin/systemctl", "/bin/systemctl"):
        if p and Path(p).exists():
            try:
                return subprocess.call([p, *args])
            except Exception:
                continue
    return 127

@app.route("/power", methods=["POST"])
def power():
    action = (request.form.get("action", "") or "").lower()
    try:
        if action == "reboot":
            rc = _systemctl_call("reboot")
            flash("Reboot requested." if rc == 0 else "Reboot failed (permissions?).")
        elif action in {"shutdown", "sleep"}:
            if PISUGAR and action == "sleep":
                try:
                    for path in ("/api/v1/sleep", "/api/sleep", "/api/v1/hibernate"):
                        req = Request(PISUGAR_BASE_LOOP + path, method="POST", headers={"User-Agent": "Squirt/1.1"})
                        with urlopen(req, timeout=2.0):
                            break
                except Exception:
                    pass
            rc = _systemctl_call("poweroff", "-i")
            flash(("Sleep" if PISUGAR else "Shutdown") + (" requested." if rc == 0 else " failed (permissions?)."))
        else:
            flash("Unknown power action.")
    except Exception as e:
        flash(f"Power action error: {e}")
    return redirect(url_for("index"))

# ── Browser ──────────────────────────────────────────────────────────────
@app.route("/browser/", defaults={"subpath": ""}, methods=["GET"])
@app.route("/browser/<path:subpath>", methods=["GET"])
def browser(subpath: str):
    d = _subpath_to_dir(subpath)
    parent_link = url_for("browser", subpath=d.parent.relative_to(ROOT).as_posix()) if d != ROOT else None
    dirs, imgs = _list_dir(d)
    body = render_template_string(BROWSER, subpath=d.relative_to(ROOT).as_posix(), parent_link=parent_link, dirs=dirs, imgs=imgs)
    bg_url, bg_blur = _bg_vars()
    pisugar_web, battery_json = _resolve_pisugar_links()
    return render_template_string(
        BASE, body=body, inky=bool(INKY), width=WIDTH, height=HEIGHT,
        bg_url=bg_url, bg_blur=bg_blur,
        pisugar=PISUGAR, pisugar_web=pisugar_web, battery_json=battery_json
    )

@app.route("/browser/<path:subpath>/mkdir", methods=["POST"])
def mkdir(subpath: str):
    d = _subpath_to_dir(subpath)
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Folder name required.")
        return redirect(url_for("browser", subpath=subpath))
    target = d / name
    if not _safe_in_static(target):
        flash("Invalid path.")
        return redirect(url_for("browser", subpath=subpath))
    try:
        target.mkdir(parents=True, exist_ok=False)
        flash(f"Created {name}/")
    except FileExistsError:
        flash("Folder already exists.")
    except Exception as e:
        flash(f"Create failed: {e}")
    return redirect(url_for("browser", subpath=subpath))

@app.route("/browser/<path:subpath>/upload", methods=["POST"])
def upload_to(subpath: str):
    d = _subpath_to_dir(subpath)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("No file.")
        return redirect(url_for("browser", subpath=subpath))
    if not allowed_file(f.filename):
        flash("Unsupported file type.")
        return redirect(url_for("browser", subpath=subpath))
    tmp: Optional[Path] = None
    try:
        data = f.read()
        if not data:
            raise UnidentifiedImageError("Empty upload.")
        tmp = d / f".{uuid.uuid4().hex}.part"
        tmp.write_bytes(data)
        with Image.open(tmp) as im: im.verify()
        with Image.open(tmp) as im2: ext2 = _safe_image_ext(im2.format)
        final = d / f"{uuid.uuid4().hex}{ext2}"
        tmp.replace(final)
        flash(f"Uploaded {final.name}")
    except (UnidentifiedImageError, OSError, ValueError) as e:
        try:
            if tmp: tmp.unlink(missing_ok=True)
        except Exception:
            pass
        flash(f"Upload failed: {e}")
    return redirect(url_for("browser", subpath=subpath))

@app.route("/browser/<path:subpath>/delete", methods=["POST"])
def delete_file(subpath: str):
    d = _subpath_to_dir(subpath)
    name = request.form.get("name", "")
    target = (d / name).resolve()
    if not (_safe_in_static(target) and target.is_file()):
        flash("File not found.")
        return redirect(url_for("browser", subpath=subpath))
    try:
        target.unlink()
        flash(f"Deleted {name}")
    except Exception as e:
        flash(f"Delete failed: {e}")
    return redirect(url_for("browser", subpath=subpath))

@app.route("/browser/<path:subpath>/display", methods=["POST"])
def display_from_browser(subpath: str):
    d = _subpath_to_dir(subpath)
    name = request.form.get("name", "")
    mode = _clamp_mode(request.form.get("mode", "fit"))
    matte = _clamp_matte(request.form.get("matte", "black"))
    target = (d / name).resolve()
    if not (_safe_in_static(target) and target.is_file()):
        flash("File not found.")
        return redirect(url_for("browser", subpath=subpath))
    if target.suffix.lower() not in ALLOWED:
        flash("Not an image.")
        return redirect(url_for("browser", subpath=subpath))
    try:
        display_and_preview(target, matte=matte, mode=mode)
        flash(f"Displayed {name}")
    except Exception as e:
        flash(f"Display failed: {e}")
    return redirect(url_for("browser", subpath=subpath))

# ── Entrypoint ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        _ = select_bg_pattern()  # warm path
    except Exception:
        pass
    try:
        port = _env_int("PORT", 8080, 1, 65535)
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        log.error("Failed to start Flask app: %s", e)
        sys.exit(1)
