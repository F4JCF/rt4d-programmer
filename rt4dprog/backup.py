"""Sauvegarde automatique sur Nextcloud (ou tout serveur WebDAV : kDrive, ownCloud…).

Même principe que CAT Pilot. Dans un dossier du serveur (« RT4D-Programmer » par défaut) :
  sauvegardes/          copie du dossier des sauvegardes du logiciel : sauvegardes complètes
                        de la flash (4 Mo, avec la calibration), états de la radio avant chaque
                        écriture, anciennes polices. Seuls les fichiers nouveaux ou modifiés
                        sont envoyés ; rien n'est jamais effacé sur le serveur.
  codeplug/             le codeplug ouvert (fichier enregistré), plus une copie datée par jour
                        dans codeplug/historique.
  reglages.json         les réglages du logiciel (sans le mot de passe).
Une fois par jour au démarrage, après chaque nouvelle sauvegarde, ou à la demande.
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, QTimer, Signal

from . import __version__

DEFAULT_FOLDER = "RT4D-Programmer"
SECRET = ("pass", "pswd", "password", "api", "token")
SKIP_SUFFIX = (".partiel", ".tmp", "~")


def dav_base(url: str, user: str, folder: str = "") -> str:
    """Adresse WebDAV du dossier. Accepte l'adresse du Nextcloud (https://cloud.exemple.fr,
    http://192.168.1.16:8080) ou directement une adresse WebDAV complète."""
    url = url.strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    if "/remote.php/" not in url and "/dav" not in url and "webdav" not in url.lower():
        url = f"{url}/remote.php/dav/files/{urllib.parse.quote(user.strip())}"
    for part in [p for p in folder.strip("/").split("/") if p]:
        url += "/" + urllib.parse.quote(part)
    return url


def _stamp(path: str) -> str:
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


