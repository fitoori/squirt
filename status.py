#!/usr/bin/env python3
from __future__ import annotations
import shutil, subprocess, sys, time, datetime as dt, logging, socket, traceback
from pathlib import Path
from typing import Tuple, List
from datetime import timezone

# ────────── logging ──────────
ROOT = Path(__file__).with_name("static")
STATUS = ROOT / "status"; STATUS.mkdir(parents=True, exist_ok=True)
LOG = STATUS / f"boot_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    handlers=[logging.FileHandler(LOG), logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("status")

# ───────── constants ─────────
PING = {"nasa.gov": "NASA", "xkcd.com": "XKCD"}
PING_CT, PING_TO = 1, 2
WARN_FREE, ERR_FREE = 15.0, 5.0

INKY_TYPE, INKY_COLOUR = "el133uf1", None
HEADLESS = (1600, 1200)

# Spectra-6 palette (white omitted)
CLR_BLACK, CLR_RED, CLR_YELL, CLR_ORNG, CLR_GRN, CLR_BLUE = (
    (0,0,0), (220,0,0), (255,215,0), (255,140,0), (0,170,0), (0,0,255))
CLR_OK, CLR_WARN, CLR_ERR = CLR_GRN, CLR_ORNG, CLR_RED
CLR_TXT, CLR_BG = CLR_BLACK, (255,255,255)

FONT_STATUS = 48
FONT_BANNER = int(FONT_STATUS*1.4)
FONT_FOOT   = 40
LINE_H      = 150
LEFT_PAD    = 80
ICON_R      = 24
EDGE_MARGIN = 48          # wrap when within 48 px of right edge

BANNER_COL = [CLR_BLACK, CLR_RED, CLR_YELL, CLR_ORNG, CLR_GRN, CLR_BLUE]

# ─── Pillow & fonts ───
from PIL import Image, ImageDraw, ImageFont

def _font(sz:int, bold=False):
    path = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf"
    try:  return ImageFont.truetype(path, sz)
    except Exception: return ImageFont.load_default()

F_STAT = _font(FONT_STATUS)
F_BANN = _font(FONT_BANNER, bold=True)
F_FOOT = _font(FONT_FOOT)

def txt_w(font, txt):            # Pillow-10/legacy safe
    try:  return int(font.getlength(txt))
    except AttributeError: return font.getbbox(txt)[2]

