"""Sauvegarde Nextcloud : adresse WebDAV, choix des fichiers, envoi vers un faux serveur WebDAV."""
import base64
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QSettings  # noqa: E402

from rt4dprog import backup as B  # noqa: E402


def test_dav_base():
    assert B.dav_base("cloud.exemple.fr", "jm", "RT4D-Programmer") == \
        "https://cloud.exemple.fr/remote.php/dav/files/jm/RT4D-Programmer"
    assert B.dav_base("http://192.168.1.16:8080/", "jean marie") == \
        "http://192.168.1.16:8080/remote.php/dav/files/jean%20marie"
    assert B.dav_base("https://connect.drive.infomaniak.com/webdav", "x", "a/b") == \
        "https://connect.drive.infomaniak.com/webdav/a/b"


def test_scan_backups(tmp_path):
    (tmp_path / "sous").mkdir()
    (tmp_path / "a.bin").write_bytes(b"1" * 10)
    (tmp_path / "sous" / "b.rt4d").write_bytes(b"2")
    (tmp_path / "c.bin.partiel").write_bytes(b"3")
    todo = B.scan_backups(str(tmp_path), {})
    assert [t[0] for t in todo] == ["a.bin", "sous/b.rt4d"]
    idx = {rel: st for rel, _p, st in todo}
    assert B.scan_backups(str(tmp_path), idx) == []
    (tmp_path / "a.bin").write_bytes(b"1" * 11)
    assert [t[0] for t in B.scan_backups(str(tmp_path), idx)] == ["a.bin"]
    assert B.scan_backups(str(tmp_path / "absent"), {}) == []


class Dav(BaseHTTPRequestHandler):
    files: dict = {}
    dirs: set = set()
    auth = "Basic " + base64.b64encode(b"jm:secret").decode()

    def log_message(self, *a):
        pass

    def _ok_auth(self):
        if self.headers.get("Authorization") != self.auth:
            self.send_response(401)
            self.end_headers()
            return False
        return True

    def do_MKCOL(self):
        if not self._ok_auth():
            return
        exists = self.path in self.dirs
        self.dirs.add(self.path)
        self.send_response(405 if exists else 201)
        self.end_headers()

    def do_PUT(self):
        if not self._ok_auth():
            return
        parent = self.path.rsplit("/", 1)[0]
        if parent not in self.dirs:
            self.send_response(409)
            self.end_headers()
            return
        n = int(self.headers.get("Content-Length", 0))
        self.files[self.path] = self.rfile.read(n)
        self.send_response(201)
        self.end_headers()


class FakeWin:
    def __init__(self, settings, bdir, path=None):
        self.settings, self._bdir, self.path = settings, bdir, path

    def backup_dir(self):
        return self._bdir


@pytest.fixture
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def _wait(app, bk, msgs, timeout=10):
    t = time.time()
    while time.time() - t < timeout:
        app.processEvents()
        if not bk.busy and msgs and not msgs[-1].endswith("…"):
            return
        time.sleep(0.02)
    raise AssertionError(f"délai dépassé : {msgs}")


def test_envoi_webdav(app, tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Dav)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Dav.files.clear()
    Dav.dirs.clear()
    try:
        s = QSettings(str(tmp_path / "r.ini"), QSettings.IniFormat)
        s.setValue("nc_url", f"http://127.0.0.1:{srv.server_port}")
        s.setValue("nc_user", "jm")
        s.setValue("nc_pass", "secret")
        s.setValue("port", "/dev/ttyUSB0")
        bdir = tmp_path / "RT4D-sauvegardes"
        bdir.mkdir()
        (bdir / "RT4D-flash-complete-2026-11-08.bin").write_bytes(b"\xAA" * 4096)
        (bdir / "RT4D-avant-ecriture-20261108-210000.rt4d").write_bytes(b"cp0")
        cp = tmp_path / "Mon codeplug.rt4d"
        cp.write_bytes(b"codeplug")
        bk = B.Backup(FakeWin(s, str(bdir), str(cp)))
        msgs = []
        bk.status.connect(msgs.append)

        bk.run()
        _wait(app, bk, msgs)
        assert "faite" in msgs[-1], msgs
        root = "/remote.php/dav/files/jm/RT4D-Programmer"
        f = Dav.files
        assert f[root + "/sauvegardes/RT4D-flash-complete-2026-11-08.bin"] == b"\xAA" * 4096
        assert f[root + "/sauvegardes/RT4D-avant-ecriture-20261108-210000.rt4d"] == b"cp0"
        assert f[root + "/codeplug/Mon%20codeplug.rt4d"] == b"codeplug"
        assert any(k.startswith(root + "/codeplug/historique/Mon%20codeplug_") for k in f)
        cfg = json.loads(f[root + "/reglages.json"])
        assert cfg["port"] == "/dev/ttyUSB0" and "nc_pass" not in cfg

        # 2e passage : seuls les nouveaux fichiers du dossier des sauvegardes partent
        Dav.files.clear()
        (bdir / "RT4D-police-avant-20261108-220000.bin").write_bytes(b"font")
        msgs.clear()
        bk.run()
        _wait(app, bk, msgs)
        sent = [k for k in Dav.files if "/sauvegardes/" in k]
        assert sent == [root + "/sauvegardes/RT4D-police-avant-20261108-220000.bin"]

        # mauvais mot de passe : message clair, rien de cassé
        s.setValue("nc_pass", "faux")
        msgs.clear()
        bk.run()
        _wait(app, bk, msgs)
        assert "refusé" in msgs[-1], msgs
    finally:
        srv.shutdown()
