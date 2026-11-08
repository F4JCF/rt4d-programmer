"""Vérification et installation des mises à jour depuis les « Releases » GitHub.

Même fonctionnement que CAT Pilot : chaque version publiée porte le fichier
RT4D-Programmer.exe (Windows) ou RT4D-Programmer-Linux (Linux). Le logiciel
compare son numéro de version à la dernière publication, télécharge le nouvel
exécutable, vérifie son empreinte SHA-256, puis un petit script remplace
l'ancien fichier une fois le logiciel fermé et le relance.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, Signal

from . import __version__

REPO = "F4JCF/rt4d-programmer"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET = "RT4D-Programmer.exe" if sys.platform == "win32" else "RT4D-Programmer-Linux"
HEADERS = {"User-Agent": f"RT4D-Programmer/{__version__}", "Accept": "application/vnd.github+json"}


def _ssl_context():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _urlopen(req, timeout):
    """Comme le navigateur : proxy du système s'il y en a un."""
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def parse_version(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def is_newer(remote, local=__version__):
    return parse_version(remote) > parse_version(local)


def can_self_update():
    """Remplacement automatique de l'exécutable compilé, si son dossier est modifiable."""
    if not getattr(sys, "frozen", False):
        return False
    return os.access(os.path.dirname(os.path.abspath(sys.executable)), os.W_OK)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _info_from_api() -> dict:
    req = urllib.request.Request(API, headers=HEADERS)
    with _urlopen(req, 15) as r:
        rel = json.loads(r.read().decode("utf-8"))
    asset = next((a for a in rel.get("assets", []) if a.get("name") == ASSET), None)
    return {"version": rel.get("tag_name", "").lstrip("v"), "title": rel.get("name") or "",
            "notes": rel.get("body") or "", "page": rel.get("html_url") or PAGE,
            "url": asset and asset.get("browser_download_url"),
            "size": asset and asset.get("size"),
            "sha256": (asset.get("digest") or "").replace("sha256:", "") if asset else ""}


def _info_from_page() -> dict:
    """Secours si l'API GitHub est indisponible : …/releases/latest redirige vers …/tag/vX.Y.Z."""
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_context()),
                                         _NoRedirect())
    req = urllib.request.Request(PAGE, headers={"User-Agent": HEADERS["User-Agent"]})
    try:
        opener.open(req, timeout=15)
        raise RuntimeError("réponse inattendue de GitHub")
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        if e.code not in (301, 302, 303, 307, 308) or "/tag/" not in loc:
            raise
    tag = loc.rsplit("/tag/", 1)[1]
    return {"version": tag.lstrip("v"), "title": "", "notes": "", "page": loc,
            "url": f"https://github.com/{REPO}/releases/download/{tag}/{ASSET}",
            "size": 0, "sha256": ""}


