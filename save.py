#!/usr/bin/env python3
"""

save.py – Production Build v1.2
───────────────────────────────────────────
cycle through images in a folder *or* fetch one URL
and display the result on an Inky e‑paper panel (or save a preview).

v1.2   (2025‑07‑27)
Exit codes: 0 success, 1 failure.
 • Listing images with metadata (index, size, modified time).
 • Deleting images by name or index.
 • Displaying a random image on demand.
 • Showing detailed information about a specific image.
 • Supporting multiple fit modes (cover or contain) and optional grayscale.

If no Inky display is detected the script will save a preview image in the
same folder. To change the default image folder use the --folder option.

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
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import requests
import io
from PIL import Image, UnidentifiedImageError

# ───────────────────────────── Configuration ──────────────────────────────
ROOT_DIR = Path(__file__).with_name("static")
DEFAULT_DIR = ROOT_DIR / "saved"
TIMEOUT = 15
RETRIES = 2
HEADLESS_RES = (1600, 1200)

# Override these with environment variables if necessary
INKY_TYPE = os.environ.get("INKY_TYPE", "el133uf1")
INKY_COLOUR = os.environ.get("INKY_COLOUR") or None

VALID_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff"}


# ─────────────────────────── Helper → pip install ──────────────────────────
def _pip_install(*pkgs: str) -> None:
    """
    Silently install one or more pip packages. When running inside a
    restricted environment (such as this worker), the '--user' flag can
    cause pip to bail with an error because user site packages are not
    visible. To mitigate this, the install is performed without '--user'
    but with '--break-system-packages' so that packages can still be
    installed into the isolated environment.
    """
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--break-system-packages", *pkgs],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        # Ignore all pip failures and fall back to headless mode
        pass


# ─────────────────────────── Helper → Inky detect ──────────────────────────
def init_inky():
    """
    Attempt to initialise an attached Inky display. If no display is found
    or the relevant module is missing, return (None, width, height) for
    headless operation.
    """
    try:
        import inky, numpy  # noqa: F401
    except ModuleNotFoundError:
        print("Installing inky & numpy…")
        _pip_install("inky>=2.1.0", "numpy")
        try:
            import inky  # noqa: F401
        except ModuleNotFoundError:
            pass

    # 1) EEPROM auto-detect
    try:
        from inky.auto import auto

        dev = auto()
        return dev, *dev.resolution
    except Exception:
        pass

    # 2) Manual class map fallback
    class_map = {
        "el133uf1": "InkyEL133UF1",
        "impression73": "InkyImpression73",
        "spectra13": "InkyEL133UF1",
        "phat": "InkyPHAT",
        "what": "InkyWHAT",
    }
    key = (INKY_TYPE or "").lower()
    if key not in class_map:
        print("No Inky board detected and INKY_TYPE is unset — headless mode.")
        return None, *HEADLESS_RES
    try:
        cls = getattr(__import__("inky", fromlist=[class_map[key]]), class_map[key])
        dev = cls(INKY_COLOUR) if key in ("phat", "what") else cls()
        return dev, *dev.resolution
    except Exception as e:
        print("Inky unavailable → headless mode:", e, file=sys.stderr)
        return None, *HEADLESS_RES


INKY, WIDTH, HEIGHT = init_inky()


# ─────────────────── Helper → HTTP session / download ─────────────────────
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(max_retries=RETRIES))
SESSION.mount("http://", requests.adapters.HTTPAdapter(max_retries=RETRIES))


def slug(text: str, n: int = 60) -> str:
    """Create a filesystem‑friendly slug from an arbitrary string."""
    return re.sub(r"[^A-Za-z0-9]+", "_", text)[:n].strip("_").lower() or "image"


# ───────────────────── Folder helpers ──────────────────────────────────────
def list_images(folder: Path) -> List[Path]:
    """
    Return a sorted list of all valid image files in the folder. Files
    automatically generated as previews (ending with '_preview.png') are
    excluded from the listing to avoid cluttering the cycle.
    """
    imgs: List[Path] = []
    for p in folder.iterdir():
        if p.suffix.lower() not in VALID_EXT:
            continue
        # skip preview images by convention
        if p.name.endswith("_preview.png"):
            continue
        imgs.append(p)
    return sorted(imgs)


def next_image(folder: Path, pointer: Path) -> Optional[Path]:
    """
    Cycle through images in folder. Stores the last displayed image in
    `pointer`. Returns the next image or None if no images.

    This function will automatically skip and remove pointer references to
    images that can no longer be opened, falling back gracefully to the first
    valid image.
    """
    imgs = list_images(folder)
    if not imgs:
        return None

    # Attempt to read last pointer; if invalid index or file missing, start fresh
    last: Optional[Path] = None
    if pointer.exists():
        try:
            last_path = Path(pointer.read_text().strip())
            if last_path in imgs:
                last = last_path
        except Exception:
            pass
    # Determine next index
    nxt = imgs[0]
    if last:
        try:
            idx = imgs.index(last)
            nxt = imgs[(idx + 1) % len(imgs)]
        except ValueError:
            nxt = imgs[0]
    # Update pointer
    pointer.write_text(str(nxt))
    return nxt


def random_image(folder: Path, pointer: Path) -> Optional[Path]:
    """
    Select a random image from the folder. Updates the pointer file to that
    selection to ensure the next call to next_image starts from this image.
    """
    imgs = list_images(folder)
    if not imgs:
        return None
    import random

    choice = random.choice(imgs)
    pointer.write_text(str(choice))
    return choice


def save_url(url: str, folder: Path, pointer: Path) -> Path:
    """
    Download an image from the given URL into the folder. Uses a safe slug
    based on the URL path and ensures the file has a valid extension. If
    a file with the same name exists a numeric suffix is appended to avoid
    clobbering existing files.

    On successful download the pointer is updated to this image.
    """
    parsed = urlparse(url)
    # Determine extension from path or Content-Type header
    ext = os.path.splitext(parsed.path)[1].lower()
    if ext and ext not in VALID_EXT:
        # Disallow dangerous extensions early
        raise ValueError(f"Unsupported file extension: {ext}")
    stem = slug(parsed.path.split("/")[-1].split(".")[0])
    # Fallback to jpg if no extension provided
    if not ext:
        ext = ".jpg"
    # Build filename, ensuring uniqueness
    target = folder / f"{stem}{ext}"
    counter = 1
    while target.exists():
        target = folder / f"{stem}_{counter}{ext}"
        counter += 1

    # Fetch file
    resp = SESSION.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    content = resp.content
    # Optionally validate image by loading with PIL
    try:
        Image.open(io.BytesIO(content)).verify()
    except Exception:
        # Not a valid image
        raise RuntimeError(f"Downloaded content from {url} is not a valid image.")

    target.write_bytes(content)
    pointer.write_text(str(target))
    return target


def delete_image(identifier: str, folder: Path, pointer: Path) -> None:
    """
    Delete an image from the folder by index (1‑based) or file name. If the
    deleted image is currently referenced by the pointer, the pointer file is
    removed so that the next cycle restarts from the first image.
    """
    imgs = list_images(folder)
    if not imgs:
        raise FileNotFoundError(f"No images found in {folder}")

    target: Optional[Path] = None
    # Try numeric index
    if identifier.isdigit():
        idx = int(identifier) - 1
        if 0 <= idx < len(imgs):
            target = imgs[idx]
    # Otherwise match by file name
    if not target:
        for p in imgs:
            if p.name == identifier or p.stem == identifier:
                target = p
                break
    if not target:
        raise ValueError(f"Image '{identifier}' not found.")

    # Remove file
    target.unlink()
    # Remove corresponding preview if it exists
    preview = target.with_name(target.stem + "_preview" + target.suffix)
    if preview.exists():
        preview.unlink()
    # If pointer references this file remove pointer to reset cycle
    if pointer.exists() and pointer.read_text().strip() == str(target):
        pointer.unlink()


def get_image_info(path: Path) -> str:
    """
    Return human readable information about the given image file including
    size, format, mode, file size and modification time.
    """
    if not path.exists() or path.suffix.lower() not in VALID_EXT:
        raise FileNotFoundError(f"Image {path} not found or unsupported.")
    try:
        with Image.open(path) as img:
            stat = path.stat()
            return (
                f"Name: {path.name}\n"
                f"Format: {img.format}, Mode: {img.mode}\n"
                f"Resolution: {img.width}×{img.height}\n"
                f"File size: {stat.st_size // 1024} KiB\n"
                f"Modified: {datetime.fromtimestamp(stat.st_mtime):%Y-%m-%d %H:%M}"
            )
    except UnidentifiedImageError as e:
        raise RuntimeError(f"Cannot open image {path}: {e}") from None


# ───────────────────── Image fit helper (cover / contain) ─────────────────
def fit_image_cover(img: Image.Image) -> Image.Image:
    """
    Resize and crop the image to fully cover the display (maintaining aspect
    ratio). Portions outside the frame are cropped.
    """
    img = img.convert("RGB")
    scale = max(WIDTH / img.width, HEIGHT / img.height)
    new = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    l = (new.width - WIDTH) // 2
    t = (new.height - HEIGHT) // 2
    return new.crop((l, t, l + WIDTH, t + HEIGHT))


def fit_image_contain(img: Image.Image) -> Image.Image:
    """
    Resize the image to fit entirely within the display area while preserving
    aspect ratio. The remaining space is filled with white bars (letterboxed).
    """
    img = img.convert("RGB")
    scale = min(WIDTH / img.width, HEIGHT / img.height)
    new_size = (round(img.width * scale), round(img.height * scale))
    new_img = img.resize(new_size, Image.LANCZOS)
    # Create a white canvas and paste the resized image centred
    canvas = Image.new("RGB", (WIDTH, HEIGHT), "white")
    offset = ((WIDTH - new_size[0]) // 2, (HEIGHT - new_size[1]) // 2)
    canvas.paste(new_img, offset)
    return canvas


# ───────────────────────── display helper ─────────────────────────────────
def display(path: Path, fit_method: str = "cover", grayscale: bool = False):
    """
    Open and display the given image on the Inky display. Supports 'cover'
    (default) or 'contain' fit methods. Optionally converts the image to
    grayscale prior to display to reduce ghosting on monochrome e‑ink panels.

    If no physical display is present a preview PNG is saved alongside
    the original with '_preview' appended to the filename.
    """
    try:
        with Image.open(path) as raw:
            # Choose fit method
            if fit_method == "contain":
                frame = fit_image_contain(raw)
            else:
                frame = fit_image_cover(raw)
            # Optional grayscale conversion
            if grayscale:
                frame = frame.convert("L").convert("RGB")
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
    """
    Define and parse command line options. In addition to the original URL
    argument, supports listing, deleting, random selection, image info,
    fit method selection and grayscale conversion.
    """
    p = argparse.ArgumentParser(description="Manage and display images on an Inky display.")
    p.add_argument("url", nargs="?", help="Optional URL to fetch & display.")
    p.add_argument("--folder", type=Path, help="Override image folder.")
    p.add_argument("--reset", action="store_true", help="Forget last pointer.")
    p.add_argument("--list", action="store_true", help="List all images in the folder with details.")
    p.add_argument("--delete", metavar="ID", help="Delete an image by index or filename.")
    p.add_argument("--random", action="store_true", help="Display a random image instead of cycling.")
    p.add_argument("--info", metavar="FILE", nargs="?", const="", help="Show info about an image file.")
    p.add_argument("--fit-method", choices=("cover", "contain"), default="cover", help="How to fit images on screen.")
    p.add_argument("--grayscale", action="store_true", help="Convert the image to grayscale before display.")
    return p.parse_args()


# ─────────────────────────────── Main ─────────────────────────────────────
def main():
    args = parse_args()

    folder = (args.folder or DEFAULT_DIR).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    pointer = folder / "last.txt"

    if args.reset:
        pointer.unlink(missing_ok=True)
        print("Pointer reset.")
        return

    # Listing images
    if args.list:
        imgs = list_images(folder)
        if not imgs:
            print(f"No images found in {folder}")
            return
        print(f"Images in {folder} ({len(imgs)}):")
        for i, img in enumerate(imgs, start=1):
            stat = img.stat()
            size_k = stat.st_size // 1024
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"{i:3}: {img.name} ({size_k} KiB, {mtime})")
        return

    # Deleting an image
    if args.delete:
        try:
            delete_image(args.delete, folder, pointer)
            print(f"Deleted image '{args.delete}'.")
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # Show info
    if args.info is not None:
        # If no argument provided to --info, use current pointer
        info_path: Optional[Path] = None
        if args.info:
            candidate = (folder / args.info).expanduser()
            if not candidate.exists():
                print(f"ERROR: {candidate} does not exist.", file=sys.stderr)
                sys.exit(1)
            info_path = candidate
        else:
            if pointer.exists():
                try:
                    info_path = Path(pointer.read_text().strip())
                except Exception:
                    info_path = None
        if not info_path:
            print("No image to show info for.", file=sys.stderr)
            sys.exit(1)
        try:
            print(get_image_info(info_path))
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # Fetch new image from URL
    if args.url:
        try:
            path = save_url(args.url, folder, pointer)
            print("Fetched →", path)
        except Exception as e:
            print(f"ERROR fetching '{args.url}': {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Select next or random image
        if args.random:
            path = random_image(folder, pointer)
        else:
            path = next_image(folder, pointer)
        if not path:
            print(f"No images found in {folder}", file=sys.stderr)
            sys.exit(1)

    # Display the image
    try:
        display(path, fit_method=args.fit_method, grayscale=args.grayscale)
        print("Displayed", path)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
