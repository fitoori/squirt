#!/usr/bin/env python3
"""
landscapes.py — fetch a previously unseen landscape painting from multiple
collections (Met, AIC, CMA, MIA), respect orientation flags, and display it
on an Inky e-paper panel (or save a PNG preview). Falls back to cycling local
images if all online backends are exhausted.

Usage
-----
  ./landscapes.py                 # random from any backend, any orientation
  ./landscapes.py --wide          # only landscape (w ≥ h)
  ./landscapes.py --tall          # only portrait  (h > w)
  ./landscapes.py --met           # only The Met
  ./landscapes.py --aic           # only Art Institute of Chicago
  ./landscapes.py --cma           # only Cleveland Museum of Art
  ./landscapes.py --mia           # only Minneapolis Institute of Art
  ./landscapes.py --mode fill     # force crop‐to‐fill (default: fit letterbox)

Folders
-------
static/
└── landscapes/
    ├── seen.json        (cache of seen IDs & .rej markers)
    ├── *_rej           (orientation rejects)
    └── *.jpg/.preview.png  (images + previews)

Exit codes: 0 success, 1 failure.
"""

from __future__ import annotations
import argparse, io, json, os, random, re, subprocess, sys, traceback
from pathlib import Path
from typing import Dict, Set, Optional, Callable

import certifi
import requests
from PIL import Image, UnidentifiedImageError

# ───────────────────────────── Configuration ──────────────────────────────
ROOT_DIR      = Path(__file__).with_name("static")
SAVE_DIR      = ROOT_DIR / "landscapes"
SAVE_DIR.mkdir(parents=True, exist_ok=True)

TIMEOUT       = 15
RETRIES       = 2
HEADLESS_RES  = (1600, 1200)

INKY_TYPE     = "el133uf1"     # override if auto-detect fails
INKY_COLOUR   = None           # for PHAT/WHAT
MAX_ATTEMPTS  = 30
REJ_SUFFIX    = ".rej"         # orientation‐reject marker suffix

# ───────────────────────────── pip helper ─────────────────────────────────
def _pip_install(*pkgs: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--user",
         "--break-system-packages", *pkgs],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    )

# ─────────────────────────── Inky detection ───────────────────────────────
def init_inky():
    try:
        import inky, numpy  # noqa
    except ModuleNotFoundError:
        print("Installing inky + numpy…")
        _pip_install("inky>=2.1.0", "numpy")
        try:
            import inky  # noqa
        except ModuleNotFoundError:
            pass

    # 1) EEPROM auto-detect
    try:
        from inky.auto import auto
        dev = auto()
        return dev, *dev.resolution
    except Exception:
        pass

    # 2) Manual class map
    class_map = {
        "el133uf1":      "InkyEL133UF1",    # 13.3″ Spectra-6
        "spectra13":     "InkyEL133UF1",
        "impression73":  "InkyImpression73",# 7-colour 7.3″ fallback
        "phat":          "InkyPHAT",
        "what":          "InkyWHAT",
    }
    key = INKY_TYPE.lower()
    if key not in class_map:
        print("No Inky detected → headless previews.")
        return None, *HEADLESS_RES

    try:
        cls = getattr(__import__("inky", fromlist=[class_map[key]]),
                      class_map[key])
        dev = cls(INKY_COLOUR) if key in ("phat", "what") else cls()
        return dev, *dev.resolution
    except Exception as exc:
        print("Inky init failed:", exc, file=sys.stderr)
        return None, *HEADLESS_RES

INKY, WIDTH, HEIGHT = init_inky()

# ───────────────────────────── HTTP helpers ───────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "LandscapeFetcher/1.4"})
SESSION.mount("https://", requests.adapters.HTTPAdapter(max_retries=RETRIES))
SESSION.mount("http://",  requests.adapters.HTTPAdapter(max_retries=RETRIES))
API_CALLS = 0

def _safe_request(url: str, **kw) -> requests.Response:
    global API_CALLS; API_CALLS += 1
    kw.setdefault("timeout", TIMEOUT)
    kw.setdefault("verify", certifi.where())
    r = SESSION.get(url, **kw)
    r.raise_for_status()
    return r

def jget(url: str, **params) -> dict:
    return _safe_request(url, params=params).json()

def fetch(url: str) -> bytes:
    return _safe_request(url).content

