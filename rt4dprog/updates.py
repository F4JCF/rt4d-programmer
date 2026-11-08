"""Vérification des mises à jour sur GitHub (releases du dépôt)."""

from __future__ import annotations

import json
import ssl
import sys
import urllib.request
from typing import Optional

REPO = "F4JCF/rt4d-programmer"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


def parse_version(text: str) -> tuple:
    t = text.strip().lstrip("vV")
    parts = []
    for p in t.split("."):
        num = "".join(c for c in p if c.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def _context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def latest_release(timeout: float = 8.0) -> Optional[dict]:
    """{'version': '0.4.0', 'page': url, 'download': url du zip de ce système} ou None."""
    req = urllib.request.Request(API_LATEST, headers={
        "User-Agent": "RT4D-Programmer", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, context=_context(), timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    tag = data.get("tag_name") or ""
    if not tag:
        return None
    want = "Windows" if sys.platform.startswith("win") else "Linux"
    download = next((a.get("browser_download_url") for a in data.get("assets", [])
                     if want in a.get("name", "")), None)
    return {"version": tag.lstrip("vV"), "page": data.get("html_url") or RELEASES_PAGE,
            "download": download}
