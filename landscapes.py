#!/usr/bin/env python3
"""
landscapes.py — fetch a previously unseen landscape painting (Met or AIC)
and show it on an Inky e‑paper display cropped full‑screen.

Usage
-----
  ./landscapes.py                 # random Met or AIC, any orientation
  ./landscapes.py --wide          # landscape (w ≥ h) only
  ./landscapes.py --tall          # portrait  (h > w) only
  ./landscapes.py --met           # Met only   (flags still apply)
  ./landscapes.py --aic           # AIC only
  ./landscapes.py --reset         # clear seen.json and exit

Folders
-------
static/
└── landscapes/
    ├── seen.json     (object‑ID cache to avoid repeats)
    └── *.jpg / *.png (images + *_preview.png when headless)

Exit codes: 0 success, 1 failure.
"""

from __future__ import annotations
import argparse, io, json, os, random, re, sys, time, subprocess, requests
from pathlib import Path
from typing import Dict, List, Optional
from PIL import Image, UnidentifiedImageError

# ─────────────────────────── Configuration ──────────────────────────────
ROOT_DIR       = Path(__file__).with_name("static")
SAVE_DIR       = ROOT_DIR / "landscapes"
SAVE_DIR.mkdir(parents=True, exist_ok=True)

CACHE_FILE     = SAVE_DIR / "seen.json"
TIMEOUT        = 15
RETRIES        = 2
HEADLESS_RES   = (1600, 1200)

# Inky detection fallback class map
INKY_TYPE      = "el133uf1"
INKY_COLOUR    = None                    # for PHAT/WHAT, ignored otherwise

MAX_ATTEMPTS   = 30                      # tries per backend for orientation

# ─────────────────────────── Helper → pip install ──────────────────────────
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
        "el133uf1":      "InkyEL133UF1",    # 13.3″ Spectra‑6
        "impression73":  "InkyImpression73",# 7‑colour 7.3″ (fallback)
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

# ─────────────────────── Helper → HTTP session / download ──────────────────
SESSION = requests.Session()
SESSION.mount("https://", requests.adapters.HTTPAdapter(max_retries=RETRIES))
SESSION.mount("http://",  requests.adapters.HTTPAdapter(max_retries=RETRIES))

API_CALLS = 0
def jget(url: str, **params) -> dict:
    global API_CALLS; API_CALLS += 1
    r = SESSION.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def fetch(url: str) -> bytes:
    global API_CALLS; API_CALLS += 1
    r = SESSION.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content

# ─────────────────────── Helper → orientation & cache ──────────────────────
def load_seen() -> Dict[str, List[str]]:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}

SEEN: Dict[str, List[str]] = load_seen()

def mark_seen(museum: str, oid: str) -> None:
    SEEN.setdefault(museum, []).append(oid)
    CACHE_FILE.write_text(json.dumps(SEEN, indent=2))

def seen(museum: str, oid: str) -> bool:
    return oid in SEEN.get(museum, [])

def slug(text: str, maxlen: int = 60) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text)[:maxlen].strip("_").lower() or "untitled"

def save_if_ok(img_bytes: bytes,
               title: str,
               museum: str,
               oid: str,
               want_wide: Optional[bool]) -> Optional[Path]:
    """
    Decode bytes, check orientation, save & mark seen if matches.
    Returns Path on success else None (still marks seen).
    """
    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            wide = img.width >= img.height
            if want_wide is None or want_wide == wide:
                name = f"{slug(title)}_{museum}_{oid}.jpg"
                path = SAVE_DIR / name
                if not path.exists():
                    path.write_bytes(img_bytes)
                mark_seen(museum, oid)
                return path
    except UnidentifiedImageError:
        pass
    mark_seen(museum, oid)
    return None