# ────────────────────────── Seen bookkeeping ───────────────────────────────
_seen_rx = re.compile(r'_(met|aic|cma|mia)_(\d+)\.(jpg|rej)$', re.I)
def _index_seen() -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for p in SAVE_DIR.iterdir():
        m = _seen_rx.search(p.name)
        if m:
            out.setdefault(m[1].lower(), set()).add(m[2])
    return out

SEEN = _index_seen()

def seen(grp: str, oid: str) -> bool:
    return oid in SEEN.get(grp, set())

def mark_seen(grp: str, oid: str, good: bool):
    if oid in SEEN.get(grp, set()):
        return
    SEEN.setdefault(grp, set()).add(oid)
    if not good:
        try:
            (SAVE_DIR / f"{grp}_{oid}{REJ_SUFFIX}").touch(exist_ok=True)
        except OSError as e:
            print("WARN: failed writing .rej marker:", e, file=sys.stderr)

# ─────────────────────────── Generic helpers ──────────────────────────────
slug = lambda s, l=60: re.sub(r"[^A-Za-z0-9]+","_", s)[:l].strip("_").lower() or "untitled"

def save_if_ok(data: bytes, title: str, grp: str,
               oid: str, want_wide: Optional[bool]) -> Optional[Path]:
    # Quick JPEG check
    if not data.startswith(b'\xff\xd8'):
        mark_seen(grp, oid, False)
        return None

    try:
        with Image.open(io.BytesIO(data)) as im:
            wide = im.width >= im.height
            if want_wide is None or want_wide == wide:
                path = SAVE_DIR / f"{slug(title)}_{grp}_{oid}.jpg"
                if not path.exists():
                    path.write_bytes(data)
                mark_seen(grp, oid, True)
                return path
    except UnidentifiedImageError:
        pass

    mark_seen(grp, oid, False)
    return None

# ────────────────────────── Museum back-ends ───────────────────────────────
def backend(tag: str):
    def wrapper(fn: Callable[[Optional[bool]], Path]):
        fn._tag = tag
        return fn
    return wrapper

@backend("met")
def met_random(w: Optional[bool]) -> Path:
    ids = jget(
        "https://collectionapi.metmuseum.org/public/collection/v1/search",
        q="landscape", medium="Paintings", hasImages="true"
    ).get("objectIDs") or []
    random.shuffle(ids)

    attempts = 0
    for oid in ids:
        if attempts >= MAX_ATTEMPTS:
            break
        if seen("met", str(oid)):
            continue
        try:
            obj = jget(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}")
            url = obj.get("primaryImage") or obj.get("primaryImageSmall")
            if not url:
                continue
            p = save_if_ok(fetch(url), obj.get("title", f"met_{oid}"), "met", str(oid), w)
            if p:
                return p
        except Exception as e:
            print("Met:", e, file=sys.stderr)
        attempts += 1

    raise RuntimeError("Met: exhausted all attempts")

@backend("aic")
def aic_random(w: Optional[bool]) -> Path:
    base = "https://www.artic.edu/iiif/2"
    attempts = 0
    while attempts < MAX_ATTEMPTS:
        try:
            hits = jget(
                "https://api.artic.edu/api/v1/artworks/search",
                q="landscape", fields="id,title,image_id",
                page=random.randint(1,50), limit=100
            ).get("data", [])
            random.shuffle(hits)
            for h in hits:
                oid, imgid = str(h["id"]), h["image_id"]
                if not imgid or seen("aic", oid):
                    continue
                url = f"{base}/{imgid}/full/843,/0/default.jpg"
                p = save_if_ok(fetch(url), h["title"], "aic", oid, w)
                if p:
                    return p
                attempts += 1
                if attempts >= MAX_ATTEMPTS:
                    break
        except Exception as e:
            print("AIC:", e, file=sys.stderr)
            attempts += 1

    raise RuntimeError("AIC: exhausted all attempts")

@backend("cma")
def cma_random(w: Optional[bool]) -> Path:
    attempts = 0
    while attempts < MAX_ATTEMPTS:
        try:
            hits = jget(
                "https://openaccess-api.clevelandart.org/api/artworks",
                q="landscape", type="Painting", has_image=1,
                limit=100, skip=random.randint(0, 5000)
            ).get("data", [])
            random.shuffle(hits)
            for h in hits:
                oid = str(h["id"])
                if seen("cma", oid):
                    continue
                img = h.get("images", {}).get("web", {}).get("url")
                title = h.get("title", "untitled")
                if not img:
                    continue
                p = save_if_ok(fetch(img), title, "cma", oid, w)
                if p:
                    return p
                attempts += 1
                if attempts >= MAX_ATTEMPTS:
                    break
        except Exception as e:
            print("CMA:", e, file=sys.stderr)
            attempts += 1

    raise RuntimeError("CMA: exhausted all attempts")

