#!/usr/bin/env python3
"""
xkcd.py — fetch a random XKCD comic and display it on an Inky e‑paper panel
(tested on 13.3″ Spectra‑6 Impression).

v1.9
• Adds seen.json: offline mode shows every cached comic once before repeats.
• Keeps atomic downloads, bounded cache, zero BeautifulSoup dependency.

Folder layout
└─ static/
   └─ xkcd/          ← comics, previews, seen.json
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Set, Tuple
from urllib.parse import urljoin, urlparse

import certifi
import requests
from PIL import Image, ImageFile, UnidentifiedImageError

# ─── Config ──────────────────────────────────────────────────────────────
REMOTE_URL = "https://c.xkcd.com/random/comic/"
ROOT_DIR = Path(__file__).with_name("static")
SAVE_DIR = ROOT_DIR / "xkcd"
SEEN_FILE = SAVE_DIR / "seen.json"
CACHE_MAX = 500                     # 0 → unlimited
TIMEOUT, RETRIES = 10, 2

INKY_TYPE = "el133uf1"
INKY_COLOUR: str | None = None
HEADLESS_RES: Tuple[int, int] = (1600, 1200)

SAVE_DIR.mkdir(parents=True, exist_ok=True)
ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = 40_000_000  # ~4 k × 10 k

# ─── Lightweight pip helper ──────────────────────────────────────────────
def _pip_install(*pkgs: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--user", "--break-system-packages", *pkgs],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

for _mod, _pkg in (("requests", "requests"), ("PIL", "pillow")):
    if _mod not in sys.modules:
        try:
            __import__(_mod)
        except ModuleNotFoundError:
            print(f"Installing {_pkg} …")
            _pip_install(_pkg)

# ─── Inky initialisation ────────────────────────────────────────────────
def init_inky():
    try:
        import inky, numpy  # noqa: F401
    except ModuleNotFoundError:
        print("Installing inky + numpy …")
        _pip_install("inky>=2.1.0", "numpy")

    try:
        from inky.auto import auto
        dev = auto()
        return dev, *dev.resolution
    except Exception:
        pass

    cls_map = {
        "el133uf1": "InkyEL133UF1",
        "spectra13": "InkyEL133UF1",
        "impression13": "InkyEL133UF1",
        "phat": "InkyPHAT",
        "what": "InkyWHAT",
    }
    key = INKY_TYPE.lower()
    if key not in cls_map:
        print("Headless mode (no Inky detected).")
        return None, *HEADLESS_RES
    try:
        cls = getattr(__import__("inky", fromlist=[cls_map[key]]), cls_map[key])
        dev = cls(INKY_COLOUR) if key in ("phat", "what") else cls()
        return dev, *dev.resolution
    except Exception as err:
        print("Inky init failed:", err, file=sys.stderr)
        return None, *HEADLESS_RES


INKY, WIDTH, HEIGHT = init_inky()

# ─── HTTP session ────────────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "XKCDFetcher/1.9"})
adapter = requests.adapters.HTTPAdapter(max_retries=RETRIES)
SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)

# ─── Seen bookkeeping ────────────────────────────────────────────────────
def load_seen() -> Set[str]:
    try:
        data = json.loads(SEEN_FILE.read_text())
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def save_seen(seen: Set[str]) -> None:
    try:
        SEEN_FILE.write_text(json.dumps(sorted(seen)))
    except Exception as e:
        print("WARN: could not write seen.json:", e, file=sys.stderr)


SEEN: Set[str] = load_seen()

# ─── Cache utilities ─────────────────────────────────────────────────────
def prune_cache(limit: int = CACHE_MAX) -> None:
    """Delete oldest files beyond `limit` and purge dangling entries in SEEN."""
    if limit <= 0:
        return
    files = sorted(SAVE_DIR.glob("*"), key=lambda p: p.stat().st_mtime)
    keep_names = {p.name for p in files[-limit:]}
    # remove excess
    for p in files[:-limit]:
        p.unlink(missing_ok=True)
    # clean SEEN
    removed = {name for name in SEEN if name not in keep_names}
    if removed:
        SEEN.difference_update(removed)
        save_seen(SEEN)

# ─── Download helpers ────────────────────────────────────────────────────
def _download(url: str, dest: Path) -> Path:
    part = dest.with_suffix(dest.suffix + ".part")
    with SESSION.get(url, stream=True, timeout=TIMEOUT, verify=certifi.where()) as r:
        r.raise_for_status()
        with part.open("wb") as fh:
            for chunk in r.iter_content(8192):
                fh.write(chunk)
    part.replace(dest)
    return dest

# ─── Core functions ──────────────────────────────────────────────────────
IMG_RX = re.compile(r'<div id="comic">.*?<img[^>]+src="([^"]+)"', re.S)

def fetch_xkcd() -> Path:
    html = SESSION.get(REMOTE_URL, timeout=TIMEOUT).text
    m = IMG_RX.search(html)
    if not m:
        raise RuntimeError("No <img> tag found")
    src = urljoin(REMOTE_URL, m.group(1))
    fname = os.path.basename(urlparse(src).path) or "comic.png"
    if not fname.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
        fname += ".png"
    return _download(src, SAVE_DIR / fname)

def random_cached() -> Path:
    imgs: List[Path] = [
        p for p in SAVE_DIR.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif")
    ]
    if not imgs:
        raise RuntimeError("Cache empty")
    unseen = [p for p in imgs if p.name not in SEEN]
    pool = unseen or imgs  # reset if all seen
    if not unseen:
        SEEN.clear()
    return random.choice(pool)

# ─── Imaging helpers ─────────────────────────────────────────────────────
def fit_image(im: Image.Image, bg: Tuple[int, int, int]) -> Image.Image:
    im = im.convert("RGB")
    scale = min(WIDTH / im.width, HEIGHT / im.height)
    if scale != 1:
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), bg)
    canvas.paste(im, ((WIDTH - im.width) // 2, (HEIGHT - im.height) // 2))
    return canvas

def display(path: Path, matte_bg: Tuple[int, int, int]) -> None:
    with Image.open(path) as raw:
        frame = fit_image(raw, matte_bg)
        if INKY:
            INKY.set_image(frame)
            INKY.show()
        else:
            prev = path.with_name(path.stem + "_preview.png")
            frame.save(prev)
            print("Preview →", prev)

# ─── CLI ────────────────────────────────────────────────────────────────
def parse_args():
    import argparse

    p = argparse.ArgumentParser(description="Fetch / display a random XKCD comic")
    col = p.add_mutually_exclusive_group()
    col.add_argument("--white", action="store_true", help="white matte (default)")
    col.add_argument("--black", action="store_true", help="black matte")
    return p.parse_args()

# ─── Main ────────────────────────────────────────────────────────────────
def main() -> None:
    args = parse_args()
    bg_colour = (0, 0, 0) if args.black else (255, 255, 255)

    try:
        comic = fetch_xkcd()
        src = "online"
    except Exception as e:
        print("WARNING:", e, file=sys.stderr)
        try:
            comic = random_cached()
            src = "offline cache"
        except Exception as e2:
            print("ERROR:", e2, file=sys.stderr)
            sys.exit(1)

    try:
        display(comic, bg_colour)
        SEEN.add(comic.name)
        save_seen(SEEN)
        prune_cache()
        print(f"Displayed ({src}) → {comic}")
    except (UnidentifiedImageError, OSError) as e:
        print("ERROR: display failed:", e, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
