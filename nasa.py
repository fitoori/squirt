#!/usr/bin/env python3
"""
nasa.py — fetch a single NASA image and display it on an Inky panel
(or write a PNG preview when no board is connected).

Sources (choose one):
  --apod                    random Astronomy Picture of the Day  [default]
  --mars [ROVER]            latest Mars‑rover photo
  --epic                    latest DSCOVR EPIC Earth disk image
  --earth LAT LON [--dim]   Landsat/MODIS tile (dim° wide, default 0.15)
  --search "QUERY"          first hit from NASA Image & Video Library

General:
  --key API_KEY             override NASA_API_KEY env var / DEMO_KEY
  --dim KM                  width/height in degrees for --earth
  --batch 10                pick best of 10 random APODs (default 4:3)
  --batch 5 --portrait      best of 5, aiming for 3:4
Folders
-------
static/
└── nasa/   ← all downloaded images and optional *_preview.png
Note: those within ± 3 % of the requested ratio are additionally copied into static/nasa/4_3/ or static/nasa/3_4/.
Exit codes: 0 success, 1 failure.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, UnidentifiedImageError

# ─── Config ───────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).with_name("static")
SAVE_DIR = ROOT_DIR / "nasa"
SAVE_DIR.mkdir(parents=True, exist_ok=True)
DIR_4_3 = SAVE_DIR / "4_3"
DIR_3_4 = SAVE_DIR / "3_4"
for d in (DIR_4_3, DIR_3_4):
    d.mkdir(exist_ok=True)

TIMEOUT = 15
RETRIES = 2
HEADLESS_RES = (1600, 1200)

INKY_TYPE = "el133uf1"
INKY_COLOUR: str | None = None

NASA_KEY = os.getenv("NASA_API_KEY", "DEMO_KEY").strip()

# ─── Silent pip helper ────────────────────────────────────────────────────
def _pip_install(*pkgs: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--user", "--break-system-packages", *pkgs],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ─── Inky detect ──────────────────────────────────────────────────────────
def init_inky():
    try:
        import inky  # noqa: F401
        import numpy  # noqa: F401
    except ModuleNotFoundError:
        print("Installing inky + numpy …")
        _pip_install("inky>=2.1.0", "numpy")
        try:
            import inky  # noqa: F401
        except ModuleNotFoundError:
            pass

    try:
        from inky.auto import auto

        dev = auto()
        return dev, *dev.resolution
    except Exception:
        pass

    class_map = {
        "el133uf1": "InkyEL133UF1",
        "spectra13": "InkyEL133UF1",
        "impression13": "InkyEL133UF1",
        "phat": "InkyPHAT",
        "what": "InkyWHAT",
    }
    key = INKY_TYPE.lower()
    if key not in class_map:
        print("No Inky detected → headless mode.")
        return None, *HEADLESS_RES
    try:
        cls = getattr(__import__("inky", fromlist=[class_map[key]]), class_map[key])
        dev = cls(INKY_COLOUR) if key in ("phat", "what") else cls()
        return dev, *dev.resolution
    except Exception as exc:
        print("Inky unavailable:", exc, file=sys.stderr)
        return None, *HEADLESS_RES


INKY, WIDTH, HEIGHT = init_inky()

# ─── Robust HTTP session ──────────────────────────────────────────────────
SESSION = requests.Session()
ADAPTER = requests.adapters.HTTPAdapter(max_retries=RETRIES)
SESSION.mount("https://", ADAPTER)
SESSION.mount("http://", ADAPTER)

API_CALLS = 0


def _json(url: str, **params):
    global API_CALLS
    API_CALLS += 1
    try:
        r = SESSION.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        if r.status_code == 403:
            raise RuntimeError("NASA API key rejected or quota exceeded.") from None
        raise RuntimeError(f"HTTP error {r.status_code}: {e}") from None
    except requests.RequestException as e:
        raise RuntimeError(f"Network error: {e}") from None
    except json.JSONDecodeError:
        raise RuntimeError("Malformed JSON from NASA.") from None


def _download(url: str) -> Path:
    global API_CALLS
    fname = os.path.basename(urlparse(url).path) or "image.jpg"
    target = SAVE_DIR / fname
    if target.exists():
        return target
    API_CALLS += 1
    try:
        with SESSION.get(url, stream=True, timeout=TIMEOUT) as r:
            r.raise_for_status()
            with target.open("wb") as fp:
                for chunk in r.iter_content(8192):
                    fp.write(chunk)
    except requests.RequestException as e:
        raise RuntimeError(f"Download failed: {e}") from None
    return target


# ─── Aspect helpers ───────────────────────────────────────────────────────
def _ratio(w: int, h: int) -> float:  # always > 0
    return w / h if h else 0.0


def _score(path: Path, target: float) -> float | None:
    try:
        with Image.open(path) as im:
            return abs(_ratio(*im.size) - target)
    except (UnidentifiedImageError, OSError):
        return None


def _maybe_classify(path: Path, tol: float) -> None:
    try:
        with Image.open(path) as im:
            r = _ratio(*im.size)
    except (UnidentifiedImageError, OSError):
        return
    if abs(r - 4 / 3) <= tol:
        dest = DIR_4_3 / path.name
    elif abs(r - 3 / 4) <= tol:
        dest = DIR_3_4 / path.name
    else:
        return
    if not dest.exists():
        try:
            shutil.copy2(path, dest)
        except OSError as e:
            print("Warn: copy failed:", e, file=sys.stderr)


# ─── NASA endpoint wrappers ───────────────────────────────────────────────
def get_apod(count: int = 1) -> list[Path]:
    data = _json("https://api.nasa.gov/planetary/apod", api_key=NASA_KEY, count=count)
    paths: list[Path] = []
    for entry in data:
        if entry.get("media_type") == "image":
            paths.append(_download(entry.get("hdurl") or entry["url"]))
    if not paths:
        raise RuntimeError("APOD returned no images.")
    return paths


def get_mars(rover: str = "curiosity") -> list[Path]:
    data = _json(
        f"https://api.nasa.gov/mars-photos/api/v1/rovers/{rover}/latest_photos",
        api_key=NASA_KEY,
    )["latest_photos"]
    if not data:
        raise RuntimeError(f"No recent photos for rover {rover!r}.")
    return [_download(random.choice(data)["img_src"])]


def get_epic() -> list[Path]:
    items = _json("https://api.nasa.gov/EPIC/api/natural", api_key=NASA_KEY)
    if not items:
        raise RuntimeError("EPIC feed empty.")
    item = items[0]
    date = _dt.datetime.fromisoformat(item["date"])
    url = f"https://epic.gsfc.nasa.gov/archive/natural/{date:%Y/%m/%d}/png/{item['image']}.png"
    return [_download(url)]


def get_earth(lat: float, lon: float, dim: float) -> list[Path]:
    data = _json(
        "https://api.nasa.gov/planetary/earth/imagery",
        lat=lat,
        lon=lon,
        dim=dim,
        api_key=NASA_KEY,
    )
    if "url" not in data:
        raise RuntimeError("Earth imagery returned no URL.")
    return [_download(data["url"])]


def get_search(query: str) -> list[Path]:
    items = _json(
        "https://images-api.nasa.gov/search", q=query, media_type="image"
    )["collection"]["items"]
    if not items:
        raise RuntimeError("No images match your query.")
    return [_download(items[0]["links"][0]["href"])]


# ─── Display helper ───────────────────────────────────────────────────────
def _fit_cover(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    s = max(WIDTH / img.width, HEIGHT / img.height)
    new = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    l = (new.width - WIDTH) // 2
    t = (new.height - HEIGHT) // 2
    return new.crop((l, t, l + WIDTH, t + HEIGHT))


def _show(path: Path) -> None:
    try:
        with Image.open(path) as raw:
            frame = _fit_cover(raw)
            if INKY:
                INKY.set_image(frame)
                INKY.show()
            else:
                prev = path.with_suffix(".preview.png")
                frame.save(prev)
                print("Preview →", prev)
    except (UnidentifiedImageError, OSError) as e:
        raise RuntimeError(f"Display failed: {e}") from None


# ─── CLI ──────────────────────────────────────────────────────────────────
def _args():
    p = argparse.ArgumentParser(description="Fetch & show NASA imagery")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--apod", action="store_true", help="random Astronomy Picture of the Day [default]")
    src.add_argument("--mars", nargs="?", const="curiosity", metavar="ROVER", help="latest rover photo")
    src.add_argument("--epic", action="store_true", help="latest DSCOVR EPIC Earth image")
    src.add_argument("--earth", nargs=2, metavar=("LAT", "LON"), type=float, help="Landsat/MODIS tile")
    src.add_argument("--search", metavar="QUERY", help="NASA Image Library search term")

    p.add_argument("--dim", type=float, default=0.15, help="size in degrees for --earth (default 0.15)")
    p.add_argument("--key", help="override NASA API key")

    p.add_argument("--batch", type=int, default=1, metavar="N", help="fetch N random images to choose the best")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--landscape", action="store_true", help="optimise for 4:3 (default)")
    mode.add_argument("--portrait", action="store_true", help="optimise for 3:4")
    p.add_argument("--tolerance", type=float, default=0.03, help="aspect ratio tolerance (default 0.03)")

    return p.parse_args()


# ─── Main ─────────────────────────────────────────────────────────────────
def main():
    args = _args()
    if args.key:
        globals()["NASA_KEY"] = args.key.strip()

    target_ratio = 3 / 4 if args.portrait else 4 / 3
    tolerance = max(0.0, args.tolerance)

    # select source function
    if args.mars is not None:
        fetcher = lambda: get_mars(args.mars)
    elif args.epic:
        fetcher = get_epic
    elif args.earth:
        lat, lon = args.earth
        fetcher = lambda: get_earth(lat, lon, args.dim)
    elif args.search:
        fetcher = lambda: get_search(args.search)
    else:
        fetcher = lambda: get_apod(args.batch if args.apod or args.batch > 1 else 1)

    # acquire images
    paths: list[Path] = []
    try:
        while len(paths) < args.batch:
            paths.extend(fetcher())
            if len(paths) >= args.batch:
                paths = paths[: args.batch]
    except RuntimeError as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)

    # evaluate & classify
    best: tuple[float, Path] | None = None
    for p in paths:
        _maybe_classify(p, tolerance)
        score = _score(p, target_ratio)
        if score is None:
            print("Skipped unreadable file:", p.name, file=sys.stderr)
            continue
        if best is None or score < best[0]:
            best = (score, p)

    if best is None:
        print("No valid images downloaded.", file=sys.stderr)
        sys.exit(1)

    try:
        _show(best[1])
    except RuntimeError as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)

    print("Displayed →", best[1])
    print("NASA API calls this run:", API_CALLS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
