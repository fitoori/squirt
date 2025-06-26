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

Folders
-------
static/
└── nasa/   ← all downloaded images and optional *_preview.png

Exit codes: 0 success, 1 failure.
"""

from __future__ import annotations
import argparse, datetime as _dt, os, random, sys, subprocess, requests, json
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image, UnidentifiedImageError

# ───────────────────────────── Configuration ──────────────────────────────
ROOT_DIR     = Path(__file__).with_name("static")
SAVE_DIR     = ROOT_DIR / "nasa"
SAVE_DIR.mkdir(parents=True, exist_ok=True)

TIMEOUT      = 15
RETRIES      = 2
HEADLESS_RES = (1600, 1200)

# Manual override if auto‑detect fails & no EEPROM:
INKY_TYPE    = "el133uf1"        # "el133uf1" | "phat" | "what" | ""
INKY_COLOUR  = None              # for PHAT/WHAT only

NASA_KEY     = os.getenv("NASA_API_KEY", "DEMO_KEY").strip()

# ─────────────────────────── Helper → pip install ──────────────────────────
def _pip_install(*pkgs: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "--quiet", "--user", "--break-system-packages", *pkgs],
        check=False,
    )

# ─────────────────────────── Helper → Inky detect ──────────────────────────
def init_inky():
    """
    Returns (inky_or_None, WIDTH, HEIGHT).
    Tries EEPROM auto‑detect first, else falls back to a class map.
    """
    try:
        import inky, numpy  # noqa
    except ModuleNotFoundError:
        print("Installing inky & numpy…")
        _pip_install("inky>=2.1.0", "numpy")
        try:
            import inky  # noqa
        except ModuleNotFoundError:
            pass

    # 1) EEPROM auto‑detect (works on Impression HATs)
    try:
        from inky.auto import auto
        dev = auto()
        return dev, *dev.resolution
    except Exception:
        pass

    # 2) Manual class map (no EEPROM or breakout)
    class_map = {
        "el133uf1":      "InkyEL133UF1",   # 13.3″ Spectra‑6 Impression
        "spectra13":     "InkyEL133UF1",   # legacy synonym
        "impression13":  "InkyEL133UF1",
        "phat":          "InkyPHAT",
        "what":          "InkyWHAT",
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

# ────────────────── Helper → robust HTTP session / download ────────────────
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(max_retries=RETRIES))
SESSION.mount("http://",  requests.adapters.HTTPAdapter(max_retries=RETRIES))

API_CALLS = 0

def api_json(url: str, **params):
    global API_CALLS; API_CALLS += 1
    try:
        r = SESSION.get(url, params=params, timeout=TIMEOUT); r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        if r.status_code == 403:
            print("🔑 NASA API key rejected or quota exceeded.", file=sys.stderr)
        raise RuntimeError(f"API error: {e}") from None
    except requests.RequestException as e:
        raise RuntimeError(f"Network error: {e}") from None
    except json.JSONDecodeError:
        raise RuntimeError("Invalid JSON from NASA") from None

def download_file(url: str) -> Path:
    global API_CALLS
    fname = os.path.basename(urlparse(url).path) or "image.jpg"
    target = SAVE_DIR / fname
    if target.exists():
        return target
    API_CALLS += 1
    with SESSION.get(url, stream=True, timeout=TIMEOUT) as r:
        r.raise_for_status()
        with target.open("wb") as fp:
            for chunk in r.iter_content(8192):
                fp.write(chunk)
    return target

# ───────────────────── Helper → cover crop fit ─────────────────────────────
def fit_image_cover(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    scale = max(WIDTH / img.width, HEIGHT / img.height)
    new = img.resize((round(img.width*scale), round(img.height*scale)),
                     Image.LANCZOS)
    l = (new.width - WIDTH)//2; t = (new.height - HEIGHT)//2
    return new.crop((l, t, l+WIDTH, t+HEIGHT))

# ─────────────────────── NASA endpoint wrappers ────────────────────────────
def get_apod() -> Path:
    data = api_json("https://api.nasa.gov/planetary/apod",
                    api_key=NASA_KEY, count=1)[0]
    if data.get("media_type") != "image":
        raise RuntimeError("APOD entry is not an image.")
    return download_file(data.get("hdurl") or data["url"])

def get_mars(rover="curiosity") -> Path:
    data = api_json(
        f"https://api.nasa.gov/mars-photos/api/v1/rovers/{rover}/latest_photos",
        api_key=NASA_KEY)["latest_photos"]
    if not data:
        raise RuntimeError(f"No recent photos for rover {rover!r}.")
    return download_file(random.choice(data)["img_src"])

def get_epic() -> Path:
    items = api_json("https://api.nasa.gov/EPIC/api/natural", api_key=NASA_KEY)
    if not items:
        raise RuntimeError("EPIC feed empty.")
    item = items[0]
    date = _dt.datetime.fromisoformat(item["date"])
    url = (f"https://epic.gsfc.nasa.gov/archive/natural/"
           f"{date:%Y/%m/%d}/png/{item['image']}.png")
    return download_file(url)

def get_earth(lat: float, lon: float, dim=0.15) -> Path:
    data = api_json("https://api.nasa.gov/planetary/earth/imagery",
                    lat=lat, lon=lon, dim=dim, api_key=NASA_KEY)
    if "url" not in data:
        raise RuntimeError("Earth imagery returned no image URL.")
    return download_file(data["url"])

def get_search(query: str) -> Path:
    data = api_json("https://images-api.nasa.gov/search",
                    q=query, media_type="image")["collection"]["items"]
    if not data:
        raise RuntimeError("No images match your query.")
    return download_file(data[0]["links"][0]["href"])

# ─────────────────────────── display helper ───────────────────────────────
def show(path: Path):
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

# ─────────────────────────────── CLI ───────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Show one NASA image on Inky.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--apod",  action="store_true",
                   help="random Astronomy Picture of the Day [default]")
    g.add_argument("--mars",  nargs="?", const="curiosity", metavar="ROVER",
                   help="latest rover photo (curiosity, perseverance, …)")
    g.add_argument("--epic",  action="store_true",
                   help="latest DSCOVR EPIC Earth image")
    g.add_argument("--earth", nargs=2, metavar=("LAT","LON"), type=float,
                   help="Landsat/MODIS tile around lat/lon")
    g.add_argument("--search", metavar="QUERY",
                   help="first match from NASA Image Library")
    p.add_argument("--dim", type=float, default=0.15,
                   help="width/height in degrees for --earth (default 0.15)")
    p.add_argument("--key", help="override NASA API key")
    return p.parse_args()

# ─────────────────────────────── Main ──────────────────────────────────────
def main():
    args = parse_args()
    if args.key:
        globals()["NASA_KEY"] = args.key.strip()

    try:
        if args.mars is not None:   path = get_mars(args.mars)
        elif args.epic:             path = get_epic()
        elif args.earth:            lat, lon = args.earth; path = get_earth(lat, lon, args.dim)
        elif args.search:           path = get_search(args.search)
        else:                       path = get_apod()

        show(path)
        print("Saved →", path)
        print("NASA API calls this run:", API_CALLS)
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
