#!/usr/bin/env python3
"""
xkcd.py — fetch a random XKCD comic and show it on an Inky e‑paper display
(or save a PNG preview when no board is present).

This version follows the unified YorozuyaTech helper conventions so that all
related scripts share the same structure, logging style, and folder layout.

Folders
-------
static/                 (created beside this file)
└── xkcd/               ← comics cached here
    └── …               ← preview PNGs also land here when headless

Exit codes: 0 = success, 1 = hard failure (network/image/inky error).
"""

from __future__ import annotations
import os, sys, subprocess, requests
from pathlib import Path
from urllib.parse import urljoin, urlparse
from importlib import import_module
from PIL import Image, UnidentifiedImageError
from bs4 import BeautifulSoup

# ────────────────────────────── Configuration ───────────────────────────────
REMOTE_URL      = "https://c.xkcd.com/random/comic/"
ROOT_DIR        = Path(__file__).with_name("static")
SAVE_DIR        = ROOT_DIR / "xkcd"          # unique sub‑folder
TIMEOUT         = 10
RETRIES         = 2
INKY_TYPE       = "spectra13"                # override only if auto fails
INKY_COLOUR     = None                       # for PHAT/WHAT
HEADLESS_RES    = (1600, 1200)               # same as Spectra‑13 panel
# ─────────────────────────────────────────────────────────────────────────────

# Ensure folder tree exists early
SAVE_DIR.mkdir(parents=True, exist_ok=True)

# ───────────────────────────── Helper → pip install ─────────────────────────
def _pip_install(*pkgs: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--user",
         "--break-system-packages", *pkgs],
        check=False,  # don’t abort script if pip itself errors
    )

# ───────────────────────────── Helper → Inky detect ─────────────────────────
def init_inky():
    """
    Return (dev_or_None, WIDTH, HEIGHT).
    Falls back to a configurable dummy resolution when no board.
    """
    try:
        import inky, numpy  # noqa: F401
    except ModuleNotFoundError:
        print("Installing inky & numpy…")
        _pip_install("inky>=2.1.0", "numpy")
        try:
            import inky  # noqa
        except ModuleNotFoundError:
            pass

    try:                               # 1) EEPROM auto‑detect
        from inky.auto import auto
        dev = auto()
        return dev, *dev.resolution
    except Exception:
        pass

    # 2) manual overrides
    try:
        if INKY_TYPE == "spectra13":
            try:
                from inky.spectra13 import InkySpectra13    # ≥2.1
            except ModuleNotFoundError:
                from inky.spectra import InkySpectra13      # <2.1
            dev = InkySpectra13()
        elif INKY_TYPE == "phat":
            from inky.phat import InkyPHAT; dev = InkyPHAT(INKY_COLOUR or "red")
        elif INKY_TYPE == "what":
            from inky.what import InkyWHAT; dev = InkyWHAT(INKY_COLOUR or "red")
        else:
            raise ValueError("Unknown INKY_TYPE")
        return dev, *dev.resolution
    except Exception as e:
        print("Inky unavailable → headless mode:", e, file=sys.stderr)
        return None, *HEADLESS_RES

INKY, WIDTH, HEIGHT = init_inky()

# ────────────────── Helper → robust HTTP session / download ─────────────────
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(max_retries=RETRIES))
SESSION.mount("http://",  requests.adapters.HTTPAdapter(max_retries=RETRIES))

def download_file(url: str, dest: Path) -> Path:
    """Stream‑download `url` into `dest` unless it already exists."""
    if dest.exists():
        return dest
    with SESSION.get(url, stream=True, timeout=TIMEOUT) as r:
        r.raise_for_status()
        with dest.open("wb") as fp:
            for chunk in r.iter_content(8192):
                fp.write(chunk)
    return dest

# ─────────────────────────── Helper → aspect‑fit ────────────────────────────
def fit_image(img: Image.Image,
              upscale: bool = True,
              bg: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """Letter‑box the image to exactly (WIDTH, HEIGHT) preserving aspect."""
    try:
        img = img.convert("RGB")
        scale = min(WIDTH / img.width, HEIGHT / img.height)
        if scale < 1 or (scale > 1 and upscale):
            img = img.resize((round(img.width*scale), round(img.height*scale)),
                             Image.LANCZOS)
        canvas = Image.new("RGB", (WIDTH, HEIGHT), bg)
        canvas.paste(img, ((WIDTH - img.width)//2, (HEIGHT - img.height)//2))
        return canvas
    except Exception as e:
        print("fit_image failed:", e, file=sys.stderr)
        return Image.new("RGB", (WIDTH, HEIGHT), bg)

# ─────────────────────────────── Core logic ─────────────────────────────────
def fetch_random_xkcd() -> Path:
    html = SESSION.get(REMOTE_URL, timeout=TIMEOUT).text
    img_tag = BeautifulSoup(html, "html.parser").select_one("div#comic img")
    if not img_tag:
        raise RuntimeError("XKCD page contained no <img>")
    img_url = urljoin(REMOTE_URL, img_tag["src"])
    name = os.path.basename(urlparse(img_url).path) or "comic.png"
    if not name.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
        name += ".png"
    return download_file(img_url, SAVE_DIR / name)

def display(path: Path):
    """Render to Inky or write side‑by‑side preview PNG in headless mode."""
    try:
        with Image.open(path) as raw:
            frame = fit_image(raw)
            if INKY:
                INKY.set_image(frame)
                INKY.show()
            else:
                preview = path.with_name(path.stem + "_preview.png")
                frame.save(preview)
                print("Headless preview →", preview)
    except (UnidentifiedImageError, OSError) as e:
        raise RuntimeError(f"Display failed: {e}") from None

# ────────────────────────────── Main entry ──────────────────────────────────
def main() -> None:
    try:
        pic = fetch_random_xkcd()
        display(pic)
        print("Saved →", pic)
    except Exception as err:
        print("ERROR:", err, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
