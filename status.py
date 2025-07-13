#!/usr/bin/env python3
"""
Boot-time status dashboard for Inky e-paper panels
=================================================

Features
--------
• Pings NASA & XKCD (3 attempts each)   • Filesystem free-space %
• PiSugar battery / charging (opt-out)  • RTC value & drift

Error indicator
---------------
A yellow ▲ with a black “!” appears when any probe fails.
Flags / env-vars let you force or suppress the triangle:

    --force-triangle      or STATUS_FORCE_WARN=1
    --no-triangle         or STATUS_NO_WARN=1
    --no-pisugar          or STATUS_NO_PISUGAR=1   # skip battery & RTC

Folder structure
----------------
Rendered PNG previews (headless runs) are written to
`static/status/preview_<timestamp>.png`.
"""

from __future__ import annotations
import argparse, os, shutil, subprocess, sys, time, datetime as dt, logging, socket, traceback
from pathlib import Path
from typing import Tuple, List
from datetime import timezone

# ────────────────────── CLI flags & env-vars ───────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--force-triangle", action="store_true",
                    help="always draw warning triangle")
parser.add_argument("--no-triangle", action="store_true",
                    help="never draw warning triangle")
parser.add_argument("--no-pisugar",   action="store_true",
                    help="disable PiSugar battery & RTC probes")
CLI, _ = parser.parse_known_args()

ENV_FORCE     = bool(os.getenv("STATUS_FORCE_WARN"))
ENV_NO_WARN   = bool(os.getenv("STATUS_NO_WARN"))
ENV_NO_PISUG  = bool(os.getenv("STATUS_NO_PISUGAR"))

ALWAYS_WARN = (CLI.force_triangle or ENV_FORCE) and not (CLI.no_triangle or ENV_NO_WARN)
NEVER_WARN  = (CLI.no_triangle    or ENV_NO_WARN)
USE_PISUGAR = not (CLI.no_pisugar or ENV_NO_PISUG)

# ───────────────────────────── Paths & logging ─────────────────────────────
ROOT   = Path(__file__).with_name("static")
STATUS = ROOT / "status"; STATUS.mkdir(parents=True, exist_ok=True)
LOG    = STATUS / f"boot_{dt.datetime.now():%Y%m%d_%H%M%S}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("status")

# ───────────────────────────── Hardware / Display ──────────────────────────
INKY_TYPE, INKY_COLOUR = "el133uf1", None
HEADLESS_RES           = (1600, 1200)  # same as 13.3″ Spectra-6

def _pip_install(*pkgs: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--user",
         "--break-system-packages", *pkgs],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    )

def init_inky() -> Tuple[object | None, int, int]:
    try:
        import inky, numpy  # noqa
    except ModuleNotFoundError:
        _pip_install("inky>=2.1.0", "numpy")
        try:
            import inky  # noqa
        except ModuleNotFoundError:
            return None, *HEADLESS_RES

    # 1) EEPROM auto-detect
    try:
        from inky.auto import auto
        dev = auto()
        return dev, *dev.resolution
    except Exception:
        pass

    # 2) Manual class map
    class_map = {"el133uf1": "InkyEL133UF1", "phat": "InkyPHAT", "what": "InkyWHAT"}
    try:
        cls = getattr(__import__("inky", fromlist=[class_map[INKY_TYPE]]),
                      class_map[INKY_TYPE])
        dev = cls(INKY_COLOUR) if INKY_TYPE in ("phat", "what") else cls()
        return dev, *dev.resolution
    except Exception:
        return None, *HEADLESS_RES

INKY, WIDTH, HEIGHT = init_inky()
if not INKY:
    log.warning("Headless mode: no Inky detected")

# ───────────────────────────── Appearance ──────────────────────────────────
# Spectra-6 palette (white omitted)
CLR_BLACK = (0, 0, 0)
CLR_RED   = (220, 0, 0)
CLR_YELL  = (255, 215, 0)
CLR_ORNG  = (255, 140, 0)
CLR_GRN   = (0, 170, 0)
CLR_BLUE  = (0, 0, 255)

