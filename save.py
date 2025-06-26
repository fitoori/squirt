#!/usr/bin/env python3
"""
save.py — cycle through images in a folder *or* fetch one URL
and display the result on an Inky e‑paper panel (or save a preview).

Usage
-----
  ./save.py                           # show next image in static/saved/
  ./save.py https://example/pic.jpg   # download → static/saved/, show it
  ./save.py --folder /path/to/dir     # use a different folder
  ./save.py --reset                   # forget last pointer

Folders
-------
static/
└── saved/
    ├── last.txt      (remembers which file you saw last)
    └── *.jpg / *.png (images + *_preview.png when headless)

Exit codes: 0 success, 1 failure.
"""

from __future__ import annotations
import argparse, os, re, subprocess, sys, requests
from pathlib import Path
from urllib.parse import urlparse
from typing import List, Optional
from PIL import Image, UnidentifiedImageError

# ───────────────────────────── Configuration ──────────────────────────────
ROOT_DIR       = Path(__file__).with_name("static")
DEFAULT_DIR    = ROOT_DIR / "saved"           # user‑requested name
TIMEOUT        = 15
RETRIES        = 2
HEADLESS_RES   = (1600, 1200)

INKY_TYPE      = "el133uf1"                   # override if auto fails
INKY_COLOUR    = None                         # for PHAT/WHAT

VALID_EXT      = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff"}

# ─────────────────────────── Helper → pip install ──────────────────────────
def _pip_install(*pkgs: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--user",
         "--break-system-packages", *pkgs],
        check=False,
    )

# ─────────────────────────── Helper → Inky detect ──────────────────────────
def init_inky():
    try:
        import inky, numpy  # noqa
    except ModuleNotFoundError:
        print("Installing inky & numpy…")
        _pip_install("inky>=2.1.0", "numpy")
        try:
            import inky  # noqa
        except ModuleNotFoundError:
            pass

    # 1) EEPROM auto‑detect
    try:
        from inky.auto import auto
        dev = auto()
        return dev, *dev.resolution
    except Exception:
        pass

    # 2) Manual class map
    class_map = {
        "el133uf1":      "InkyEL133UF1",      # 13.3″ Spectra‑6
        "impression73":  "InkyImpression73",  # 7‑colour 7.3″
        "spectra13":     "InkyEL133UF1",
        "phat":          "InkyPHAT",
        "what":          "InkyWHAT",
    }
    key = INKY_TYPE.lower()
    if key not in class_map:
        print("No Inky board detected and INKY_TYPE is unset — headless mode.")
        return None, *HEADLESS_RES
    try:
        cls = getattr(__import__("inky", fromlist=[class_map[key]]),
                      class_map[key])
        dev = cls(INKY_COLOUR) if key in ("phat", "what") else cls()
        return dev, *dev.resolution
    except Exception as e:
        print("Inky unavailable → headless mode:", e, file=sys.stderr)
        return None, *HEADLESS_RES

INKY, WIDTH, HEIGHT = init_inky()

# ─────────────────── Helper → HTTP session / download ─────────────────────
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(max_retries=RETRIES))
SESSION.mount("http://",  requests.adapters.HTTPAdapter(max_retries=RETRIES))

def slug(text: str, n: int = 60) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text)[:n].strip("_").lower() or "image"

# ───────────────────── Folder helpers ──────────────────────────────────────
def list_images(folder: Path) -> List[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in VALID_EXT)

def next_image(folder: Path, pointer: Path) -> Optional[Path]:
    imgs = list_images(folder)
    if not imgs:
        return None
    if pointer.exists():
        try:
            last = Path(pointer.read_text().strip())
            idx  = imgs.index(last)
            nxt  = imgs[(idx + 1) % len(imgs)]
        except ValueError:
            nxt = imgs[0]
    else:
        nxt = imgs[0]
    pointer.write_text(str(nxt))
    return nxt

def save_url(url: str, folder: Path, pointer: Path) -> Path:
    parsed = urlparse(url)
    stem   = slug(parsed.path.split("/")[-1].split(".")[0])
    ext    = os.path.splitext(parsed.path)[1] or ".jpg"
    target = folder / f"{stem}{ext}"
    if not target.exists():
        r = SESSION.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        target.write_bytes(r.content)
    pointer.write_text(str(target))
    return target

# ───────────────────── Image fit helper (cover) ────────────────────────────
def fit_image_cover(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    scale = max(WIDTH / img.width, HEIGHT / img.height)
    new = img.resize((round(img.width*scale), round(img.height*scale)),
                     Image.LANCZOS)
    l = (new.width - WIDTH)//2; t = (new.height - HEIGHT)//2
    return new.crop((l, t, l+WIDTH, t+HEIGHT))

# ───────────────────────── display helper ─────────────────────────────────
def display(path: Path):
    try:
        with Image.open(path) as raw:
            frame = fit_image_cover(raw)
            if INKY:
                INKY.set_image(frame)
                INKY.show()
            else:
                preview = path.with_name(path.stem + "_preview.png")
                frame.save(preview)
                print("Headless preview →", preview)
    except (UnidentifiedImageError, OSError) as e:
        raise RuntimeError(f"Display failed: {e}") from None

# ───────────────────────────── CLI parsing ────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Cycle or fetch images for Inky.")
    p.add_argument("url", nargs="?", help="optional URL to fetch & display")
    p.add_argument("--folder", type=Path, help="override image folder")
    p.add_argument("--reset", action="store_true", help="forget last pointer")
    return p.parse_args()

# ─────────────────────────────── Main ─────────────────────────────────────
def main():
    args = parse_args()

    folder = (args.folder or DEFAULT_DIR).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    pointer = folder / "last.txt"

    if args.reset:
        pointer.unlink(missing_ok=True)
        print("pointer reset")
        return

    try:
        if args.url:
            path = save_url(args.url, folder, pointer)
            print("Fetched →", path)
        else:
            path = next_image(folder, pointer)
            if not path:
                sys.exit(f"No images found in {folder}")
        display(path)
        print("Displayed", path)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
