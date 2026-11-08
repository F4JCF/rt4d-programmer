"""Mise à jour automatique depuis les releases GitHub du projet."""

from __future__ import annotations

import io
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional

from . import __version__
from .protocol import CancelCheck, Cancelled, Progress, RadioError

REPO = "F4JCF/rt4d-programmer"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
UA = {"User-Agent": "RT4D-Programmer-updater", "Accept": "application/vnd.github+json"}


@dataclass
class Release:
    version: str
    notes: str
    asset_url: Optional[str]
    asset_size: int
    page: str
    api_asset_url: Optional[str] = None


def _ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _openers(follow: bool = True):
    """Connexion directe d'abord, puis via le proxy éventuel du système."""
    https = urllib.request.HTTPSHandler(context=_ctx())
    extra = [] if follow else [_NoRedirect()]
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}), https, *extra)
    system = urllib.request.build_opener(https, *extra)
    return [("directe", direct), ("proxy système", system)]


def parse_version(v: str) -> tuple:
    v = v.strip().lstrip("vV")
    out = []
    for part in v.split("."):
        num = "".join(c for c in part if c.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def asset_name() -> str:
    return "RT4D-Programmer-Windows.zip" if sys.platform.startswith("win") else "RT4D-Programmer-Linux.zip"


def exe_name() -> str:
    return "RT4D-Programmer.exe" if sys.platform.startswith("win") else "RT4D-Programmer"


def can_self_update() -> bool:
    """Seulement pour l'exécutable compilé, dans un dossier où l'on peut écrire."""
    if not getattr(sys, "frozen", False):
        return False
    return os.access(os.path.dirname(os.path.abspath(sys.executable)), os.W_OK)


def _latest_from_api(opener, timeout) -> dict:
    req = urllib.request.Request(API_LATEST, headers=UA)
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _latest_from_redirect(opener, timeout) -> dict:
    """Sans l'API : github.com/…/releases/latest redirige vers …/releases/tag/vX.Y.Z."""
    req = urllib.request.Request(RELEASES_PAGE, headers={"User-Agent": UA["User-Agent"]})
    try:
        opener.open(req, timeout=timeout)
        raise IOError("pas de redirection")
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        if e.code not in (301, 302, 303, 307, 308) or "/tag/" not in loc:
            raise
    tag = loc.rsplit("/tag/", 1)[1]
    base = f"https://github.com/{REPO}/releases/download/{tag}/"
    return {"tag_name": tag, "body": "", "html_url": loc,
            "assets": [{"name": n, "browser_download_url": base + n, "size": 0, "url": None}
                       for n in ("RT4D-Programmer-Windows.zip", "RT4D-Programmer-Linux.zip")]}


def check_latest(timeout: float = 15) -> Optional[Release]:
    """Retourne la dernière release si elle est plus récente, sinon None."""
    errors = []
    data = None
    for how, fn, follow in (("API GitHub", _latest_from_api, True),
                            ("page GitHub", _latest_from_redirect, False)):
        for name, opener in _openers(follow):
            try:
                data = fn(opener, timeout)
                break
            except Exception as e:
                errors.append(f"{how} ({name}) : {e}")
        if data:
            break
    if data is None:
        raise IOError("GitHub ne répond pas.\n" + "\n".join(errors))
    tag = data.get("tag_name", "")
    if not tag or not is_newer(tag):
        return None
    url, size, api_url = None, 0, None
    for a in data.get("assets", []):
        if a.get("name") == asset_name():
            url, size = a.get("browser_download_url"), int(a.get("size") or 0)
            api_url = a.get("url")
    return Release(version=tag.lstrip("vV"), notes=data.get("body") or "", asset_url=url,
                   asset_size=size, page=data.get("html_url") or RELEASES_PAGE,
                   api_asset_url=api_url)


def _download(url: str, headers: dict, expected: int, progress: Progress, cancel: CancelCheck,
              opener=None) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    buf = bytearray()
    opener = opener or _openers()[0][1]
    with opener.open(req, timeout=90) as r:
        total = int(r.headers.get("Content-Length") or expected or 0)
        while True:
            if cancel and cancel():
                raise Cancelled("Mise à jour annulée.")
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            buf.extend(chunk)
            if progress:
                progress(len(buf) // 1024, max(total // 1024, len(buf) // 1024), "Téléchargement (Ko)")
    if expected and len(buf) != expected:
        raise IOError(f"fichier incomplet ({len(buf)} / {expected} octets)")
    return bytes(buf)


def download_and_install(rel: Release, progress: Progress = None, cancel: CancelCheck = None,
                         log: Callable[[str], None] = print) -> str:
    """Télécharge la nouvelle version et remplace l'exécutable. Retourne son chemin."""
    if not rel.asset_url:
        raise RadioError("Cette version ne contient pas de fichier pour votre système.")
    if not can_self_update():
        raise RadioError("Impossible de remplacer le programme dans son dossier actuel "
                         "(droits insuffisants).")
    log(f"Téléchargement de la version {rel.version}…")
    sources = [(rel.asset_url, {"User-Agent": UA["User-Agent"]})]
    if rel.api_asset_url:
        sources.append((rel.api_asset_url, {"User-Agent": UA["User-Agent"],
                                            "Accept": "application/octet-stream"}))
    openers = _openers()
    plan = [(u, h, o) for u, h in sources for _, o in openers]
    buf = None
    last = None
    for attempt in range(max(4, len(plan))):
        url, headers, opener = plan[attempt % len(plan)]
        try:
            buf = _download(url, headers, rel.asset_size, progress, cancel, opener)
            break
        except Cancelled:
            raise
        except Exception as e:
            last = e
            wait = 3 * (attempt + 1)
            log(f"Essai {attempt + 1} échoué ({e}). Nouvel essai dans {wait} s…")
            for _ in range(wait * 10):
                if cancel and cancel():
                    raise Cancelled("Mise à jour annulée.")
                time.sleep(0.1)
    if buf is None:
        raise RadioError(f"Téléchargement impossible après plusieurs essais : {last}\n\n"
                         f"Réessayez plus tard, ou téléchargez la version à la main :\n{rel.page}")
    try:
        with zipfile.ZipFile(io.BytesIO(bytes(buf))) as z:
            member = next((n for n in z.namelist() if os.path.basename(n) == exe_name()), None)
            if not member:
                raise RadioError("Le programme est absent de l'archive téléchargée.")
            new = z.read(member)
    except zipfile.BadZipFile as e:
        raise RadioError("L'archive téléchargée est endommagée.") from e
    if sys.platform.startswith("win") and new[:2] != b"MZ":
        raise RadioError("Le fichier téléchargé n'est pas un programme Windows valide.")
    if not sys.platform.startswith("win") and new[:4] != b"\x7fELF":
        raise RadioError("Le fichier téléchargé n'est pas un programme Linux valide.")
    return install_binary(new, os.path.abspath(sys.executable), log)


def install_binary(new: bytes, target: str, log: Callable[[str], None] = print) -> str:
    """Remplace `target` par `new` (le programme en cours peut être renommé, pas écrasé)."""
    folder = os.path.dirname(target)
    tmp = os.path.join(folder, os.path.basename(target) + ".nouveau")
    with open(tmp, "wb") as f:
        f.write(new)
    if not sys.platform.startswith("win"):
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)
    else:
        old = target[:-4] + ".ancien.exe" if target.lower().endswith(".exe") else target + ".ancien"
        try:
            if os.path.exists(old):
                os.remove(old)
        except OSError:
            pass
        os.replace(target, old)              # un .exe en cours d'exécution peut être renommé
        try:
            os.replace(tmp, target)
        except OSError:
            os.replace(old, target)          # retour à l'état initial
            raise
    log("Nouvelle version installée.")
    return target


def restart(path: str):
    """Lance la nouvelle version (l'appelant doit ensuite quitter l'application)."""
    env = dict(os.environ)
    env.pop("_MEIPASS2", None)
    env.pop("_PYI_APPLICATION_HOME_DIR", None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    kw = {}
    if sys.platform.startswith("win"):
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen([path], env=env, close_fds=True, **kw)


def cleanup_old():
    """Supprime la copie de l'ancienne version laissée après une mise à jour (Windows)."""
    if not getattr(sys, "frozen", False):
        return
    exe = os.path.abspath(sys.executable)
    for old in (exe[:-4] + ".ancien.exe" if exe.lower().endswith(".exe") else exe + ".ancien",
                exe + ".nouveau"):
        try:
            if os.path.exists(old):
                os.remove(old)
        except OSError:
            pass
