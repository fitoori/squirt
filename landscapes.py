#!/usr/bin/env python3
"""
landscapes.py — fetch a previously unseen landscape painting from multiple
collections (Met, AIC, CMA), respect orientation flags, and display it
on an Inky e-paper panel (or save a PNG preview). Falls back to cycling local
images if all online backends are exhausted.

v1.7 - Definitive Edition

This script is considered functionally complete as of July 2025. 
As long as the museums continue presenting their endpoints such as they are,
there is no reason this script should ever fail or become out-of-date.

Attempts were made to include additional sources, but ultimately did not 
make the final cut due to reliablity and consistency issues. 

A special thank-you to The Met, Art Institute of Chicago, and Cleveland Museum of Art 
for the robust presentation of their collected works.

The three endpoints included in this script collectively surface well over half-a-million 
landscape paintings, archiving and presenting a new one each time the script is called.

If you find use of this script or any of the others found in the SQUIRT suite please reach
out to the creator, reachable via email - satbajaj@outlook.com

Usage
-----
  ./landscapes.py                 # random from any backend, any orientation
  ./landscapes.py --wide          # only landscape (w ≥ h)
  ./landscapes.py --tall          # only portrait  (h > w)
  ./landscapes.py --met           # only The Met
  ./landscapes.py --aic           # only Art Institute of Chicago
  ./landscapes.py --cma           # only Cleveland Museum of Art
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
import argparse, io, os, random, re, subprocess, sys, traceback
from pathlib import Path
from typing import Callable, Dict, Optional, Set

import certifi, requests
from PIL import Image, UnidentifiedImageError

# ── Config ────────────────────────────────────────────────────────────────
ROOT_DIR = Path(__file__).with_name("static")
SAVE_DIR = ROOT_DIR / "landscapes"; SAVE_DIR.mkdir(parents=True, exist_ok=True)

TIMEOUT, RETRIES = 15, 2
HEADLESS_RES = (1600, 1200)

INKY_TYPE = "el133uf1"     # override if auto-detect fails
INKY_COLOUR: str | None = None
MAX_ATTEMPTS = 30
REJ_SUFFIX = ".rej"

# ── Silent pip helper ─────────────────────────────────────────────────────
def _pip_install(*pkgs: str) -> None:
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--user",
                    "--break-system-packages", *pkgs],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

# ── Inky detection (unchanged from previous) ──────────────────────────────
def init_inky():
    try:
        import inky, numpy  # noqa: F401
    except ModuleNotFoundError:
        print("Installing inky + numpy …"); _pip_install("inky>=2.1.0", "numpy")
    try:
        from inky.auto import auto
        dev = auto(); return dev, *dev.resolution
    except Exception: pass

    class_map = {
        "el133uf1": "InkyEL133UF1", "spectra13": "InkyEL133UF1",
        "impression73": "InkyImpression73", "phat": "InkyPHAT", "what": "InkyWHAT",
    }
    key = INKY_TYPE.lower()
    if key not in class_map:
        print("No Inky detected → headless previews."); return None, *HEADLESS_RES
    try:
        cls = getattr(__import__("inky", fromlist=[class_map[key]]), class_map[key])
        dev = cls(INKY_COLOUR) if key in ("phat", "what") else cls()
        return dev, *dev.resolution
    except Exception as exc:
        print("Inky init failed:", exc, file=sys.stderr); return None, *HEADLESS_RES

INKY, WIDTH, HEIGHT = init_inky()

# ── HTTP helpers ──────────────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "LandscapeFetcher/1.7"})
SESSION.mount("https://", requests.adapters.HTTPAdapter(max_retries=RETRIES))
SESSION.mount("http://",  requests.adapters.HTTPAdapter(max_retries=RETRIES))
API_CALLS = 0

def _safe_request(url: str, **kw) -> requests.Response:
    global API_CALLS; API_CALLS += 1
    kw.setdefault("timeout", TIMEOUT); kw.setdefault("verify", certifi.where())
    r = SESSION.get(url, **kw); r.raise_for_status(); return r

jget  = lambda url, **p: _safe_request(url, params=p).json()
fetch = lambda url: _safe_request(url).content

# ── Seen bookkeeping ──────────────────────────────────────────────────────
_seen_rx = re.compile(r'_(\w+)_(.+?)\.(jpg|rej)$', re.I)
def _index_seen() -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for p in SAVE_DIR.iterdir():
        if m := _seen_rx.search(p.name):
            out.setdefault(m[1].lower(), set()).add(m[2])
    return out
SEEN = _index_seen()

def seen(g: str, oid: str) -> bool: return oid in SEEN.get(g, set())
def mark_seen(g: str, oid: str, ok: bool):
    if oid in SEEN.get(g, set()): return
    SEEN.setdefault(g, set()).add(oid)
    if not ok:
        try: (SAVE_DIR / f"{g}_{oid}{REJ_SUFFIX}").touch()
        except OSError as e: print("WARN: .rej write:", e, file=sys.stderr)

# ── Generic helpers ───────────────────────────────────────────────────────
slug = lambda s, l=60: re.sub(r"[^A-Za-z0-9]+","_", s)[:l].strip("_").lower() or "untitled"

def save_if_ok(data: bytes, title: str, g: str, oid: str,
               want_wide: Optional[bool]) -> Optional[Path]:
    if not data.startswith(b'\xff\xd8'):
        mark_seen(g, oid, False); return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            wide = im.width >= im.height
            if want_wide is None or want_wide == wide:
                p = SAVE_DIR / f"{slug(title)}_{g}_{oid}.jpg"
                if not p.exists(): p.write_bytes(data)
                mark_seen(g, oid, True); return p
    except UnidentifiedImageError: pass
    mark_seen(g, oid, False); return None

def backend(tag: str):
    def wrap(fn): fn._tag = tag; return fn
    return wrap

# ── Metropolitan Museum of Art ────────────────────────────────────────────
@backend("met")
def met_random(w: Optional[bool]) -> Path:
    ids = jget("https://collectionapi.metmuseum.org/public/collection/v1/search",
               q="landscape", medium="Paintings", hasImages="true").get("objectIDs") or []
    random.shuffle(ids)
    for attempts, oid in enumerate(ids, 1):
        if attempts > MAX_ATTEMPTS: break
        if seen("met", str(oid)): continue
        try:
            obj = jget(f"https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}")
            url = obj.get("primaryImage") or obj.get("primaryImageSmall")
            if not url: continue
            if p := save_if_ok(fetch(url), obj.get("title", f"met_{oid}"), "met", str(oid), w):
                return p
        except Exception as e: print("Met:", e, file=sys.stderr)
    raise RuntimeError("Met: exhausted")

# ── Art Institute of Chicago ──────────────────────────────────────────────
@backend("aic")
def aic_random(w: Optional[bool]) -> Path:
    base = "https://www.artic.edu/iiif/2"
    while (att := 0) < MAX_ATTEMPTS:
        hits = jget("https://api.artic.edu/api/v1/artworks/search",
                    q="landscape", fields="id,title,image_id",
                    page=random.randint(1, 50), limit=100).get("data", [])
        random.shuffle(hits)
        for h in hits:
            att += 1;  oid, imgid = str(h["id"]), h["image_id"]
            if att > MAX_ATTEMPTS or not imgid or seen("aic", oid): break
            url = f"{base}/{imgid}/full/843,/0/default.jpg"
            if p := save_if_ok(fetch(url), h["title"], "aic", oid, w): return p
    raise RuntimeError("AIC: exhausted")

# ── Cleveland Museum of Art ───────────────────────────────────────────────
@backend("cma")
def cma_random(w: Optional[bool]) -> Path:
    while (att := 0) < MAX_ATTEMPTS:
        hits = jget("https://openaccess-api.clevelandart.org/api/artworks",
                    q="landscape", type="Painting", has_image=1,
                    limit=100, skip=random.randint(0, 5000)).get("data", [])
        random.shuffle(hits)
        for h in hits:
            att += 1; oid = str(h["id"])
            if att > MAX_ATTEMPTS or seen("cma", oid): break
            img = h.get("images", {}).get("web", {}).get("url")
            if img and (p := save_if_ok(fetch(img), h.get("title","untitled"), "cma", oid, w)):
                return p
    raise RuntimeError("CMA: exhausted")

# ── Backend registry ──────────────────────────────────────────────────────
BACKENDS: Dict[str, Callable[[Optional[bool]], Path]] = {
    fn._tag: fn for fn in (met_random, aic_random, cma_random)
}

# ── Imaging helpers (unchanged aside from bg arg) ─────────────────────────
def scale_cover(img: Image.Image) -> Image.Image:
    s = max(WIDTH / img.width, HEIGHT / img.height)
    n = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    l = (n.width - WIDTH) // 2; t = (n.height - HEIGHT) // 2
    return n.crop((l, t, l + WIDTH, t + HEIGHT))

def scale_fit(img: Image.Image, bg: str) -> Image.Image:
    s = min(WIDTH / img.width, HEIGHT / img.height)
    n = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), bg)
    canvas.paste(n, ((WIDTH - n.width) // 2, (HEIGHT - n.height) // 2)); return canvas

def display(path: Path, mode: str, bg: str):
    with Image.open(path) as raw:
        frame = scale_cover(raw) if mode == "fill" else scale_fit(raw, bg)
        if INKY: INKY.set_image(frame); INKY.show()
        else:
            preview = path.with_suffix(f".{mode}.preview.png")
            frame.save(preview); print("Preview →", preview)

# ── Offline fallback ──────────────────────────────────────────────────────
def local_cycle(w: Optional[bool]) -> Path:
    files = sorted(SAVE_DIR.glob("*.jpg"), key=lambda p: p.stat().st_atime)
    if not files: raise RuntimeError("No local images")
    for p in files:
        try:
            with Image.open(p) as im:
                if w is None or (im.width >= im.height) == w:
                    os.utime(p, None); return p
        except Exception: pass
    raise RuntimeError("Offline: orientation mismatch")

# ── CLI & main ────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Fetch/cycle landscape artworks")
    src = p.add_mutually_exclusive_group()
    for tag in BACKENDS: src.add_argument(f"--{tag}", action="store_true", help=f"only {tag.upper()}")
    ori = p.add_mutually_exclusive_group()
    ori.add_argument("--wide", action="store_true"); ori.add_argument("--tall", action="store_true")
    p.add_argument("--mode", choices=("fill", "fit"), default="fit",
                   help="fill=crop, fit=letterbox")
    p.add_argument("--white", action="store_true", help="white matte (default black)")
    return p.parse_args()

def main():
    a = parse_args()
    want = True if a.wide else False if a.tall else None
    bg = "white" if a.white else "black"
    chosen = [BACKENDS[t] for t in BACKENDS if getattr(a, t)] or list(BACKENDS.values())
    random.shuffle(chosen)

    for be in chosen:
        try:
            pic = be(want); display(pic, a.mode, bg)
            print(f"Saved → {pic}\nHTTP requests: {API_CALLS}"); return
        except Exception as e:
            print(f"[{be.__name__}] {e}", file=sys.stderr)

    try:
        pic = local_cycle(want); display(pic, a.mode, bg); print(f"(offline) {pic}")
    except Exception as e:
        traceback.print_exc(); sys.exit(f"Offline fallback failed: {e}")

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: sys.exit(1)