def scan_backups(root: str, index: dict) -> list[tuple[str, str, str]]:
    """Fichiers du dossier des sauvegardes à envoyer : (chemin relatif « / », chemin, empreinte).
    Un fichier déjà envoyé (même taille, même date) est ignoré."""
    out = []
    if not root or not os.path.isdir(root):
        return out
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for name in sorted(files):
            if name.startswith(".") or name.endswith(SKIP_SUFFIX):
                continue
            path = os.path.join(d, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                st = _stamp(path)
            except OSError:
                continue
            if index.get(rel) != st:
                out.append((rel, path, st))
    return out


def settings_json(settings) -> bytes:
    cfg = {}
    for k in settings.allKeys():
        if any(x in k.lower() for x in SECRET) or k.startswith("nc_index"):
            continue
        v = settings.value(k)
        if isinstance(v, (str, int, float, bool)) or v is None:
            cfg[k] = v
    return json.dumps(cfg, ensure_ascii=False, indent=1).encode("utf-8")


def _ssl_context():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class Backup(QObject):
    status = Signal(str)
    _sent = Signal(str, dict, str)      # cible, nouvelles entrées de l'index, date du jour (fil principal)
    _over = Signal()

    def __init__(self, win):
        super().__init__()
        self.win, self.s = win, win.settings
        self.busy = False
        self.again = False
        self._sent.connect(self._store_index)
        self._over.connect(self._finished)
        QTimer.singleShot(20000, self.auto)

    # -- réglages --
    def configured(self) -> bool:
        return all((self.s.value(k, "") or "").strip() for k in ("nc_url", "nc_user", "nc_pass"))

    def folder(self) -> str:
        return (self.s.value("nc_folder", DEFAULT_FOLDER) or DEFAULT_FOLDER).strip("/ ")

    def target(self) -> str:
        return f"{self.s.value('nc_url', '')}|{self.s.value('nc_user', '')}|{self.folder()}"

    def index(self) -> dict:
        """Fichiers déjà envoyés. Remis à zéro si l'adresse, l'utilisateur ou le dossier change."""
        if self.s.value("nc_index_target", "") != self.target():
            return {}
        try:
            return json.loads(self.s.value("nc_index", "{}") or "{}")
        except ValueError:
            return {}

    def reset_index(self):
        self.s.setValue("nc_index", "{}")

    def _store_index(self, target, new, today):
        idx = self.index() if self.s.value("nc_index_target", "") == target else {}
        idx.update(new)
        self.s.setValue("nc_index_target", target)
        self.s.setValue("nc_index", json.dumps(idx))
        if today:
            self.s.setValue("nc_last", today)
        self.s.sync()

    # -- déclenchement --
    def auto(self):
        """Au démarrage : une fois par jour si l'option est cochée."""
        if not self.configured() or self.s.value("nc_daily", "true") not in (True, "true"):
            return
        if self.s.value("nc_last", "") == datetime.date.today().isoformat():
            return
        self.run()

    def after_backup(self):
        """Juste après une nouvelle sauvegarde (écriture, sauvegarde complète, police)."""
        if self.configured() and self.s.value("nc_after", "true") in (True, "true"):
            self.run()

    def run(self):
        if self.busy:
            self.again = True              # une sauvegarde arrivée pendant l'envoi partira juste après
            return
        if not self.configured():
            return
        self.busy = True
        cp_path = self.win.path if self.win.path and os.path.isfile(self.win.path) else None
        job = dict(
            url=self.s.value("nc_url", ""), user=self.s.value("nc_user", ""),
            pwd=self.s.value("nc_pass", ""), folder=self.folder(), target=self.target(),
            todo=scan_backups(self.win.backup_dir(), self.index()),
            cfg=settings_json(self.s), codeplug=cp_path,
        )
        threading.Thread(target=self._run, args=(job,), daemon=True).start()

    # -- WebDAV --
    def _req(self, job, method, url, data=None):
        tok = base64.b64encode(f"{job['user'].strip()}:{job['pwd']}".encode()).decode()
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Basic {tok}", "User-Agent": f"RT4D-Programmer/{__version__}"})
        try:
            with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def _mkdirs(self, job, base, rel_dir, made):
        url = base
        for part in [p for p in rel_dir.split("/") if p]:
            url += "/" + urllib.parse.quote(part)
            if url in made:
                continue
            code = self._req(job, "MKCOL", url)
            if code in (401, 403):
                raise RuntimeError("identifiant ou mot de passe d'application refusé")
            if code not in (201, 405, 301):            # 405 : le dossier existe déjà
                raise RuntimeError(f"création du dossier « {part} » refusée (code {code})")
            made.add(url)
        return url

    def _put(self, job, url, data, name):
        code = self._req(job, "PUT", url, data)
        if code in (401, 403):
            raise RuntimeError("identifiant ou mot de passe d'application refusé")
        if code not in (200, 201, 204):
            raise RuntimeError(f"envoi de {name} refusé (code {code})")

    def _run(self, job):
        sent = {}
        n = 0
        try:
            self.status.emit("Sauvegarde Nextcloud en cours…")
            root = dav_base(job["url"], job["user"])
            made = set()
            base = self._mkdirs(job, root, job["folder"], made)

            # 1. dossier des sauvegardes (seulement les nouveaux fichiers)
            for i, (rel, path, st) in enumerate(job["todo"], 1):
                self.status.emit(f"Sauvegarde Nextcloud : {rel} ({i}/{len(job['todo'])})…")
                d = self._mkdirs(job, base, "sauvegardes/" + rel.rpartition("/")[0], made)
                with open(path, "rb") as f:
                    self._put(job, d + "/" + urllib.parse.quote(rel.rpartition("/")[2]), f.read(), rel)
                sent[rel] = st
                n += 1

            # 2. codeplug ouvert + copie datée du jour
            today = datetime.date.today().isoformat()
            if job["codeplug"]:
                name = os.path.basename(job["codeplug"])
                with open(job["codeplug"], "rb") as f:
                    data = f.read()
                d = self._mkdirs(job, base, "codeplug/historique", made)
                self._put(job, base + "/codeplug/" + urllib.parse.quote(name), data, name)
                stem, ext = os.path.splitext(name)
                self._put(job, d + "/" + urllib.parse.quote(f"{stem}_{today}{ext}"), data, name)
                n += 1

            # 3. réglages
            self._put(job, base + "/reglages.json", job["cfg"], "reglages.json")
            n += 1
            self._sent.emit(job["target"], sent, today)
            self.status.emit(f"Sauvegarde Nextcloud faite : {n} fichier(s) envoyé(s) dans « {job['folder']} »")
        except Exception as e:
            if sent:
                self._sent.emit(job["target"], sent, "")
            msg = str(e)
            if isinstance(e, urllib.error.URLError):
                msg = f"serveur injoignable ({e.reason})"
            self.status.emit(f"Sauvegarde Nextcloud impossible : {msg}")
        finally:
            self._over.emit()

    def _finished(self):
        self.busy = False
        if self.again:
            self.again = False
            self.run()