# ─────────────────────── Met backend ────────────────────────────────────────
def met_random(want_wide: Optional[bool]) -> Path:
    data = jget(
        "https://collectionapi.metmuseum.org/public/collection/v1/search",
        q="landscape", medium="Paintings", hasImages="true"
    )
    ids = data.get("objectIDs") or []
    random.shuffle(ids)

    attempts = 0
    for oid in ids:
        if attempts >= MAX_ATTEMPTS:
            break
        if seen("met", str(oid)):
            continue
        obj = jget(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}")
        url = obj.get("primaryImage") or obj.get("primaryImageSmall")
        if not url:
            continue

        path = save_if_ok(
            fetch(url), obj.get("title", f"met_{oid}"),
            "met", str(oid), want_wide
        )
        if path:
            return path
        attempts += 1

    raise RuntimeError("Met: no unseen painting matched the requested orientation")

# ─────────────────────── AIC backend ────────────────────────────────────────
def aic_random(want_wide: Optional[bool]) -> Path:
    base = "https://www.artic.edu/iiif/2"
    attempts = 0

    for _ in range(MAX_ATTEMPTS):
        if attempts >= MAX_ATTEMPTS:
            break
        page = random.randint(1, 50)
        hits = jget(
            "https://api.artic.edu/api/v1/artworks/search",
            q="landscape", fields="id,title,image_id",
            page=page, limit=100
        ).get("data", [])
        random.shuffle(hits)
        for h in hits:
            oid, img_id = str(h["id"]), h["image_id"]
            if not img_id or seen("aic", oid):
                continue
            url = f"{base}/{img_id}/full/843,/0/default.jpg"
            path = save_if_ok(fetch(url), h["title"], "aic", oid, want_wide)
            if path:
                return path
            attempts += 1

    raise RuntimeError("AIC: no unseen painting matched the requested orientation")

# ─────────────────────── Image fit helper (cover) ───────────────────────────
def fit_image_cover(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    scale = max(WIDTH / img.width, HEIGHT / img.height)
    new = img.resize((round(img.width*scale), round(img.height*scale)),
                     Image.LANCZOS)
    l = (new.width - WIDTH)//2; t = (new.height - HEIGHT)//2
    return new.crop((l, t, l+WIDTH, t+HEIGHT))

# ───────────────────────────── display helper ──────────────────────────────
def display(path: Path):
    try:
        with Image.open(path) as raw:
            frame = fit_image_cover(raw)
            if INKY:
                INKY.set_image(frame)
                INKY.show()
            else:
                preview = path.with_suffix(".preview.png")
                frame.save(preview)
                print("Headless preview →", preview)
    except (UnidentifiedImageError, OSError) as e:
        raise RuntimeError(f"Display failed: {e}") from None

# ───────────────────────────── CLI parsing ─────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Display an unseen landscape painting")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--met", action="store_true", help="only from The Met")
    src.add_argument("--aic", action="store_true", help="only from AIC")

    ori = p.add_mutually_exclusive_group()
    ori.add_argument("--wide", action="store_true", help="landscape only (w ≥ h)")
    ori.add_argument("--tall", action="store_true", help="portrait only (h > w)")

    p.add_argument("--reset", action="store_true", help="clear seen.json then exit")
    return p.parse_args()

# ─────────────────────────────── Main ──────────────────────────────────────
def main():
    args = parse_args()
    if args.reset:
        CACHE_FILE.unlink(missing_ok=True)
        print("seen.json cleared")
        return

    want_wide: Optional[bool] = True if args.wide else False if args.tall else None
    backends = (
        [met_random] if args.met else
        [aic_random] if args.aic else
        [met_random, aic_random]
    )
    random.shuffle(backends)

    for be in backends:
        try:
            pic = be(want_wide)
            display(pic)
            print("Saved →", pic)
            print("HTTP requests this run:", API_CALLS)
            return
        except Exception as e:
            print(f"[{be.__name__}] {e}", file=sys.stderr)

    sys.exit("All back‑ends failed")

if __name__ == "__main__":
    main()
