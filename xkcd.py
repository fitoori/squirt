#!/usr/bin/env python3
"""
xkcd.py — fetch a random XKCD comic and display it on an Inky e‑paper panel
(tested on 13.3″ Spectra‑6 Impression).

v2.0
• Orientation filter: huge vertical strips or cinema‑wide comics are cached
  but auto‑skipped (<9:16 or >3:1 on landscape panels; inverse on portrait).

• Cached comics that don’t meet the current orientation tolerance are skipped and not displayed. 
  They remain in the cache and can be shown later if the panel orientation (or thresholds) change. 
  Only displayed comics are added to seen.json.

• --landscape / --portrait flags override auto-detected panel orientation. 

unchanged from v1.9:
• seen.json: offline mode shows every cached comic once before repeats.
• atomic downloads, bounded cache, zero BeautifulSoup dependency.
• White matte remains default; use --black for dark letter‑boxing.

Folder layout
└─ static/
   └─ xkcd/          comics, previews and seen.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Set, Tuple
from urllib.parse import urljoin, urlparse

import certifi
import requests
from PIL import Image, ImageFile, UnidentifiedImageError

# ─── Tunables & constants ────────────────────────────────────────────────
REMOTE_URL = "https://c.xkcd.com/random/comic/"
ROOT_DIR = Path(__file__).with_name("static")
SAVE_DIR = ROOT_DIR / "xkcd"
SEEN_FILE = SAVE_DIR / "seen.json"

TIMEOUT = 10
RETRIES = 2
CACHE_MAX = 500
MAX_FETCH_ATTEMPTS = 10             # max fresh downloads tried per run

# Aspect‑ratio limits (landscape panel). In portrait they’re inverted.
MIN_RATIO = 9 / 16                  # 0.562 → anything narrower is “too tall”
MAX_RATIO = 3.0                     # anything wider than 3:1 skipped

INKY_TYPE = "el133uf1"
INKY_COLOUR: str | None = None
HEADLESS_RES: Tuple[int, int] = (1600, 1200)

SAVE_DIR.mkdir(parents=True, exist_ok=True)
ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = 40_000_000

# ─── Minimal pip helper ──────────────────────────────────────────────────
def _pip_install(*pkgs: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--user", "--break-system-packages", *pkgs],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

for mod, pkg in (("requests", "requests"), ("PIL", "pillow")):
    try:
        __import__(mod)
    except ModuleNotFoundError:
        print(f"Installing {pkg} …")
        _pip_install(pkg)

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
SESSION.headers.update({"User-Agent": "XKCDFetcher/2.0"})
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

# ─── Cache management ───────────────────────────────────────────────────
def prune_cache(limit: int = CACHE_MAX) -> None:
    if limit <= 0:
        return
    files = sorted(SAVE_DIR.glob("*"), key=lambda p: p.stat().st_mtime)
    keep = {p.name for p in files[-limit:]}
    for p in files[:-limit]:
        p.unlink(missing_ok=True)
    dropped = {n for n in SEEN if n not in keep}
    if dropped:
        SEEN.difference_update(dropped)
        save_seen(SEEN)

# ─── Aspect‑ratio helper ────────────────────────────────────────────────
def acceptable(w: int, h: int, panel_landscape: bool) -> bool:
    aspect = w / h
    if panel_landscape:
        return MIN_RATIO <= aspect <= MAX_RATIO
    # portrait panel → invert logic
    return 1 / MAX_RATIO <= aspect <= 1 / MIN_RATIO

# ─── Download helpers ────────────────────────────────────────────────────
def _download(url: str, dest: Path) -> Path:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with SESSION.get(url, stream=True, timeout=TIMEOUT, verify=certifi.where()) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(8192):
                fh.write(chunk)
    tmp.replace(dest)
    return dest

# ─── Core: online & offline fetchers ─────────────────────────────────────
IMG_RX = re.compile(r'<div id="comic">.*?<img[^>]+src="([^"]+)"', re.S)

def fetch_one_xkcd() -> Path:
    html = SESSION.get(REMOTE_URL, timeout=TIMEOUT).text
    m = IMG_RX.search(html)
    if not m:
        raise RuntimeError("No <img> tag found")
    src = urljoin(REMOTE_URL, m.group(1))
    fname = os.path.basename(urlparse(src).path) or "comic.png"
    if not fname.lower().endswith((".png", ".jpg", ".jpeg", ".gif")):
        fname += ".png"
    return _download(src, SAVE_DIR / fname)

def fetch_xkcd(panel_landscape: bool) -> Path:
    for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
        p = fetch_one_xkcd()
        try:
            with Image.open(p) as im:
                if acceptable(im.width, im.height, panel_landscape):
                    return p
        except Exception:
            pass  # corrupt download? keep and continue
        print(f"Skipped unsuitable orientation ({attempt}/{MAX_FETCH_ATTEMPTS}) → {p.name}",
              file=sys.stderr)
    raise RuntimeError("No suitable comic found after multiple attempts")

def random_cached(panel_landscape: bool) -> Path:
    imgs: List[Path] = [p for p in SAVE_DIR.iterdir()
                        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif")]
    if not imgs:
        raise RuntimeError("Cache empty")
    # Prefer unseen + acceptable
    random.shuffle(imgs)
    for p in imgs:
        if p.name in SEEN:
            continue
        try:
            with Image.open(p) as im:
                if acceptable(im.width, im.height, panel_landscape):
                    return p
        except Exception:
            continue
    # fallback: any acceptable, even if seen
    for p in imgs:
        try:
            with Image.open(p) as im:
                if acceptable(im.width, im.height, panel_landscape):
                    SEEN.discard(p.name)  # reset rotation cycle
                    return p
        except Exception:
            continue
    raise RuntimeError("No cached comic matches panel orientation")

# ─── Imaging & display ──────────────────────────────────────────────────
def fit_image(im: Image.Image, bg: Tuple[int, int, int]) -> Image.Image:
    im = im.convert("RGB")
    scale = min(WIDTH / im.width, HEIGHT / im.height)
    if scale != 1:
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), bg)
    canvas.paste(im, ((WIDTH - im.width) // 2, (HEIGHT - im.height) // 2))
    return canvas

def display(path: Path, bg: Tuple[int, int, int]) -> None:
    with Image.open(path) as raw:
        frame = fit_image(raw, bg)
        if INKY:
            INKY.set_image(frame)
            INKY.show()
        else:
            prev = path.with_name(path.stem + "_preview.png")
            frame.save(prev)
            print("Preview →", prev)

# ─── CLI ────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch / display a random XKCD comic")
    matte = p.add_mutually_exclusive_group()
    matte.add_argument("--white", action="store_true", help="white matte (default)")
    matte.add_argument("--black", action="store_true", help="black matte")
    orient = p.add_mutually_exclusive_group()
    orient.add_argument("--landscape", action="store_true", help="force landscape panel")
    orient.add_argument("--portrait", action="store_true", help="force portrait panel")
    return p.parse_args()

# ─── Main ───────────────────────────────────────────────────────────────
def main() -> None:
    args = parse_args()
    bg_colour = (0, 0, 0) if args.black else (255, 255, 255)
    panel_landscape = args.landscape or (not args.portrait and WIDTH >= HEIGHT)

    try:
        comic = fetch_xkcd(panel_landscape)
        src = "online"
    except Exception as e:
        print("WARNING:", e, file=sys.stderr)
        try:
            comic = random_cached(panel_landscape)
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