CLR_OK, CLR_WARN, CLR_ERR = CLR_GRN, CLR_ORNG, CLR_RED
CLR_TXT, CLR_BG           = CLR_BLACK, (255, 255, 255)

# Fonts & layout
FONT_STATUS = 48
FONT_BANNER = int(FONT_STATUS * 3.0)   # 40 % larger than previous 2.2×
FONT_BANG   = int(FONT_BANNER * 0.8)
FONT_FOOT   = 40

LINE_H      = 130  # tighter stack
LEFT_PAD    = 80
ICON_R      = 24
EDGE_MARGIN = 48
BANNER_COL  = [CLR_BLACK, CLR_RED, CLR_YELL, CLR_ORNG, CLR_GRN, CLR_BLUE]

from PIL import Image, ImageDraw, ImageFont

def _font(sz: int, bold=False):
    path = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"
    try:
        return ImageFont.truetype(path, sz)
    except Exception:
        return ImageFont.load_default()

F_STAT = _font(FONT_STATUS)
F_BANN = _font(FONT_BANNER, bold=True)
F_BANG = _font(FONT_BANG,  bold=True)
F_FOOT = _font(FONT_FOOT)

def txt_w(font, txt: str) -> int:
    try:
        return int(font.getlength(txt))
    except AttributeError:
        return font.getbbox(txt)[2]

# ───────────────────────────── Probes ──────────────────────────────────────
PING = {"nasa.gov": "NASA", "xkcd.com": "XKCD"}
PING_CT, PING_TO = 3, 2  # 3 attempts

