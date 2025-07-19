#!/usr/bin/env python3
"""
status.py – Production Build v1.2
───────────────────────────────────────────
Displays system status, connectivity, disk usage, thermal state, battery and RTC health (if available).
Designed for Inky e-paper displays; runs headless and outputs PNG for preview if no hardware present.
Usage: See CLI flags below for options to override warning indicator and battery/RTC checks.

"""

from __future__ import annotations
import argparse, os, shutil, subprocess, sys, time, datetime as dt, logging, socket, traceback
from pathlib import Path
from typing import Tuple, List
from datetime import timezone
from PIL import Image, ImageDraw, ImageFont


# ─── CLI ───────────────────────────────────────────────────────────────────
P = argparse.ArgumentParser()
P.add_argument("--force-triangle", action="store_true")
P.add_argument("--no-triangle",    action="store_true")
P.add_argument("--no-pisugar",     action="store_true")
CLI, _ = P.parse_known_args()

ENV_FORCE   = bool(os.getenv("STATUS_FORCE_WARN"))
ENV_NO_WARN = bool(os.getenv("STATUS_NO_WARN"))
ENV_NO_PISU = bool(os.getenv("STATUS_NO_PISUGAR"))

ALWAYS_WARN = (CLI.force_triangle or ENV_FORCE) and not (CLI.no_triangle or ENV_NO_WARN)
NEVER_WARN  = CLI.no_triangle or ENV_NO_WARN
USE_PISUGAR = not (CLI.no_pisugar or ENV_NO_PISU)


# ─── Paths & logging ───────────────────────────────────────────────────────
ROOT   = Path(__file__).with_name("static")
STATUS = ROOT / "status"; STATUS.mkdir(parents=True, exist_ok=True)
LOG    = STATUS / f"boot_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(message)s",
                    handlers=[logging.FileHandler(LOG),
                              logging.StreamHandler(sys.stdout)])
log = logging.getLogger("status")


# ─── Display probe ─────────────────────────────────────────────────────────
INKY_TYPE, INKY_COLOUR = "el133uf1", None
HEADLESS_RES = (1600, 1200)

def _pip_install(*pkgs: str):
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    "--user", "--break-system-packages", *pkgs],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def init_inky() -> Tuple[object | None, int, int]:
    try:
        import inky, numpy  # noqa: F401
    except ModuleNotFoundError:
        _pip_install("inky>=2.1.0", "numpy")
        try:
            import inky  # noqa: F401
        except ModuleNotFoundError:
            return None, *HEADLESS_RES

    try:
        from inky.auto import auto
        dev = auto()
        return dev, *dev.resolution
    except Exception:
        pass

    class_map = {"el133uf1": "InkyEL133UF1",
                 "phat": "InkyPHAT",
                 "what": "InkyWHAT"}
    try:
        cls = getattr(__import__("inky", fromlist=[class_map[INKY_TYPE]]),
                      class_map[INKY_TYPE])
        dev = cls(INKY_COLOUR) if INKY_TYPE in ("phat", "what") else cls()
        return dev, *dev.resolution
    except Exception:
        return None, *HEADLESS_RES

INKY, WIDTH, HEIGHT = init_inky()
if not INKY:
    log.warning("Headless mode")


# ─── Palette ───────────────────────────────────────────────────────────────
PAL = {
    "BLACK": "#000",
    "WHITE": "#FFF",
    "RED"  : "#F00",
    "YEL"  : "#FF0",
    "BLU"  : "#00F",
    "GRN"  : "#0A0",
}

