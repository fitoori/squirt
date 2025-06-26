#!/usr/bin/env python3
"""
xkcd.py – download a random xkcd and show it on an Inky display.

• Handles Spectra-13 boards with/without EEPROM, older/newer inky packages.
• Auto-installs inky (and numpy) under the *current* Python interpreter.
• Works whether you use the system interpreter or a venv.
• Uses the reliable https://c.xkcd.com/random/comic/ source.
"""

import os, sys, subprocess, requests
from pathlib import Path
from importlib import import_module
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError   # ← the missing import

# ── CONFIG ────────────────────────────────────────────────────────────────────
REMOTE_URL   = "https://c.xkcd.com/random/comic/"
SAVE_DIR     = Path(__file__).with_name("static")          # ./static/
TIMEOUT      = 10
# Board override (only needed when auto-detect fails and EEPROM is blank):
INKY_TYPE    = "spectra13"   # "spectra13" | "phat" | "what" | ""
INKY_COLOUR  = None          # For phat/what: "red", "black", "yellow"
HEADLESS_RES = (1600, 1200)  # use Spectra-13 resolution when headless
# ──────────────────────────────────────────────────────────────────────────────


# ── tiny helper: run pip with the *current* Python ────────────────────────────
def _pip_install(*packages: str) -> bool:
    cmd = [
        sys.executable, "-m", "pip", "install", "--quiet", "--user",
        "--break-system-packages"
    ]
    try:
        subprocess.run(cmd + list(packages), check=True)
        return True
    except subprocess.CalledProcessError as e:
        print("pip install failed:", e, file=sys.stderr)
        return False


# ── ensure dependencies (inky + numpy) are importable ────────────────────────
def _ensure_inky():
    try:
        import inky, numpy  # noqa: F401
        return True
    except ModuleNotFoundError:
        print("Inky/numpy missing – attempting to install…")
        return _pip_install('inky>=2.1.0', 'numpy')


if not _ensure_inky():
    print("ERROR: could not install required packages; running headless.")


# ── initialise Inky board ────────────────────────────────────────────────────
def init_inky():
    """
    Detect via EEPROM, else fall back to board specified in INKY_TYPE.
    Returns (inky_or_None, width, height).
    """
    try:                                     # 1) auto detect
        from inky.auto import auto as auto_inky
        dev = auto_inky()
        return dev, *dev.resolution
    except Exception:
        pass

    # 2) manual path variants
    try:
        if INKY_TYPE == "spectra13":
            try:
                from inky.spectra13 import InkySpectra13      # new path
            except ModuleNotFoundError:
                from inky.spectra import InkySpectra13        # pre-2.1.0 path
            dev = InkySpectra13()
        elif INKY_TYPE == "phat":
            from inky.phat import InkyPHAT
            dev = InkyPHAT(INKY_COLOUR or "red")
        elif INKY_TYPE == "what":
            from inky.what import InkyWHAT
            dev = InkyWHAT(INKY_COLOUR or "red")
        else:
            raise ValueError("Unknown INKY_TYPE or auto-detect failed")
        return dev, *dev.resolution
    except Exception as e:
        print("Inky unavailable (running headless):", e, file=sys.stderr)
        return None, *HEADLESS_RES


inky, WIDTH, HEIGHT = init_inky()
SAVE_DIR.mkdir(exist_ok=True)


# ── util: resize with aspect-fit, optional upscaling, robust to errors ────────
def aspect_fit(img: Image.Image, *, upscale: bool = True,
               bg_color: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """
    Return `img` resized to fill as much of the display as possible
    while preserving aspect ratio.

    Parameters
    ----------
    upscale : bool  – allow enlarging small images when True.
    bg_color        – RGB tuple for the letter-box background.

    On any exception a blank canvas is returned so callers never crash.
    """
    try:
        if img.width == 0 or img.height == 0:
            raise ValueError("image has zero width/height")

        img = img.convert("RGB")

        scale = min(WIDTH / img.width, HEIGHT / img.height)
        if scale < 1 or (upscale and scale > 1):
            new_size = (round(img.width * scale), round(img.height * scale))
            img = img.resize(new_size, Image.LANCZOS)

        canvas = Image.new("RGB", (WIDTH, HEIGHT), bg_color)
        canvas.paste(
            img,
            ((WIDTH - img.width) // 2, (HEIGHT - img.height) // 2),
        )
        return canvas

    except Exception as err:
        print("aspect_fit failed:", err, file=sys.stderr)
        return Image.new("RGB", (WIDTH, HEIGHT), bg_color)


# ── core: download comic or reuse cached ─────────────────────────────────────
def download_comic() -> Path:
    html = requests.get(REMOTE_URL, timeout=TIMEOUT).text
    tag  = BeautifulSoup(html, "html.parser").select_one("div#comic img")
    if not tag:
        raise RuntimeError("Could not find <img> in xkcd page")

    img_url = urljoin(REMOTE_URL, tag["src"])
    fname   = os.path.basename(urlparse(img_url).path) or "latest.png"
    if not fname.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
        fname += ".png"

    target = SAVE_DIR / fname
    if not target.exists():
        with requests.get(img_url, stream=True, timeout=TIMEOUT) as r:
            r.raise_for_status()
            if "image" not in r.headers.get("Content-Type", ""):
                raise RuntimeError("URL did not return an image")
            with target.open("wb") as fp:
                for chunk in r.iter_content(8192):
                    fp.write(chunk)
    return target


# ── display (no-op when headless) ────────────────────────────────────────────
def display(path: Path):
    try:
        with Image.open(path) as raw:
            fitted = aspect_fit(raw)
            if inky is not None:
                inky.set_image(fitted)
                inky.show()
            else:
                # headless run → leave a preview side-by-side
                preview = path.with_name(path.stem + "_preview.png")
                fitted.save(preview)
                print("Saved preview →", preview)
    except (UnidentifiedImageError, OSError) as e:
        print("Display failed:", e, file=sys.stderr)


# ── main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        pic = download_comic()
        display(pic)
        print("Saved to", pic)
    except Exception as err:
        print("ERROR:", err, file=sys.stderr)
        sys.exit(1)
