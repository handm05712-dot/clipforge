"""Font library: bundled fonts, uploaded .ttf/.otf, and Google Fonts downloads."""
from __future__ import annotations

import re
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

from fontTools.ttLib import TTFont

from .. import config

FONT_EXTS = {".ttf", ".otf", ".ttc"}


def _family_name(path: Path) -> str | None:
    try:
        font = TTFont(str(path), fontNumber=0, lazy=True)
        name_table = font["name"]
        for name_id in (1, 4):
            rec = name_table.getDebugName(name_id)
            if rec:
                font.close()
                return rec
        font.close()
    except Exception:
        return None
    return None


def _safe(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return base or "font"


def scan_dirs() -> list[Path]:
    out: list[Path] = []
    for directory in (config.BUNDLED_FONTS, config.FONTS):
        if directory.exists():
            out.extend(sorted(p for p in directory.iterdir() if p.suffix.lower() in FONT_EXTS))
    return out


def list_fonts() -> list[dict]:
    fonts = []
    seen = set()
    for path in scan_dirs():
        family = _family_name(path) or path.stem
        key = family.lower()
        if key in seen:
            continue
        seen.add(key)
        fonts.append({"family": family, "file": path.name,
                      "path": str(path), "uploaded": config.FONTS in path.parents})
    for fallback in ("DejaVu Sans", "Liberation Sans", "Noto Sans", "Noto Color Emoji"):
        if fallback.lower() not in seen:
            fonts.append({"family": fallback, "file": None, "path": None, "uploaded": False})
    return sorted(fonts, key=lambda f: f["family"].lower())


def system_font_path(family: str) -> Path | None:
    try:
        import subprocess
        res = subprocess.run(["fc-match", "-f", "%{file}", family], capture_output=True,
                             text=True, timeout=10)
        p = Path(res.stdout.strip())
        return p if res.returncode == 0 and p.exists() else None
    except Exception:
        return None


def usable_path(family: str) -> Path | None:
    for f in list_fonts():
        if f["family"].lower() == family.lower() and f["path"]:
            return Path(f["path"])
    return system_font_path(family)


def save_upload(filename: str, data: bytes) -> dict:
    ext = Path(filename).suffix.lower()
    if ext not in FONT_EXTS:
        raise ValueError("Only .ttf, .otf and .ttc font files are supported.")
    if len(data) > 40 * 1024 * 1024:
        raise ValueError("Font file is too large (max 40 MB).")
    dest = config.FONTS / f"{_safe(Path(filename).stem)}{ext}"
    dest.write_bytes(data)
    family = _family_name(dest)
    if not family:
        # not a usable font file
        dest.unlink(missing_ok=True)
        raise ValueError("That file does not look like a valid font.")
    return {"family": family, "file": dest.name, "path": str(dest), "uploaded": True}


def delete_font(filename: str) -> bool:
    target = config.FONTS / _safe(Path(filename).stem)
    for p in config.FONTS.glob(target.name + ".*"):
        p.unlink(missing_ok=True)
        return True
    return False


def download_google_font(family: str) -> dict:
    """Fetch a Google Font family (all weights available as static TTF)."""
    family = family.strip()
    if not family:
        raise ValueError("Missing font family name.")
    css_url = (
        "https://fonts.googleapis.com/css2?"
        + urllib.parse.urlencode({"family": family + ":wght@400;700;900", "display": "swap"})
    )
    req = urllib.request.Request(css_url, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(req, timeout=30) as res:
        css = res.read().decode("utf-8", "replace")
    urls = re.findall(r"url\((https://[^)]+\.(?:ttf|otf|woff2))\)", css)
    if not urls:
        raise ValueError(f"No downloadable font found for '{family}'.")
    # prefer ttf/otf sources (the curl UA usually yields ttf)
    urls.sort(key=lambda u: (u.endswith(".woff2"), u))
    chosen = urls[0]
    if chosen.endswith(".woff2"):
        raise ValueError("Google returned woff2 only; try another family.")
    data_req = urllib.request.Request(chosen, headers={"User-Agent": "curl/8.0"})
    with urllib.request.urlopen(data_req, timeout=60) as res:
        data = res.read()
    return save_upload(Path(urllib.parse.urlparse(chosen).path).name or f"{family}.ttf", data)


def ensure_installed() -> Path:
    """Return a stable fontsdir for libass.

    Bundled/uploaded fonts are referenced in place via a small directory of
    relative symlinks (no copies), which keeps the workspace tiny.
    """
    d = config.DATA / "fontdir"
    d.mkdir(parents=True, exist_ok=True)
    wanted = {p.resolve() for p in scan_dirs()}
    try:
        emoji = system_font_path("Noto Color Emoji")
        if emoji:
            wanted.add(emoji.resolve())
    except Exception:
        pass
    existing = set()
    for link in d.iterdir():
        if link.is_symlink() and link.resolve() in wanted:
            existing.add(link.resolve())
        else:
            link.unlink(missing_ok=True)
    for target in wanted - existing:
        link = d / target.name
        if link.exists() or link.is_symlink():
            continue
        try:
            link.symlink_to(target)
        except OSError:
            try:
                shutil.copy2(target, link)
            except OSError:
                pass
    return d


def font_dir() -> Path:
    return ensure_installed()


def ensure_font_file(style_font: str) -> Path | None:
    """Make sure libass can find the font; returns the local file if we have one."""
    p = usable_path(style_font)
    if p:
        return p
    try:
        info = download_google_font(style_font)
        return Path(info["path"])
    except Exception as exc:
        print(f"[fonts] could not resolve '{style_font}': {exc}")
        return None
