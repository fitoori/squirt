#!/usr/bin/env python3
"""
landscapes.py – fetch an unseen landscape painting (Met or AIC)
and show it on a 7-colour Inky display, cropped full-screen.

Usage:
  ./landscape_inky.py           # random Met or AIC, any orientation
  ./landscape_inky.py --wide    # landscape (width ≥ height) only
  ./landscape_inky.py --tall    # portrait (height > width) only
  ./landscape_inky.py --met     # Met only (add --wide/--tall if desired)
  ./landscape_inky.py --aic     # AIC only
  ./landscape_inky.py --reset   # clear seen.json then exit

Every object ID is recorded in seen.json, even if orientation-rejected,
so you’ll never see the same painting twice.
"""

from __future__ import annotations
import argparse, io, json, os, random, re, subprocess, sys, time, requests
from pathlib import Path
from typing import Dict, List
from PIL import Image, UnidentifiedImageError

# ───── configuration ─────────────────────────────────────────────────────────
CACHE   = Path("seen.json")
STATIC  = Path("static"); STATIC.mkdir(exist_ok=True)
TIMEOUT = 15
HEADLESS_RES       = (1600, 1200)
FALLBACK_INKY_CLASS = "InkyImpressions73"   # change if your panel is different
MAX_ATTEMPTS       = 30                     # tries per back-end to match orientation

# ───── HTTP helper w/ request counter ────────────────────────────────────────
session = requests.Session()
session.headers["User-Agent"] = "LandscapeInky/1.3 (+github)"
API_CALLS = 0

def jget(url: str, **params) -> dict:
    global API_CALLS; API_CALLS += 1
    r = session.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def fetch(url: str) -> bytes:
    global API_CALLS; API_CALLS += 1
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content

# ───── seen-cache helpers ────────────────────────────────────────────────────
def load_seen() -> Dict[str, List[str]]:
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}

SEEN: Dict[str, List[str]] = load_seen()

def seen(museum: str, oid: str) -> bool:
    return oid in SEEN.get(museum, [])

def mark_seen(museum: str, oid: str) -> None:
    SEEN.setdefault(museum, []).append(oid)
    CACHE.write_text(json.dumps(SEEN, indent=2))

def slug(text: str, maxlen: int = 60) -> str:
    txt = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return (txt[:maxlen] or "untitled").lower()

def save_if_ok(img_bytes: bytes,
               title: str,
               museum: str,
               oid: str,
               want_wide: bool|None) -> Path|None:
    """
    Decode the bytes to check orientation. If matches want_wide (or
    want_wide is None), save + mark_seen, and return Path.
    Otherwise mark_seen and return None.
    """
    try:
        with Image.open(io.BytesIO(img_bytes)) as im:
            wide = im.width >= im.height
            if want_wide is None or want_wide == wide:
                fn = STATIC / f"{slug(title)}_{museum}_{oid}.jpg"
                if not fn.exists():
                    fn.write_bytes(img_bytes)
                mark_seen(museum, oid)
                return fn
    except UnidentifiedImageError:
        pass

    # reject mismatch or unreadable but still mark seen
    mark_seen(museum, oid)
    return None

# ───── Met backend ───────────────────────────────────────────────────────────
def met_random(want_wide: bool|None) -> Path:
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

        pic = save_if_ok(
            fetch(url),
            obj.get("title", f"met_{oid}"),
            "met",
            str(oid),
            want_wide
        )
        if pic:
            return pic

        attempts += 1

    raise RuntimeError("Met: no unseen painting matched the requested orientation")

