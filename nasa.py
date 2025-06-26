#!/usr/bin/env python3
"""
nasa.py – show a NASA photo on an Inky display (or leave a preview).

Flags (choose one source):
  --apod                 random Astronomy Picture of the Day  [default]
  --mars [ROVER]         latest Mars-rover shot (curiosity | perseverance | opportunity | spirit)
  --epic                 latest DSCOVR EPIC full-disk Earth image
  --earth LAT LON [--dim KM]  Landsat/Modis tile around lat/lon (dim° wide)
  --search "QUERY"       first hit from NASA Image & Video Library

Other flags:
  --key YOUR_KEY         override NASA_API_KEY env var / DEMO_KEY
  --dim KM               width/height in degrees for --earth (default 0.15)

The script auto-installs inky + numpy, runs headless if no board,
and centre-crops each image to fill the panel.
"""

import argparse, datetime as _dt, json, os, random, sys, subprocess, requests
from pathlib import Path
from urllib.parse import urlparse
from PIL import Image, UnidentifiedImageError

# ── Basic config ────────────────────────────────────────────────────────────
SAVE_DIR     = Path(__file__).with_name("static"); SAVE_DIR.mkdir(exist_ok=True)
TIMEOUT      = 15
RETRIES      = 2               # auto-retries on flaky connections
HEADLESS_RES = (1600, 1200)
INKY_TYPE    = "spectra13"     # change if auto-detect fails
INKY_COLOUR  = None
NASA_KEY     = os.getenv("NASA_API_KEY", "DEMO_KEY").strip()

# Count every NASA request this run
API_CALLS = 0

# ── pip helper ──────────────────────────────────────────────────────────────
def _pip_install(*packages: str):
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--user",
         "--break-system-packages", *packages],
        check=True,
    )

# ── Inky initialisation ─────────────────────────────────────────────────────
def init_inky():
    try:
        import inky, numpy  # noqa: F401
    except ModuleNotFoundError:
        print("Installing inky & numpy…")
        _pip_install("inky>=2.1.0", "numpy")
        import inky  # noqa: F401

    try:
        from inky.auto import auto as auto_inky
        dev = auto_inky()
        return dev, *dev.resolution
    except Exception:
        pass

    try:
        if INKY_TYPE == "spectra13":
            try:
                from inky.spectra13 import InkySpectra13
            except ModuleNotFoundError:
                from inky.spectra import InkySpectra13
            dev = InkySpectra13()
        elif INKY_TYPE == "phat":
            from inky.phat import InkyPHAT; dev = InkyPHAT(INKY_COLOUR or "red")
        elif INKY_TYPE == "what":
            from inky.what import InkyWHAT; dev = InkyWHAT(INKY_COLOUR or "red")
        else:
            raise ValueError("unknown INKY_TYPE")
        return dev, *dev.resolution
    except Exception as e:
        print("Inky unavailable (headless):", e, file=sys.stderr)
        return None, *HEADLESS_RES

inky, WIDTH, HEIGHT = init_inky()