@backend("mia")
def mia_random(w: Optional[bool]) -> Path:
    attempts = 0
    while attempts < MAX_ATTEMPTS:
        try:
            offset = random.randint(0, 5000)
            hits = jget(
                "https://api.artsmia.org/objects",
                query="landscape painting", has_images=1,
                size=100, from_=offset
            ).get("records", [])
            random.shuffle(hits)
            for h in hits:
                oid = str(h["id"])
                if seen("mia", oid):
                    continue
                img = h.get("primaryimageurl")
                title = h.get("title", "untitled")
                if not img:
                    continue
                p = save_if_ok(fetch(img), title, "mia", oid, w)
                if p:
                    return p
                attempts += 1
                if attempts >= MAX_ATTEMPTS:
                    break
        except Exception as e:
            print("MIA:", e, file=sys.stderr)
            attempts += 1

    raise RuntimeError("MIA: exhausted all attempts")

# Map tag → function
BACKENDS: Dict[str, Callable[[Optional[bool]], Path]] = {
    fn._tag: fn for fn in (met_random, aic_random, cma_random, mia_random)
}

# ────────────────────────── Imaging helpers ───────────────────────────────
def scale_cover(img: Image.Image) -> Image.Image:
    s = max(WIDTH / img.width, HEIGHT / img.height)
    n = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    l = (n.width - WIDTH) // 2; t = (n.height - HEIGHT) // 2
    return n.crop((l, t, l + WIDTH, t + HEIGHT))

def scale_fit(img: Image.Image) -> Image.Image:
    s = min(WIDTH / img.width, HEIGHT / img.height)
    n = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    bg = Image.new("RGB", (WIDTH, HEIGHT), "white")
    bg.paste(n, ((WIDTH - n.width) // 2, (HEIGHT - n.height) // 2))
    return bg

def display(path: Path, mode: str):
    with Image.open(path) as raw:
        frame = scale_cover(raw) if mode == "fill" else scale_fit(raw)
        if INKY:
            INKY.set_image(frame)
            INKY.show()
        else:
            preview = path.with_suffix(f".{mode}.preview.png")
            frame.save(preview)
            print("Headless preview →", preview)

# ────────────────────────── Offline fallback ──────────────────────────────
def local_cycle(w: Optional[bool]) -> Path:
    files = sorted(SAVE_DIR.glob("*.jpg"), key=lambda p: p.stat().st_atime)
    if not files:
        raise RuntimeError("No local images for offline mode")
    for p in files:
        try:
            with Image.open(p) as im:
                if w is None or (im.width >= im.height) == w:
                    os.utime(p, None)
                    return p
        except Exception:
            pass
    raise RuntimeError("Offline: no orientation match")

# ───────────────────────────── CLI & main ────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Landscape painting fetcher/cycler")
    src = p.add_mutually_exclusive_group()
    for tag in BACKENDS:
        src.add_argument(f"--{tag}", action="store_true",
                         help=f"only {tag.upper()}")
    ori = p.add_mutually_exclusive_group()
    ori.add_argument("--wide", action="store_true", help="landscape only (w ≥ h)")
    ori.add_argument("--tall", action="store_true", help="portrait only (h > w)")
    p.add_argument("--mode", choices=("fill", "fit"), default="fit",
                   help="fill (crop) or fit (letter-box)")
    return p.parse_args()

def main():
    args = parse_args()
    want = True if args.wide else False if args.tall else None

    # pick backends
    selected = [t for t in BACKENDS if getattr(args, t)]
    backends = [BACKENDS[t] for t in selected] if selected else list(BACKENDS.values())
    random.shuffle(backends)

    # try each
    for be in backends:
        try:
            pic = be(want)
            display(pic, args.mode)
            print(f"Saved → {pic}")
            print(f"HTTP requests this run: {API_CALLS}")
            return
        except Exception as e:
            print(f"[{be.__name__}] {e}", file=sys.stderr)

    # fallback to local files
    try:
        pic = local_cycle(want)
        display(pic, args.mode)
        print(f"(offline) Displayed → {pic}")
    except Exception as e:
        traceback.print_exc()
        sys.exit(f"Offline fallback failed: {e}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