# ─── Inky detection (unchanged) ───
def _pip_install(*pkgs:str):
    subprocess.run([sys.executable,"-m","pip","install","--quiet","--user",
                    "--break-system-packages",*pkgs],
                   timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def init_inky()->Tuple[object|None,int,int]:
    try: import inky,numpy  # noqa
    except ModuleNotFoundError:
        _pip_install("inky>=2.1.0","numpy")
        try: import inky,numpy  # noqa
        except ModuleNotFoundError: return None,*HEADLESS
    try:
        from inky.auto import auto; dev=auto(); return dev,*dev.resolution
    except Exception:
        class_map={"el133uf1":"InkyEL133UF1","phat":"InkyPHAT","what":"InkyWHAT"}
        try:
            cls=getattr(__import__("inky",fromlist=[class_map[INKY_TYPE]]),class_map[INKY_TYPE])
            dev=cls(INKY_COLOUR) if INKY_TYPE in ("phat","what") else cls()
            return dev,*dev.resolution
        except Exception: return None,*HEADLESS

INKY,WIDTH,HEIGHT = init_inky()
if not INKY: log.warning("Headless mode: no Inky detected")

# ─── probes (unchanged logic) ───
def ping_ok(h):                   # → ok,str
    try: ok=subprocess.run(["ping","-c",str(PING_CT),"-W",str(PING_TO),h],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    except FileNotFoundError: return False,"N/A"
    return ok,"OK" if ok else "FAIL"

def storage_info():               # → ok,text,pct
    t,_,f=shutil.disk_usage("/")
    pct=f/t*100 if t else 0
    return pct>=WARN_FREE,f"{pct:4.1f}% free",pct

def _ps(cmd):
    try:
        with socket.create_connection(("127.0.0.1",8423),1) as s:
            s.settimeout(2)
            s.sendall((cmd+"\n").encode()); return s.recv(64).decode().strip()
    except Exception: return None

def bat_info():                   # → ok,text
    pct=_ps("get battery"); chg=_ps("get battery_charging")
    if pct and pct.startswith("battery:"):
        val=pct.split(":",1)[1].strip()
        src="USB" if chg and chg.endswith("true") else "Battery"
        return True,f"{val}% ({src})"
    return False,"N/A"

def rtc_info():                   # → ok,text
    raw=_ps("get rtc_time")
    if not raw or not raw.startswith("rtc_time:"): return False,"rtc_time unavailable"
    ts=raw.split(":",1)[1].strip()
    try:
        rtc=dt.datetime.fromisoformat(ts)
        if rtc.tzinfo is None: rtc=rtc.replace(tzinfo=timezone.utc)
        drift=abs((dt.datetime.now(timezone.utc)-rtc).total_seconds())
        return drift<120,f"{ts} Δ{int(drift)}s"
    except ValueError: return False,f"malformed {ts}"

def colour(ok,pct=None):
    if pct is not None:
        return CLR_OK if pct>=WARN_FREE else CLR_WARN if pct>=ERR_FREE else CLR_ERR
    return CLR_OK if ok else CLR_ERR

# ─── drawing helpers ───
def safe_wrap(text:str,max_w:int,font)->str:
    """Word-wrap so each line ≤ max_w; never errors on single-word."""
    words=text.split(" ")
    if len(words)==1: return text
    out,cur="",words[0]
    for w in words[1:]:
        trial=f"{cur} {w}"
        if txt_w(font,trial)<=max_w:
            cur=trial
        else:
            out+=cur+"\n"; cur=w
    return out+cur

def draw_stat(d:ImageDraw.Draw,y,label,text,clr):
    cx=LEFT_PAD//2
    d.ellipse((cx-ICON_R,y+ICON_R/2,cx+ICON_R,y+ICON_R*2.5),fill=clr,outline=clr)
    max_w=WIDTH-LEFT_PAD-EDGE_MARGIN
    wrapped=safe_wrap(f"{label}: {text}",max_w,F_STAT)
    d.multiline_text((LEFT_PAD,y),wrapped,font=F_STAT,fill=CLR_TXT,spacing=4)

def banner(d):
    gap=24; txt="SQUIRT"
    total=sum(txt_w(F_BANN,c) for c in txt)+(len(txt)-1)*gap
    x=(WIDTH-total)//2
    for i,c in enumerate(txt):
        d.text((x,10),c,font=F_BANN,fill=BANNER_COL[i],
               stroke_width=0 if BANNER_COL[i]==CLR_BLACK else 2,
               stroke_fill=CLR_BLACK)
        x+=txt_w(F_BANN,c)+gap

def warning_triangle(d):
    side=100; h=int(side*3**0.5/2)
    cx,top=WIDTH//2,HEIGHT-160-side-10
    pts=[(cx,top),(cx-side//2,top+h),(cx+side//2,top+h)]
    d.polygon(pts,fill=CLR_YELL,outline=CLR_BLACK)
    d.text((cx-8,top+h*0.25),"!",font=F_BANN,fill=CLR_BLACK)

def footer(d,warn):
    msg1="This screen will automatically refresh."
    msg2="Please standby as normal function resumes…"
    if warn: warning_triangle(d)
    y=HEIGHT-160
    for i,ln in enumerate((msg1,msg2)):
        d.text(((WIDTH-txt_w(F_FOOT,ln))//2,y+i*(FONT_FOOT+4)),
               ln,font=F_FOOT,fill=CLR_TXT)

# ─── frame ───
def make_frame():
    from PIL import Image
    img=Image.new("RGB",(WIDTH,HEIGHT),CLR_BG); d=ImageDraw.Draw(img)
    banner(d); y=FONT_BANNER+50; errs=[]
    for h,tag in PING.items():
        ok,txt=ping_ok(h); draw_stat(d,y,f"{tag} ping",txt,colour(ok)); y+=LINE_H
        if not ok: errs.append(f"{h} ping")
    ok,txt,pct=storage_info(); draw_stat(d,y,"Storage",txt,colour(ok,pct)); y+=LINE_H
    if not ok: errs.append("Low disk")
    ok,txt=bat_info(); draw_stat(d,y,"Battery",txt,colour(ok)); y+=LINE_H
    if not ok: errs.append("Battery")
    ok,txt=rtc_info(); now=dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    draw_stat(d,y,"Clock",f"{now} | {txt}",colour(ok)); y+=LINE_H
    if not ok: errs.append("RTC")
    footer(d,bool(errs))
    return img,errs

# ─── main ───
def main():
    try:
        img,errs=make_frame()
        if INKY: INKY.set_image(img); INKY.show()
        else:
            fn=STATUS/f"preview_{int(time.time())}.png"; img.save(fn)
            log.info("Preview → %s",fn)
        for e in errs: log.warning("! %s",e)
    except Exception as exc:
        log.error("TOP-LEVEL %s",exc); log.debug(traceback.format_exc())

if __name__=="__main__":
    main()