# ── Image helper: scale-and-crop to fill ────────────────────────────────────
def crop_fit(img: Image.Image,
             bg: tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    try:
        img = img.convert("RGB")
        scale = max(WIDTH / img.width, HEIGHT / img.height)
        new = img.resize((round(img.width*scale), round(img.height*scale)),
                         Image.LANCZOS)
        l = (new.width - WIDTH)//2; t = (new.height - HEIGHT)//2
        return new.crop((l, t, l+WIDTH, t+HEIGHT))
    except Exception as e:
        print("crop_fit failed:", e, file=sys.stderr)
        return Image.new("RGB", (WIDTH, HEIGHT), bg)

# ── Robust HTTP helpers ─────────────────────────────────────────────────────
session = requests.Session()
session.mount("https://", requests.adapters.HTTPAdapter(max_retries=RETRIES))
session.mount("http://",  requests.adapters.HTTPAdapter(max_retries=RETRIES))

def safe_json(url: str, **params):
    global API_CALLS
    API_CALLS += 1
    try:
        r = session.get(url, params=params, timeout=TIMEOUT); r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        if r.status_code == 403:
            print("🔑 NASA API key rejected or over quota.", file=sys.stderr)
        raise RuntimeError(f"API request failed: {e}") from None
    except requests.RequestException as e:
        raise RuntimeError(f"Network error: {e}") from None
    except json.JSONDecodeError:
        raise RuntimeError("API returned invalid JSON") from None

def safe_download(url: str) -> Path:
    global API_CALLS
    fname = os.path.basename(urlparse(url).path) or "image.jpg"
    target = SAVE_DIR / fname
    if target.exists():
        return target
    API_CALLS += 1
    try:
        with session.get(url, stream=True, timeout=TIMEOUT) as r:
            r.raise_for_status()
            with target.open("wb") as fp:
                for chunk in r.iter_content(8192): fp.write(chunk)
    except requests.RequestException as e:
        raise RuntimeError(f"Download failed: {e}") from None
    return target

# ── Endpoint wrappers ───────────────────────────────────────────────────────
def get_apod() -> Path:
    data = safe_json("https://api.nasa.gov/planetary/apod",
                     api_key=NASA_KEY, count=1)[0]
    if data.get("media_type") != "image":
        raise RuntimeError("APOD entry is a video; try again.")
    return safe_download(data.get("hdurl") or data["url"])

def get_mars(rover="curiosity") -> Path:
    data = safe_json(
        f"https://api.nasa.gov/mars-photos/api/v1/rovers/{rover}/latest_photos",
        api_key=NASA_KEY)["latest_photos"]
    if not data:
        raise RuntimeError(f"No recent photos for rover {rover!r}")
    return safe_download(random.choice(data)["img_src"])

def get_epic() -> Path:
    items = safe_json("https://api.nasa.gov/EPIC/api/natural",
                      api_key=NASA_KEY)
    if not items:
        raise RuntimeError("EPIC feed empty.")
    item = items[0]
    date = _dt.datetime.fromisoformat(item["date"])
    url = (f"https://epic.gsfc.nasa.gov/archive/natural/"
           f"{date:%Y/%m/%d}/png/{item['image']}.png")
    return safe_download(url)

def get_earth(lat: float, lon: float, dim=0.15) -> Path:
    data = safe_json("https://api.nasa.gov/planetary/earth/imagery",
                     lat=lat, lon=lon, dim=dim, api_key=NASA_KEY)
    if "url" not in data:
        raise RuntimeError("Earth imagery returned no image URL.")
    return safe_download(data["url"])

def get_search(query: str) -> Path:
    data = safe_json("https://images-api.nasa.gov/search",
                     q=query, media_type="image")["collection"]["items"]
    if not data:
        raise RuntimeError("No images match your query.")
    return safe_download(data[0]["links"][0]["href"])

# ── Display (or preview) ────────────────────────────────────────────────────
def show(path: Path):
    try:
        with Image.open(path) as raw:
            fitted = crop_fit(raw)
            if inky:
                inky.set_image(fitted); inky.show()
            else:
                preview = path.with_name(path.stem+"_preview.png")
                fitted.save(preview); print("Headless preview →", preview)
    except (UnidentifiedImageError, OSError) as e:
        print("Display failed:", e, file=sys.stderr)

# ── CLI ──────────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Show a NASA image on Inky.")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--apod",  action="store_true",
                   help="random Astronomy Picture of the Day [default]")
    g.add_argument("--mars",  nargs="?", const="curiosity", metavar="ROVER",
                   help="latest rover photo (curiosity, perseverance, …)")
    g.add_argument("--epic",  action="store_true",
                   help="latest DSCOVR EPIC Earth image")
    g.add_argument("--earth", nargs=2, metavar=("LAT","LON"), type=float,
                   help="Landsat/Modis tile around lat/lon")
    g.add_argument("--search", metavar="QUERY",
                   help="first match from NASA Image Library")
    p.add_argument("--dim", type=float, default=0.15,
                   help="width/height in degrees for --earth (default 0.15)")
    p.add_argument("--key", help="override NASA API key")
    return p.parse_args()

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    args = parse_args()
    if args.key: globals()["NASA_KEY"] = args.key.strip()

    try:
        if args.mars is not None:   path = get_mars(args.mars)
        elif args.epic:             path = get_epic()
        elif args.earth:            lat,lon = args.earth; path = get_earth(lat,lon,args.dim)
        elif args.search:           path = get_search(args.search)
        else:                       path = get_apod()     # default

        show(path)
        print("Saved to", path)
        print("NASA API calls this run:", API_CALLS)

    except Exception as e:
        print("ERROR:", e, file=sys.stderr); sys.exit(1)

if __name__ == "__main__":
    main()