def _hex(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

CLR_BLACK, CLR_WHITE, CLR_RED, CLR_YEL, CLR_BLU, CLR_GRN = map(_hex, PAL.values())
CLR_ORNG = (255, 140, 0)
CLR_OK, CLR_WARN, CLR_ERR = CLR_GRN, CLR_YEL, CLR_RED
CLR_TXT, CLR_BG = CLR_BLACK, CLR_WHITE


# ─── Layout ────────────────────────────────────────────────────────────────
FONT_STATUS = 48
FONT_BANNER = int(FONT_STATUS * 3)
FONT_BANG   = int(FONT_BANNER * 0.30)
FONT_FOOT   = 40

LINE_H   = 160            # ↑ increased
LEFT_PAD = 80
EDGE     = 48
ICON_R   = 24
COL_W    = (WIDTH - 2 * LEFT_PAD) // 2

# Thermometer sizing & temp scale
THM_SCALE    = 1.25       # ↑ 25 %
THM_W        = int(14 * THM_SCALE)
THM_H        = int((LINE_H - 20) * 0.75 * THM_SCALE)
THM_BULB_R   = THM_W

TEMP_MIN = 20.0           # °C
TEMP_MAX = 75.0
TEMP_GRN = 55.0
TEMP_YEL = 60.0           # ≥60 → red

def _font(sz: int, bold=False):
    p = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"
    try:
        return ImageFont.truetype(p, sz)
    except Exception:
        return ImageFont.load_default()

F_STAT = _font(FONT_STATUS)
F_BANN = _font(FONT_BANNER, True)
F_BANG = _font(FONT_BANG)
F_FOOT = _font(FONT_FOOT)

def txt_w(f, t):
    return int(getattr(f, "getlength", lambda s: f.getbbox(s)[2])(t))


# ─── Probes ────────────────────────────────────────────────────────────────
PING_CT, PING_TO = 3, 2

def ping_ok(host: str):
    try:
        rc = subprocess.run(["ping", "-c", str(PING_CT), "-W", str(PING_TO), host],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        return rc == 0, "OK" if rc == 0 else "FAIL"
    except FileNotFoundError:
        return False, "N/A"

def human(n: int):
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    v = float(n)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:0.1f} {u}"
        v /= 1024

def format_storage_line(free_b: int, total_b: int):
    used_pct = (1 - free_b / total_b) * 100 if total_b else 0.0
    free_str = human(free_b)
    return f"Storage: {used_pct:0.1f}%\n({free_str} available)"

def storage_info():
    total, _, free = shutil.disk_usage("/")
    ok = (free / total * 100) >= 15 if total else False
    return ok, format_storage_line(free, total), (free / total * 100 if total else 0)

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

def cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return True, int(f.read().strip()) / 1000
    except Exception:
        return False, None


# ─── Drawing helpers ───────────────────────────────────────────────────────
def wrap(text: str, max_w: int, font):
    if "\n" in text:
        return text
    words = text.split(" ")
    out, cur = "", words[0]
    for w in words[1:]:
        trial = f"{cur} {w}"
        if txt_w(font, trial) <= max_w:
            cur = trial
        else:
            out += cur + "\n"
            cur = w
    return out + cur

def draw_stat(d: ImageDraw.Draw, y: int, label: str, txt: str, clr, col: int = 0):
    x0 = LEFT_PAD + col * COL_W
    cx = x0 - LEFT_PAD // 2
    d.ellipse((cx - ICON_R, y + ICON_R / 2,
               cx + ICON_R, y + ICON_R * 2.5),
              fill=clr, outline=clr)
    message = f"{label}: {txt}" if label else txt
    d.multiline_text((x0, y),
                     wrap(message, COL_W - EDGE, F_STAT),
                     font=F_STAT, fill=CLR_TXT, spacing=4)

def render_cpu(d: ImageDraw.Draw, x: int, y: int, h: int, temp: float | None):
    """Draw segmented thermometer (green/yellow/red) and tick line."""
    top = y + THM_BULB_R
    # segment heights (proportional to temp thresholds)
    span = TEMP_MAX - TEMP_MIN
    red_h    = int(h * (TEMP_MAX - TEMP_YEL) / span)           # 15 °C
    yellow_h = int(h * (TEMP_YEL - TEMP_GRN) / span)           # 5 °C
    green_h  = h - red_h - yellow_h                            # remainder

    # Bars (draw top→bottom)
    d.rectangle([x, top, x + THM_W, top + red_h],              fill=CLR_RED)
    d.rectangle([x, top + red_h,
                 x + THM_W, top + red_h + yellow_h],           fill=CLR_YEL)
    d.rectangle([x, top + red_h + yellow_h,
                 x + THM_W, top + h],                          fill=CLR_GRN)

    # Bulb
    cx, cy = x + THM_W // 2, top + h
    d.ellipse([cx - THM_BULB_R, cy - THM_BULB_R,
               cx + THM_BULB_R, cy + THM_BULB_R],
              fill=CLR_GRN)

    # Tick line
    if temp is not None:
        t = max(TEMP_MIN, min(TEMP_MAX, temp))
        ratio = (t - TEMP_MIN) / span
        py = top + h - int(ratio * h)
        d.line([x - 4, py, x + THM_W + 4, py],
               fill=CLR_BLACK, width=4)

def draw_cpu(d: ImageDraw.Draw, y: int, temp: float | None, col: int = 0):
    """CPU line with thermometer centred on its text."""
    x0 = LEFT_PAD + col * COL_W
    cx = x0 - LEFT_PAD // 2

    txt = "CPU N/A" if temp is None else f"CPU {temp:0.1f}℃"
    wrapped = wrap(txt, COL_W - EDGE, F_STAT)

    # Estimate text height
    lines   = wrapped.count("\n") + 1
    text_h  = lines * FONT_STATUS + (lines - 1) * 4

    thermo_centre = y + text_h // 2
    thermo_top    = int(thermo_centre - (THM_BULB_R + THM_H / 2))

    render_cpu(d, cx - THM_W // 2, thermo_top, THM_H, temp)
    d.multiline_text((x0, y), wrapped,
                     font=F_STAT, fill=CLR_TXT, spacing=4)

def banner(d: ImageDraw.Draw):
    gap = 26
    txt = "SQUIRT"
    cols = [CLR_BLACK, CLR_RED, CLR_YEL, CLR_ORNG, CLR_GRN, CLR_BLU]
    total = sum(txt_w(F_BANN, c) for c in txt) + (len(txt) - 1) * gap
    x = (WIDTH - total) // 2
    for i, c in enumerate(txt):
        d.text((x, 12), c, font=F_BANN,
               fill=cols[i], stroke_width=2, stroke_fill=CLR_BLACK)
        x += txt_w(F_BANN, c) + gap

def warn_triangle(d: ImageDraw.Draw):
    bang = "!"
    bx0, by0, bx1, by1 = d.textbbox((0, 0), bang, font=F_BANG)
    side = int(max(bx1 - bx0, by1 - by0) * 1.2 + 32)
    h = int(side * (3 ** 0.5) / 2)
    cx, top = WIDTH // 2, HEIGHT - 184 - h
    pts = [(cx, top),
           (cx - side // 2, top + h),
           (cx + side // 2, top + h)]
    d.polygon(pts, fill=CLR_YEL)
    for i in range(3):
        d.line([pts[i], pts[(i + 1) % 3]], fill=CLR_BLACK, width=6)
    d.text((cx - (bx1 - bx0) // 2, top + 0.46 * h - (by1 - by0) // 2),
           bang, font=F_BANG, fill=CLR_BLACK)

def footer(d: ImageDraw.Draw, warn: bool):
    if warn and not NEVER_WARN:
        warn_triangle(d)
    y = HEIGHT - 136
    for i, line in enumerate(("This screen will automatically refresh.",
                              "Please standby as normal function resumes…")):
        d.text(((WIDTH - txt_w(F_FOOT, line)) // 2,
                y + i * (FONT_FOOT + 4)),
               line, font=F_FOOT, fill=CLR_TXT)


# ─── Frame builder ────────────────────────────────────────────────────────
def make_frame():
    img = Image.new("RGB", (WIDTH, HEIGHT), CLR_BG)
    d   = ImageDraw.Draw(img)
    banner(d)

    y = FONT_BANNER + 60
    errs: List[str] = []

    ok1, t1 = ping_ok("nasa.gov")
    ok2, t2 = ping_ok("xkcd.com")
    draw_stat(d, y, "NASA ping", t1, CLR_OK if ok1 else CLR_ERR, 0)
    draw_stat(d, y, "XKCD ping", t2, CLR_OK if ok2 else CLR_ERR, 1)

    if not ok1:
        errs.append("NASA ping")
    if not ok2:
        errs.append("XKCD ping")

    y += LINE_H
    ok_st, txt_st, free_pct = storage_info()
    ok_cpu, deg            = cpu_temp()

    draw_stat(d, y, "", txt_st,
              CLR_ERR if free_pct < 10 else CLR_WARN if free_pct < 20 else CLR_OK,
              0)
    draw_cpu (d, y, deg, 1)

    if not ok_st:
        errs.append("Disk")
    if not ok_cpu:
        errs.append("CPU temp")

    y += LINE_H
    if USE_PISUGAR:
        ok_b, txt_b = bat_info()
        draw_stat(d, y, "Battery", txt_b, CLR_OK if ok_b else CLR_ERR, 0)
        if not ok_b:
            errs.append("Battery")

        y += LINE_H
        ok_r, txt_r = rtc_info()
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        draw_stat(d, y, "Clock", f"{now} | {txt_r}", CLR_OK if ok_r else CLR_ERR, 0)
        if not ok_r:
            errs.append("RTC")
    else:
        draw_stat(d, y, "Battery", "disabled", CLR_TXT, 0)
        y += LINE_H
        draw_stat(d, y, "Clock", "disabled", CLR_TXT, 0)

    footer(d, bool(errs) or ALWAYS_WARN)
    return img, errs


# ─── Main ──────────────────────────────────────────────────────────────────
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
