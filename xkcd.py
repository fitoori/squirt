#!/usr/bin/env python3
"""
xkcd.py — fetch a random XKCD comic and display it on an Inky panel
(13.3″ Spectra‑6, other Impressions, pHAT, wHAT …) or save a PNG preview
when no hardware is present.

v1.1
• On failure (no network, bad download, etc.): displays a previously cached
  comic picked at random.

Folders
-------
static/
└── xkcd/                ← comics and optional previews

"""

from __future__ import annotations
import os, sys, subprocess, requests, random
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError, ImageFile

# ───────────────────────── Configuration ──────────────────────────
REMOTE_URL   = "https://c.xkcd.com/random/comic/"
ROOT_DIR     = Path(__file__).with_name("static")
SAVE_DIR     = ROOT_DIR / "xkcd"
TIMEOUT      = 10
RETRIES      = 2
# Manual override when auto-detect fails and EEPROM is blank:
INKY_TYPE    = "el133uf1"          # "el133uf1" | "phat" | "what" | ""
INKY_COLOUR  = None                # for pHAT / wHAT
HEADLESS_RES = (1600, 1200)        # matches 13.3″ panel
# ──────────────────────────────────────────────────────────────────

SAVE_DIR.mkdir(parents=True, exist_ok=True)
ImageFile.LOAD_TRUNCATED_IMAGES = True          # tolerate partial files

# — pip helper —
def _pip_install(*pkgs: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--user",
         "--break-system-packages", *pkgs],
        check=False,
    )

# — Inky initialisation —
def init_inky():
    """
    Returns (dev_or_None, WIDTH, HEIGHT).
    Uses inky.auto when possible, else falls back to a manual class map.
    """
    try:
        import inky, numpy  # noqa
    except ModuleNotFoundError:
        print("Installing inky & numpy …")
        _pip_install("inky>=2.1.0", "numpy")
        try:
            import inky  # noqa
        except ModuleNotFoundError:
            pass

    # 1) EEPROM-driven auto-detect (works on modern HATs)
    try:
        from inky.auto import auto
        dev = auto()
        return dev, *dev.resolution
    except Exception:
        pass

    # 2) Manual class map for older / EEPROM-less boards
    class_map = {
        "el133uf1":  "InkyEL133UF1",   # 13.3″ Spectra-6 Impression
        "spectra13":"InkyEL133UF1",    # synonym for legacy scripts
        "impression13":"InkyEL133UF1",
        "phat":      "InkyPHAT",
        "what":      "InkyWHAT",
    }
    key = INKY_TYPE.lower()
    if key not in class_map:
        print("No Inky board detected and INKY_TYPE is unset — headless mode.")
        return None, *HEADLESS_RES

    try:
        board_cls = getattr(__import__("inky", fromlist=[class_map[key]]),
                            class_map[key])
        dev = board_cls(INKY_COLOUR) if key in ("phat", "what") else board_cls()
        return dev, *dev.resolution
    except Exception as e:
        print("Inky unavailable → headless mode:", e, file=sys.stderr)
        return None, *HEADLESS_RES

INKY, WIDTH, HEIGHT = init_inky()

# — HTTP helpers —
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(max_retries=RETRIES))
SESSION.mount("http://",  requests.adapters.HTTPAdapter(max_retries=RETRIES))

def download(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    with SESSION.get(url, stream=True, timeout=TIMEOUT) as r:
        r.raise_for_status()
        with dest.open("wb") as fp:
            for chunk in r.iter_content(8192):
                fp.write(chunk)
    return dest

# — Image helper (letter-box fit) —
def fit_image(img: Image.Image,
              upscale: bool = True,
              bg=(255, 255, 255)) -> Image.Image:
    img = img.convert("RGB")
    scale = min(WIDTH / img.width, HEIGHT / img.height)
    if scale < 1 or (scale > 1 and upscale):
        img = img.resize((round(img.width*scale), round(img.height*scale)),
                         Image.LANCZOS)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), bg)
    canvas.paste(img, ((WIDTH - img.width)//2, (HEIGHT - img.height)//2))
    return canvas

# — Core —
def fetch_xkcd() -> Path:
    """Download a fresh random XKCD and return its cached Path."""
    try:
        html = SESSION.get(REMOTE_URL, timeout=TIMEOUT).text
        tag  = BeautifulSoup(html, "html.parser").select_one("div#comic img")
        if not tag or not tag.get("src"):
            raise RuntimeError("XKCD page contained no <img>")
        img_url = urljoin(REMOTE_URL, tag["src"])
        fname   = os.path.basename(urlparse(img_url).path) or "comic.png"
        if not fname.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
            fname += ".png"
        return download(img_url, SAVE_DIR / fname)
    except (requests.RequestException, RuntimeError) as exc:
        raise RuntimeError(f"online fetch failed: {exc}") from exc

def random_cached_comic() -> Path:
    """Return a random image from SAVE_DIR (raises if none found)."""
    files = [p for p in SAVE_DIR.iterdir()
             if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif")]
    if not files:
        raise RuntimeError("No cached comics available for offline fallback")
    return random.choice(files)

def display(path: Path):
    with Image.open(path) as raw:
        frame = fit_image(raw)
        if INKY:
            INKY.set_image(frame)
            INKY.show()
        else:
            preview = path.with_name(path.stem + "_preview.png")
            frame.save(preview)
            print("Headless preview →", preview)

# — Main —
def main():
    try:
        comic = fetch_xkcd()
        source = "online"
    except Exception as err:
        print(f"WARNING: {err}", file=sys.stderr)
        try:
            comic = random_cached_comic()
            source = "offline cache"
        except Exception as inner:
            print("ERROR:", inner, file=sys.stderr)
            sys.exit(1)

    try:
        display(comic)
        print(f"Displayed ({source}) → {comic}")
    except (UnidentifiedImageError, OSError) as disp_err:
        print("ERROR: display failed:", disp_err, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