def ping_ok(host: str):
    try:
        rc = subprocess.run(
            ["ping", "-c", str(PING_CT), "-W", str(PING_TO), host],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode
        ok = rc == 0
        return ok, "OK" if ok else "FAIL"
    except FileNotFoundError:
        return False, "N/A"

def storage_info():
    t, _, f = shutil.disk_usage("/")
    pct = f / t * 100 if t else 0
    return pct >= 15, f"{pct:4.1f}% free", pct

def _pisugar(cmd: str):
    try:
        with socket.create_connection(("127.0.0.1", 8423), 1) as s:
            s.settimeout(2)
            s.sendall((cmd + "\n").encode())
            return s.recv(64).decode().strip()
    except Exception:
        return None

def bat_info():
    if not USE_PISUGAR:
        return None
    pct = _pisugar("get battery")
    chg = _pisugar("get battery_charging")
    if pct and pct.startswith("battery:"):
        val = pct.split(":", 1)[1].strip()
        src = "USB" if chg and chg.endswith("true") else "Battery"
        return True, f"{val}% ({src})"
    return False, "N/A"

def rtc_info():
    if not USE_PISUGAR:
        return None
    raw = _pisugar("get rtc_time")
    if not raw or not raw.startswith("rtc_time:"):
        return False, "rtc_time unavailable"
    ts = raw.split(":", 1)[1].strip()
    try:
        rtc = dt.datetime.fromisoformat(ts)
        if rtc.tzinfo is None:
            rtc = rtc.replace(tzinfo=timezone.utc)
        drift = abs((dt.datetime.now(timezone.utc) - rtc).total_seconds())
        return drift < 120, f"{ts} Δ{int(drift)}s"
    except ValueError:
        return False, f"malformed {ts}"

def colour(ok: bool | None, pct: float | None = None):
    if ok is None:  # PiSugar disabled
        return CLR_TXT
    if pct is not None:
        return CLR_OK if pct >= 15 else CLR_WARN if pct >= 5 else CLR_ERR
    return CLR_OK if ok else CLR_ERR

# ───────────────────────────── Drawing helpers ────────────────────────────
def safe_wrap(text: str, max_w: int, font) -> str:
    words = text.split(" ")
    if len(words) == 1:
        return text
    out, cur = "", words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if txt_w(font, trial) <= max_w:
            cur = trial
        else:
            out += cur + "\n"
            cur = w
    return out + cur

def draw_stat(d: ImageDraw.Draw, y: int, label: str, text: str, clr):
    cx = LEFT_PAD // 2
    d.ellipse((cx - ICON_R, y + ICON_R / 2, cx + ICON_R, y + ICON_R * 2.5),
              fill=clr, outline=clr)
    wrapped = safe_wrap(f"{label}: {text}",
                        WIDTH - LEFT_PAD - EDGE_MARGIN, F_STAT)
    d.multiline_text((LEFT_PAD, y), wrapped, font=F_STAT,
                     fill=CLR_TXT, spacing=2)

def banner(d: ImageDraw.Draw):
    gap = 26
    txt = "SQUIRT"
    total = sum(txt_w(F_BANN, c) for c in txt) + (len(txt) - 1) * gap
    x = (WIDTH - total) // 2
    for i, c in enumerate(txt):
        d.text(
            (x, 12),
            c,
            font=F_BANN,
            fill=BANNER_COL[i],
            stroke_width=2,
            stroke_fill=CLR_BLACK,
        )
        x += txt_w(F_BANN, c) + gap

def warning_triangle(d: ImageDraw.Draw):
    bang = "!"
    bx0, by0, bx1, by1 = d.textbbox((0, 0), bang, font=F_BANG)
    bw, bh = bx1 - bx0, by1 - by0

    grow = 1.20
    pad  = 16
    side = int((max(bw, bh) + 2 * pad) * grow)
    h    = int(side * 3 ** 0.5 / 2)
    cx   = WIDTH // 2
    top  = HEIGHT - 184 - h

    pts = [(cx, top),
           (cx - side // 2, top + h),
           (cx + side // 2, top + h)]
    d.polygon(pts, fill=CLR_YELL)
    for i in range(3):
        d.line([pts[i], pts[(i + 1) % 3]], fill=CLR_BLACK, width=6)

    tx = cx - bw // 2
    ty = top + h * 0.46 - bh // 2
    d.text((tx, ty), bang, font=F_BANG, fill=CLR_BLACK)

def footer(d: ImageDraw.Draw, warn: bool):
    if warn and not NEVER_WARN:
        warning_triangle(d)
    y = HEIGHT - 136
    for i, ln in enumerate(("This screen will automatically refresh.",
                            "Please standby as normal function resumes…")):
        d.text(((WIDTH - txt_w(F_FOOT, ln)) // 2, y + i * (FONT_FOOT + 4)),
               ln, font=F_FOOT, fill=CLR_TXT)

# ───────────────────────────── Frame builder ──────────────────────────────
def make_frame():
    img = Image.new("RGB", (WIDTH, HEIGHT), CLR_BG)
    d = ImageDraw.Draw(img)

    banner(d)
    y = FONT_BANNER + 60
    errs: List[str] = []

    for host, tag in PING.items():
        ok, txt = ping_ok(host)
        draw_stat(d, y, f"{tag} ping", txt, colour(ok))
        y += LINE_H
        if not ok:
            errs.append(f"{host} ping")

    ok, txt, pct = storage_info()
    draw_stat(d, y, "Storage", txt, colour(ok, pct))
    y += LINE_H
    if not ok:
        errs.append("Low disk")

    if USE_PISUGAR:
        ok, txt = bat_info()
        draw_stat(d, y, "Battery", txt, colour(ok))
        y += LINE_H
        if not ok:
            errs.append("Battery")

        ok, txt = rtc_info()
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        draw_stat(d, y, "Clock", f"{now} | {txt}", colour(ok))
        if not ok:
            errs.append("RTC")
    else:
        draw_stat(d, y, "Battery", "disabled", CLR_TXT)
        y += LINE_H
        draw_stat(d, y, "Clock", "disabled", CLR_TXT)

    footer(d, bool(errs) or ALWAYS_WARN)
    return img, errs

# ───────────────────────────── Main ───────────────────────────────────────
def main():
    try:
        img, errs = make_frame()
        if INKY:
            INKY.set_image(img)
            INKY.show()
        else:
            fn = STATUS / f"preview_{int(time.time())}.png"
            img.save(fn)
            log.info("Preview → %s", fn)
        for e in errs:
            log.warning("! %s", e)
    except Exception as exc:
        log.error("Uncaught error: %s", exc)
        log.debug(traceback.format_exc())

if __name__ == "__main__":
    main()