class Updater(QObject):
    checked = Signal(object, bool)        # infos de la dernière version (ou exception), vérification manuelle ?
    progress = Signal(int, int)           # octets reçus, total
    downloaded = Signal(object)           # chemin du fichier téléchargé (ou exception)

    def __init__(self):
        super().__init__()
        self._cancel = False

    # --- vérification -------------------------------------------------------------------
    def check(self, manual=False):
        threading.Thread(target=self._check, args=(manual,), daemon=True).start()

    def _check(self, manual):
        errors = []
        for fn in (_info_from_api, _info_from_page):
            try:
                self.checked.emit(fn(), manual)
                return
            except urllib.error.HTTPError as e:
                errors.append("aucune version publiée n'est accessible" if e.code == 404
                              else f"erreur HTTP {e.code}")
            except Exception as e:
                errors.append(str(e))
        self.checked.emit(RuntimeError(" / ".join(dict.fromkeys(errors))), manual)

    # --- téléchargement -----------------------------------------------------------------
    def download(self, info):
        self._cancel = False
        threading.Thread(target=self._download, args=(info,), daemon=True).start()

    def cancel(self):
        self._cancel = True

    def _download(self, info):
        last = None
        for attempt in range(3):
            try:
                self.downloaded.emit(self._download_once(info))
                return
            except Exception as e:
                last = e
                if self._cancel:
                    break
        self.downloaded.emit(last)

    def _download_once(self, info):
        ext = ".exe" if sys.platform == "win32" else ""
        dest = os.path.join(tempfile.gettempdir(), f"RT4D-Programmer_{info['version']}{ext}")
        req = urllib.request.Request(info["url"], headers={"User-Agent": HEADERS["User-Agent"]})
        h = hashlib.sha256()
        with _urlopen(req, 60) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or info.get("size") or 0)
            done = 0
            while True:
                if self._cancel:
                    raise RuntimeError("téléchargement annulé")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                self.progress.emit(done, total)
        if total and done != total:
            raise RuntimeError("fichier incomplet")
        if info.get("sha256") and h.hexdigest().lower() != info["sha256"].lower():
            raise RuntimeError("l'empreinte du fichier ne correspond pas : téléchargement corrompu")
        with open(dest, "rb") as f:
            head = f.read(4)
        if (sys.platform == "win32" and head[:2] != b"MZ") or \
                (sys.platform != "win32" and head != b"\x7fELF"):
            raise RuntimeError("le fichier téléchargé n'est pas un programme valide")
        return dest


def clean_env():
    """Environnement pour lancer une NOUVELLE instance (sinon PyInstaller réutilise
    le dossier temporaire de l'instance qui se ferme et s'arrête sans rien afficher)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_MEI", "_PYI"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def spawn_detached(cmd):
    """Lance un processus indépendant, qui survit à la fermeture du logiciel."""
    if sys.platform != "win32":
        subprocess.Popen(cmd, env=clean_env(), start_new_session=True, close_fds=True)
        return
    base = 0x08000000 | 0x00000200          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:                                    # hors du « job » de l'application si Windows l'autorise
        subprocess.Popen(cmd, env=clean_env(), creationflags=base | 0x01000000, close_fds=True)
    except OSError:
        subprocess.Popen(cmd, env=clean_env(), creationflags=base, close_fds=True)


def _wait_lines():
    """Attend la fin de ce processus et de son lanceur PyInstaller (qui verrouille l'exe)."""
    image = os.path.basename(sys.executable)
    out = "@echo off\n"
    for pid in sorted({os.getpid(), os.getppid()}):
        out += (f":attente{pid}\n"
                f'tasklist /FI "PID eq {pid}" /FI "IMAGENAME eq {image}" 2>nul | find " {pid} " >nul && '
                f"(ping -n 2 127.0.0.1 >nul & goto attente{pid})\n")
    return out + "ping -n 2 127.0.0.1 >nul\n"


def install_and_restart(new_exe):
    """Lance le script qui attend la fermeture, remplace l'exécutable et le relance."""
    exe = os.path.abspath(sys.executable)
    if sys.platform != "win32":
        os.chmod(new_exe, 0o755)
        spawn_detached(["sh", "-c", f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done\n"
                        f'mv -f "{new_exe}" "{exe}" && chmod +x "{exe}"\nexec "{exe}"'])
        return
    bat = os.path.join(tempfile.gettempdir(), "rt4dprog_maj.bat")
    with open(bat, "w", encoding="mbcs") as f:
        f.write(_wait_lines() + f"""set essai=0
:copie
move /y "{new_exe}" "{exe}" >nul 2>&1 && goto relance
set /a essai+=1
if %essai% lss 20 (ping -n 2 127.0.0.1 >nul & goto copie)
:relance
start "" "{exe}"
del "%~f0"
""")
    spawn_detached(["cmd", "/c", bat])


def cleanup_old():
    """Supprime les restes des anciennes méthodes de mise à jour (versions 0.4 à 0.7)."""
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
