#!/usr/bin/env python3
from __future__ import annotations
import os, shutil, subprocess, sys, time, json, datetime as dt
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageDraw, ImageFont

ROOT_DIR   = Path(__file__).with_name("static")
STATUS_DIR = ROOT_DIR / "status"; STATUS_DIR.mkdir(parents=True, exist_ok=True)

PING_HOSTS  = {"nasa.gov": "NASA", "xkcd.com": "XKCD"}
PING_COUNT  = 1
PING_TMO    = 2
WARN_FREE   = 15.0
ERR_FREE    = 5.0

INKY_TYPE   = "el133uf1"
INKY_COLOUR = None
HEADLESS_RES = (1600, 1200)

CLR_OK   = (0, 170, 0)
CLR_WARN = (255, 165, 0)
CLR_ERR  = (220, 0, 0)
CLR_TXT  = (0, 0, 0)
CLR_BG   = (255, 255, 255)

FONT_SIZE = 44
LINE_H    = 120
LEFT_PAD  = 60
ICON_R    = 22

def _pip_install(*pkgs: str) -> None:
    subprocess.run([sys.executable, "-m", "pip", "install",
                    "--quiet", "--user", "--break-system-packages", *pkgs],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def init_inky():
    try:
        import inky, numpy  # noqa
    except ModuleNotFoundError:
        _pip_install("inky>=2.1.0", "numpy")
        try: import inky  # noqa
        except ModuleNotFoundError: pass
    try:
        from inky.auto import auto; dev = auto(); return dev, *dev.resolution
    except Exception as e:
        print("[WARN] Inky auto-detect failed:", e)
    class_map = {"el133uf1":"InkyEL133UF1","phat":"InkyPHAT","what":"InkyWHAT"}
    key = INKY_TYPE.lower()
    try:
        cls = getattr(__import__("inky", fromlist=[class_map[key]]), class_map[key])
        dev = cls(INKY_COLOUR) if key in ("phat","what") else cls()
        return dev, *dev.resolution
    except Exception as e:
        print("[ERR] Inky fallback failed:", e)
        return None, *HEADLESS_RES

INKY, WIDTH, HEIGHT = init_inky()

def ping_ok(host: str) -> bool:
    try:
        return subprocess.run(["ping","-c",str(PING_COUNT),"-W",str(PING_TMO),host],
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0
    except Exception as e:
        print(f"[ERR] Ping to {host} failed:", e)
        return False

def storage_status() -> Tuple[float,str]:
    try:
        total, used, free = shutil.disk_usage("/")
        pct_free = free/total*100.0
        return pct_free, f"{pct_free:4.1f}% free"
    except Exception as e:
        print("[ERR] Disk usage check failed:", e)
        return 0.0, "N/A"

def pisugar_status() -> Tuple[str,str]:
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8423/api/charge", timeout=1) as r:
            data = json.load(r)
            pct = str(data.get("percentage") or data.get("capacity") or data.get("percent") or "?")
            src = "USB" if data.get("charging") else "Battery"
            return pct+"%", src
    except Exception as e:
        print("[WARN] PiSugar REST API failed:", e)
        try:
            out = subprocess.check_output(["pisugar-power","-b"], text=True, timeout=2)
            pct = "".join(c for c in out if c.isdigit()) or "?"
            return pct+"%", "CLI"
        except Exception as e:
            print("[ERR] PiSugar CLI fallback failed:", e)
            return "N/A", "No PiSugar"

def rtc_ok() -> bool:
    try:
        subprocess.check_output(["hwclock","-r"], stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        print("[WARN] RTC check failed:", e)
        return False

def colour_for(ok: bool|None, pct: float|None=None):
    if pct is not None:
        return CLR_OK if pct>=WARN_FREE else CLR_WARN if pct>=ERR_FREE else CLR_ERR
    return CLR_OK if ok else CLR_ERR

def draw_stat(draw,y,label,text,color):
    cx=LEFT_PAD//2
    draw.ellipse((cx-ICON_R, y+ICON_R/2, cx+ICON_R, y+ICON_R*2.5), fill=color, outline=color)
    draw.text((LEFT_PAD,y),f"{label}: {text}", fill=CLR_TXT, font=FONT)

try:
    FONT=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", FONT_SIZE)
except Exception:
    FONT=ImageFont.load_default()
    print("[WARN] Fallback to default font")

def generate_frame():
    img=Image.new("RGB",(WIDTH,HEIGHT),CLR_BG)
    d=ImageDraw.Draw(img)
    y=40
    try:
        for host,tag in PING_HOSTS.items():
            ok=ping_ok(host)
            draw_stat(d,y,f"{tag} ping","OK" if ok else "FAIL", colour_for(ok)); y+=LINE_H
    except Exception as e:
        print("[ERR] Internet check failed:", e)

    try:
        pct,txt=storage_status()
        draw_stat(d,y,"Storage",txt, colour_for(True,pct)); y+=LINE_H
    except Exception as e:
        print("[ERR] Storage check failed:", e)

    try:
        batt,src=pisugar_status()
        draw_stat(d,y,"Battery",f"{batt} ({src})", colour_for(batt!="N/A")); y+=LINE_H
    except Exception as e:
        print("[ERR] Battery check failed:", e)

    try:
        tstr=dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        rtc=rtc_ok()
        draw_stat(d,y,"Clock",f"{tstr} | RTC {'OK' if rtc else 'FAIL'}", colour_for(rtc)); y+=LINE_H
    except Exception as e:
        print("[ERR] Clock check failed:", e)

    footer="This screen will automatically refresh. Please standby as normal function resumes…"
    try:
        d.text((LEFT_PAD,HEIGHT-LINE_H),footer,fill=CLR_TXT,font=FONT)
    except Exception as e:
        print("[ERR] Footer rendering failed:", e)

    return img

def main():
    try:
        frame=generate_frame()
    except Exception as e:
        print("[FATAL] Failed to generate frame:", e)
        return

    if INKY:
        try:
            INKY.set_image(frame); INKY.show()
        except Exception as e:
            print("[ERR] Failed to display on Inky:", e)
            fallback_path = STATUS_DIR/f"fallback_{int(time.time())}.png"
            frame.save(fallback_path)
            print("Saved fallback to:", fallback_path)
    else:
        path=STATUS_DIR/f"boot_status_{int(time.time())}.png"
        try:
            frame.save(path)
            print("Headless preview:",path)
        except Exception as e:
            print("[FATAL] Failed to save image:", e)

if __name__=="__main__":
    main()