# ───── AIC backend (retry, safe pages) ───────────────────────────────────────
def aic_random(want_wide: bool|None) -> Path:
    base = "https://www.artic.edu/iiif/2"
    attempts = 0

    for _ in range(MAX_ATTEMPTS):
        if attempts >= MAX_ATTEMPTS:
            break
        page = random.randint(1, 50)
        try:
            hits = jget(
                "https://api.artic.edu/api/v1/artworks/search",
                q="landscape",
                fields="id,title,image_id",
                page=page,
                limit=100
            )["data"]
        except requests.HTTPError:
            continue

        random.shuffle(hits)
        for h in hits:
            oid, img_id = str(h["id"]), h["image_id"]
            if not img_id or seen("aic", oid):
                continue

            url = f"{base}/{img_id}/full/843,/0/default.jpg"
            pic = save_if_ok(
                fetch(url),
                h.get("title", f"aic_{oid}"),
                "aic",
                oid,
                want_wide
            )
            if pic:
                return pic

            attempts += 1

    raise RuntimeError("AIC: no unseen painting matched the requested orientation")

# ───── Inky initialisation ───────────────────────────────────────────────────
def init_inky():
    try:
        import inky, numpy  # noqa: F401
    except ModuleNotFoundError:
        subprocess.run([
            sys.executable, "-m", "pip", "install", "--quiet", "--user",
            "inky>=2.1.0", "numpy"
        ], check=False)
        import inky  # noqa: F401

    try:
        from inky.auto import auto
        dev = auto()
        return dev, *dev.resolution
    except Exception:
        try:
            mod = __import__("inky.impressions", fromlist=[FALLBACK_INKY_CLASS])
            cls = getattr(mod, FALLBACK_INKY_CLASS)
            dev = cls()
            return dev, *dev.resolution
        except Exception as e:
            print("Inky unavailable (headless):", e, file=sys.stderr)
            return None, *HEADLESS_RES

inky, WIDTH, HEIGHT = init_inky()

# ───── image fit = cover crop ─────────────────────────────────────────────────
def fit(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    # scale to overfill at least one dimension
    scale = max(WIDTH / img.width, HEIGHT / img.height)
    new = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    # centre-crop to exact panel
    left = (new.width - WIDTH) // 2
    top  = (new.height - HEIGHT) // 2
    return new.crop((left, top, left + WIDTH, top + HEIGHT))

# ───── display (robust, headless preview) ────────────────────────────────────
def display(path: Path):
    try:
        with Image.open(path) as raw:
            frame = fit(raw)
            if inky:
                try:
                    inky.set_image(frame)  # let device handle its palette
                    inky.show()
                except Exception as e:
                    print("Inky error:", e, file=sys.stderr)
                    raise
            else:
                raise RuntimeError("headless")
    except Exception:
        # fallback preview
        preview = path.with_suffix(".preview.png")
        with Image.open(path) as raw:
            fit(raw).save(preview)
        print("Headless preview →", preview)

# ───── CLI & main ────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Show an unseen landscape painting")
    group_src = p.add_mutually_exclusive_group()
    group_src.add_argument("--met", action="store_true", help="only from The Met")
    group_src.add_argument("--aic", action="store_true", help="only from AIC")

    group_ori = p.add_mutually_exclusive_group()
    group_ori.add_argument("--wide", action="store_true", help="landscape only (w ≥ h)")
    group_ori.add_argument("--tall", action="store_true", help="portrait only (h > w)")

    p.add_argument("--reset", action="store_true", help="clear seen.json then exit")
    return p.parse_args()

def main():
    args = parse_args()
    if args.reset:
        CACHE.unlink(missing_ok=True)
        print("seen.json cleared")
        return

    want_wide: bool|None = None
    if args.wide:
        want_wide = True
    elif args.tall:
        want_wide = False

    random.seed(time.time_ns())
    backends = (
        [met_random] if args.met else
        [aic_random] if args.aic else
        [met_random, aic_random]
    )
    random.shuffle(backends)

    for be in backends:
        try:
            pic = be(want_wide)
            print("Saved →", pic)
            display(pic)
            print("HTTP requests this run:", API_CALLS)
            return
        except Exception as e:
            print(f"[{be.__name__}] {e}", file=sys.stderr)

    sys.exit("All back-ends failed")

if __name__ == "__main__":
    main()
