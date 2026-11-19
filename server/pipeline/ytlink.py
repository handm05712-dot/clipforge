"""Parse the link shapes people actually paste.

Kept pure (no network, no yt-dlp) so the rules are unit-testable: one bad
regex here would send every job down the wrong path.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_HOSTS = {
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "m.youtube.com": "youtube",
    "music.youtube.com": "youtube",
    "youtu.be": "short",
    "www.youtu.be": "short",
    "youtube-nocookie.com": "embed",
    "www.youtube-nocookie.com": "embed",
}

_PATH_PREFIXES = ("shorts", "live", "embed", "v", "e")


class BadLink(ValueError):
    """The text is not a YouTube link we can work with."""


def parse(url: str) -> dict:
    """Return {video_id, kind, canonical, start_at}.

    kind is one of: watch, short, live, embed, playlist_only.
    `canonical` is a plain watch URL, so the rest of the app never has to care
    whether the user pasted a Shorts or a /live/ link.
    """
    raw = (url or "").strip()
    if not raw:
        raise BadLink("Paste a link first.")
    if not re.match(r"^https?://", raw, re.I):
        # bare "youtu.be/ID" or "youtube.com/watch?v=ID" is common when copied
        raw = "https://" + raw.lstrip("/")
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise BadLink("That does not look like a URL.") from exc

    host = (parsed.netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host_family = _HOSTS.get(host) or _HOSTS.get(host[4:])
    else:
        host_family = _HOSTS.get(host)
    if not host_family:
        raise BadLink(
            "That link is not YouTube. Paste a YouTube video, Shorts, or livestream link."
        )

    parts = [p for p in (parsed.path or "").split("/") if p]
    query = parse_qs(parsed.query or "")

    video_id = ""
    kind = "watch"
    if host_family == "short":
        video_id = parts[0] if parts else ""
        kind = "short"
    elif parts and parts[0] in _PATH_PREFIXES:
        video_id = parts[1] if len(parts) > 1 else ""
        kind = {"shorts": "short", "live": "live", "embed": "embed"}.get(parts[0], "watch")
    else:
        video_id = (query.get("v") or [""])[0]

    if not video_id:
        if query.get("list"):
            raise BadLink(
                "That is a playlist link. Open one video in it and paste that video's link."
            )
        raise BadLink("I could not find a video ID in that link.")

    if not _ID_RE.match(video_id):
        raise BadLink("That video ID looks malformed. Copy the link again from YouTube.")

    start_at = 0.0
    for key in ("t", "start"):
        value = (query.get(key) or [""])[0]
        if value:
            start_at = _parse_time(value)
            break

    return {
        "video_id": video_id,
        "kind": kind,
        "canonical": f"https://www.youtube.com/watch?v={video_id}",
        "start_at": start_at,
    }


def _parse_time(value: str) -> float:
    """YouTube uses 90, 1m30s and 1h2m3s in `?t=`."""
    value = value.strip().lower()
    if value.isdigit():
        return float(value)
    total = 0.0
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)([hms])", value):
        total += float(amount) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def watch_url(video_id: str, at: float | None = None) -> str:
    base = f"https://www.youtube.com/watch?v={video_id}"
    if at and at > 0:
        return f"{base}&t={int(at)}s"
    return base
