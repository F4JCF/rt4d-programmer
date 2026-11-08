"""Fenêtre principale de RT4D Programmer."""

from __future__ import annotations

import os
import sys
import traceback

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from . import APP_NAME, __version__, csvio
from . import layout as L
from . import protocol, radio_ops
from .codeplug import Codeplug
from .gui_tabs import ChannelsTab, ContactsTab, GroupsTab, SettingsTab, ZonesTab
from .protocol import Cancelled, RadioError

HELP = """
<h3>RT4D Programmer — mode d'emploi rapide</h3>
<p><b>Ce logiciel programme le Radtel RT-4D</b> avec le <b>firmware officiel Radtel
V3.18 ou plus récent</b> (testé avec la V3.22) ou avec le firmware communautaire
<b>REFW</b> (beta 41 ou plus). Le format est détecté automatiquement.</p>
<h4>Programmer la radio</h4>
<ol>
<li>Branchez le câble USB, allumez la radio <b>normalement</b>, choisissez le port.</li>
<li><b>Radio → Lire la radio</b> (environ 30 s).</li>
<li>Modifiez les canaux, contacts, groupes RX, zones et réglages.</li>
<li><b>Radio → Écrire dans la radio</b>. Le contenu actuel de la radio est d'abord
sauvegardé automatiquement dans le dossier indiqué.</li>
</ol>
<h4>Changer de firmware (mise à jour ou retour arrière)</h4>
<ol>
<li>Faites d'abord une <b>sauvegarde complète</b> (Radio → Sauvegarde complète, environ 6 min).</li>
<li>Menu <b>Firmware → Flasher un firmware</b>, choisissez le fichier <code>.bin</code>.</li>
<li>Éteignez la radio, câble branché, puis <b>rallumez-la en maintenant le PTT</b>
(mode bootloader : l'écran reste éteint).</li>
<li>Lancez le flash et ne touchez à rien jusqu'à la fin.</li>
</ol>
<p>Ce logiciel flashe le firmware principal (MCU). Il ne flashe pas le firmware
de la puce DMR. Les mises à jour officielles Radtel sont livrées en <code>.exe</code>
et ne contiennent pas de <code>.bin</code> utilisable ici.</p>
<h4>Linux</h4>
<p>Si le port est refusé : <code>sudo usermod -aG dialout $USER</code>, puis fermez et rouvrez la session.</p>
"""


# ───────────────────────────── tâches en arrière-plan ─────────────────────────────

class Worker(QObject):
    progress = Signal(int, int, str)
    log = Signal(str)
    done = Signal(bool, object)

    def __init__(self, fn, *args, **kw):
        super().__init__()
        self.fn, self.args, self.kw = fn, args, kw
        self.cancelled = False

    def run(self):
        try:
            res = self.fn(*self.args, progress=lambda a, b, c: self.progress.emit(a, b, c),
                          cancel=lambda: self.cancelled, log=self.log.emit, **self.kw)
            self.done.emit(True, res)
        except (RadioError, Cancelled) as e:
            self.done.emit(False, str(e))
        except Exception as e:
            self.done.emit(False, f"Erreur inattendue : {e}\n\n{traceback.format_exc()}")


class TaskDialog(QDialog):
    """Fenêtre de progression modale pour une opération radio."""

    def __init__(self, parent, title, fn, *args, cancellable=True, **kw):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        v = QVBoxLayout(self)
        self.label = QLabel("Démarrage…")
        self.label.setWordWrap(True)
        v.addWidget(self.label)
        self.bar = QProgressBar()
        v.addWidget(self.bar)
        self.logbox = QPlainTextEdit(readOnly=True)
        self.logbox.setMaximumHeight(140)
        v.addWidget(self.logbox)
        self.btn = QPushButton("Annuler" if cancellable else "Patientez…")
        self.btn.setEnabled(cancellable)
        self.btn.clicked.connect(self.on_btn)
        v.addWidget(self.btn, alignment=Qt.AlignRight)
        self.ok = False
        self.result_value = None
        self.finished_ = False
        self.thread = QThread(self)
        self.worker = Worker(fn, *args, **kw)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress)
        self.worker.log.connect(self.on_log)
        self.worker.done.connect(self.on_done)
        self.thread.start()

    def on_progress(self, a, b, label):
        self.bar.setMaximum(max(b, 1))
        self.bar.setValue(a)
        self.label.setText(f"{label} : {a} / {b}")

    def on_log(self, msg):
        self.logbox.appendPlainText(msg)
        if self.parent() and hasattr(self.parent(), "log"):
            self.parent().log(msg)

    def on_done(self, ok, res):
        self.ok, self.result_value, self.finished_ = ok, res, True
        self.thread.quit()
        self.thread.wait()
        self.accept()

    def on_btn(self):
        self.worker.cancelled = True
        self.btn.setEnabled(False)
        self.btn.setText("Annulation…")

    def reject(self):          # touche Échap
        if not self.finished_:
            self.on_btn()
            return
        super().reject()

    @classmethod
    def run(cls, parent, title, fn, *args, **kw):
        dlg = cls(parent, title, fn, *args, **kw)
        dlg.exec()
        if not dlg.ok:
            QMessageBox.critical(parent, title, str(dlg.result_value))
        return dlg.ok, dlg.result_value


# ───────────────────────────── dialogues ─────────────────────────────

class WriteDialog(QDialog):
    def __init__(self, parent, cp: Codeplug, backup_dir: str):
        super().__init__(parent)
        self.setWindowTitle("Écrire dans la radio")
        v = QVBoxLayout(self)
        self.checks = {}
        if getattr(cp, "kind", "refw") == "stock":
            lab = QLabel("Firmware officiel Radtel : comme le logiciel officiel, tout le codeplug "
                         "est écrit (réglages, canaux, zones, contacts, groupes RX…). Les parties "
                         "non gérées ici sont réécrites telles qu'elles ont été lues.")
            lab.setWordWrap(True)
            v.addWidget(lab)
        else:
            v.addWidget(QLabel("Parties à écrire dans la radio :"))
        for s in (L.EDITABLE_SECTIONS if getattr(cp, "kind", "refw") == "refw" else []):
            cb = QCheckBox(L.SECTION_LABELS[s])
            cb.setChecked(True)
            if s == "settings" and not (cp.has_settings and cp.is_refw):
                cb.setChecked(False)
                cb.setEnabled(False)
                cb.setText(cb.text() + " (indisponible : pas de réglages REFW dans ce codeplug)")
            self.checks[s] = cb
            v.addWidget(cb)
        f = QFormLayout()
        row = QHBoxLayout()
        self.dir = QLineEdit(backup_dir)
        b = QPushButton("…")
        b.clicked.connect(self.pick)
        row.addWidget(self.dir, 1)
        row.addWidget(b)
        f.addRow("Sauvegarde automatique dans", row)
        v.addLayout(f)
        info = QLabel("Avant d'écrire, le logiciel relit la radio et enregistre son contenu "
                      "actuel (fichier .4rdmf) : vous pourrez toujours revenir en arrière.")
        info.setWordWrap(True)
        v.addWidget(info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Écrire")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def pick(self):
        d = QFileDialog.getExistingDirectory(self, "Dossier de sauvegarde", self.dir.text())
        if d:
            self.dir.setText(d)

    def sections(self):
        if not self.checks:
            return ["all"]
        return [s for s, cb in self.checks.items() if cb.isChecked()]


class FirmwareDialog(QDialog):
    def __init__(self, parent, port: str, last_dir: str):
        super().__init__(parent)
        self.setWindowTitle("Flasher un firmware (mise à jour / retour arrière)")
        self.setMinimumWidth(600)
        self.data = None
        self.last_dir = last_dir
        v = QVBoxLayout(self)
        warn = QLabel(
            "<b>Attention : cette opération remplace le firmware de la radio.</b><br>"
            "Faites d'abord une sauvegarde complète (menu Radio). Utilisez uniquement un "
            "fichier <code>.bin</code> prévu pour le RT-4D (par exemple une version REFW "
            "de Dual Tachyon, plus récente ou plus ancienne).<br><br>"
            "<b>Procédure :</b><br>"
            "1. Éteignez la radio, laissez le câble USB branché.<br>"
            "2. <b>Maintenez le PTT enfoncé</b> et rallumez la radio : l'écran reste éteint "
            "(mode bootloader). Vous pouvez relâcher le PTT.<br>"
            "3. Cliquez sur « Flasher ». Ne débranchez rien avant la fin.<br>"
            "4. À la fin, éteignez puis rallumez la radio normalement.<br><br>"
            "Seul le firmware principal (MCU) est flashé, pas celui de la puce DMR.")
        warn.setWordWrap(True)
        warn.setTextFormat(Qt.RichText)
        v.addWidget(warn)
        f = QFormLayout()
        self.port = QComboBox()
        for dev, desc in protocol.list_ports():
            self.port.addItem(f"{dev}  {desc}".strip(), dev)
        i = self.port.findData(port)
        if i >= 0:
            self.port.setCurrentIndex(i)
        f.addRow("Port", self.port)
        row = QHBoxLayout()
        self.file = QLineEdit(readOnly=True)
        b = QPushButton("Choisir…")
        b.clicked.connect(self.pick)
        row.addWidget(self.file, 1)
        row.addWidget(b)
        f.addRow("Fichier firmware", row)
        v.addLayout(f)
        self.status = QLabel()
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        self.confirm = QCheckBox("J'ai fait une sauvegarde et je comprends le risque.")
        self.confirm.toggled.connect(self.update_ok)
        v.addWidget(self.confirm)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.okbtn = bb.button(QDialogButtonBox.Ok)
        self.okbtn.setText("Flasher")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.update_ok()

    def pick(self):
        path, _ = QFileDialog.getOpenFileName(self, "Firmware RT-4D", self.last_dir,
                                              "Firmware (*.bin);;Tous les fichiers (*)")
        if not path:
            return
        self.last_dir = os.path.dirname(path)
        self.file.setText(path)
        with open(path, "rb") as fh:
            data = fh.read()
        err = protocol.check_firmware_file(data)
        if err:
            self.data = None
            self.status.setText(f"<span style='color:#c0392b'>{err}</span>")
        else:
            self.data = data
            self.status.setText(f"<span style='color:#27ae60'>Fichier valide : {len(data)} octets "
                                f"({(len(data) + 1023) // 1024} blocs).</span>")
        self.update_ok()

    def update_ok(self):
        self.okbtn.setEnabled(bool(self.data) and self.confirm.isChecked() and self.port.count() > 0)


class RestoreDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Restaurer une sauvegarde complète")
        v = QVBoxLayout(self)
        lab = QLabel("La sauvegarde de 4 Mo va être réécrite dans la radio (allumée normalement) : "
                     "codeplug, réglages, journal, logo, polices, voix…")
        lab.setWordWrap(True)
        v.addWidget(lab)
        self.calib = QCheckBox("Restaurer aussi la CALIBRATION (déconseillé, sauf si elle est "
                               "corrompue et que la sauvegarde vient de CETTE radio)")
        v.addWidget(self.calib)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Restaurer")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)


# ───────────────────────────── fenêtre principale ─────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("F4JCF", "RT4D-Programmer")
        self.cp = Codeplug()
        self.path: str | None = None
        self.dirty = False
        self.resize(1250, 760)

        tb = QToolBar("Principal")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addWidget(QLabel(" Port : "))
        self.port = QComboBox()
        self.port.setMinimumWidth(220)
        tb.addWidget(self.port)
        b = QPushButton("↻")
        b.setToolTip("Actualiser la liste des ports")
        b.clicked.connect(self.refresh_ports)
        tb.addWidget(b)
        tb.addSeparator()

        self.tabs = QTabWidget()
        self.t_ch = ChannelsTab()
        self.t_ct = ContactsTab()
        self.t_gl = GroupsTab()
        self.t_zn = ZonesTab()
        self.t_st = SettingsTab()
        self.logbox = QPlainTextEdit(readOnly=True)
        self.logbox.setFont(QFont("Monospace", 9))
        for t, name in ((self.t_ch, "Canaux"), (self.t_ct, "Contacts"), (self.t_gl, "Groupes RX"),
                        (self.t_zn, "Zones"), (self.t_st, "Réglages")):
            self.tabs.addTab(t, name)
            t.changed.connect(self.mark_dirty)
        self.tabs.addTab(self.logbox, "Journal")
        self.tabs.currentChanged.connect(self.on_tab)

        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(4, 4, 4, 4)
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        cv.addWidget(self.banner)
        cv.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        self._menus(tb)
        self.refresh_ports()
        self.load_codeplug(self.cp, None)
        self.log(f"{APP_NAME} {__version__} prêt.")

    # -- menus --
    def _act(self, menu, text, fn, shortcut=None, tb=None):
        a = QAction(text, self)
        a.triggered.connect(fn)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        menu.addAction(a)
        if tb is not None:
            tb.addAction(a)
        return a

    def _menus(self, tb):
        m = self.menuBar().addMenu("&Fichier")
        self._act(m, "Nouveau codeplug vide", self.new_file)
        self._act(m, "Ouvrir…", self.open_file, "Ctrl+O")
        self._act(m, "Enregistrer", self.save_file, "Ctrl+S")
        self._act(m, "Enregistrer sous…", self.save_as)
        m.addSeparator()
        self._act(m, "Importer des canaux (CSV)…", lambda: self.import_csv("channels"))
        self._act(m, "Exporter les canaux (CSV)…", lambda: self.export_csv("channels"))
        self._act(m, "Importer des contacts (CSV)…", lambda: self.import_csv("contacts"))
        self._act(m, "Exporter les contacts (CSV)…", lambda: self.export_csv("contacts"))
        m.addSeparator()
        self._act(m, "Quitter", self.close, "Ctrl+Q")

        m = self.menuBar().addMenu("&Radio")
        self._act(m, "Tester la connexion", self.test_link, tb=tb)
        self._act(m, "Lire la radio", self.read_radio, "Ctrl+R", tb=tb)
        self._act(m, "Écrire dans la radio…", self.write_radio, "Ctrl+W", tb=tb)
        m.addSeparator()
        self._act(m, "Sauvegarde complète de la flash (4 Mo)…", self.backup_full)
        self._act(m, "Restaurer une sauvegarde complète…", self.restore_full)

        m = self.menuBar().addMenu("F&irmware")
        self._act(m, "Flasher un firmware (mise à jour / retour arrière)…", self.flash_fw)

        m = self.menuBar().addMenu("&Aide")
        self._act(m, "Mode d'emploi", self.show_help, "F1")
        self._act(m, "À propos", self.about)

    # -- utilitaires --
    def log(self, msg: str):
        self.logbox.appendPlainText(msg)

    def port_name(self) -> str | None:
        p = self.port.currentData()
        if not p:
            QMessageBox.warning(self, "Port", "Aucun port série sélectionné. Branchez le câble "
                                "et cliquez sur ↻.")
        return p

    def refresh_ports(self):
        cur = self.port.currentData() or self.settings.value("port", "")
        self.port.clear()
        for dev, desc in protocol.list_ports():
            self.port.addItem(f"{dev}  {desc}".strip(), dev)
        i = self.port.findData(cur)
        if i >= 0:
            self.port.setCurrentIndex(i)

    def backup_dir(self) -> str:
        return self.settings.value("backup_dir",
                                   os.path.join(os.path.expanduser("~"), "RT4D-sauvegardes"))

    def last_dir(self) -> str:
        return self.settings.value("last_dir", os.path.expanduser("~"))

    def set_last_dir(self, path: str):
        self.settings.setValue("last_dir", os.path.dirname(path))

    def mark_dirty(self):
        self.dirty = True
        self.update_title()

    def update_title(self):
        name = os.path.basename(self.path) if self.path else "sans titre"
        self.setWindowTitle(f"{'● ' if self.dirty else ''}{name} — {APP_NAME} {__version__}")

    def update_banner(self):
        cp = self.cp
        if getattr(cp, "kind", "refw") == "stock":
            txt, col = ("Firmware officiel Radtel (V3.18 ou plus récent) : lecture et écriture "
                        "disponibles."), "#1e7e34"
        elif not cp.has_settings:
            txt, col = ("Codeplug sans réglages radio : vous pouvez préparer canaux, contacts et "
                        "zones, puis les écrire (les réglages de la radio seront conservés)."), "#2c3e50"
        elif cp.is_refw:
            txt, col = "Format REFW reconnu : lecture et écriture disponibles.", "#1e7e34"
        else:
            txt, col = ("Format REFW NON reconnu (firmware officiel ou REFW ancien) : les données "
                        "affichées peuvent être incohérentes et l'écriture est bloquée. "
                        "Flashez le firmware REFW (menu Firmware)."), "#b03a2e"
        self.banner.setText(txt)
        self.banner.setStyleSheet(f"color: white; background: {col}; padding: 6px; border-radius: 4px;")

    def load_codeplug(self, cp: Codeplug, path):
        self.cp = cp
        self.path = path
        self.dirty = False
        for t in (self.t_ch, self.t_ct, self.t_gl, self.t_zn, self.t_st):
            t.set_codeplug(cp)
        self.update_banner()
        self.update_title()

    def on_tab(self, i):
        w = self.tabs.widget(i)
        if hasattr(w, "refresh"):
            w.refresh()

    def confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "Modifications non enregistrées",
                                 "Le codeplug a été modifié. Enregistrer avant de continuer ?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_file()
        return r == QMessageBox.Discard

    # -- fichiers --
    def new_file(self):
        if self.confirm_discard():
            self.load_codeplug(Codeplug(), None)

    def open_file(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Ouvrir un codeplug", self.last_dir(),
            "Codeplugs RT-4D (*.rt4d *.4rdmf *.bin);;Firmware officiel (*.rt4d);;"
            "REFW (*.4rdmf);;Sauvegarde complète (*.bin);;Tous les fichiers (*)")
        if not path:
            return
        try:
            cp = radio_ops.load_any(path)
            if path.lower().endswith(".bin"):
                path = None          # ne jamais réécrire une sauvegarde complète
        except Exception as e:
            QMessageBox.critical(self, "Ouverture", str(e))
            return
        self.load_codeplug(cp, path)
        self.log(f"Fichier ouvert : {path or 'sauvegarde complète (lecture)'}")

    def save_file(self) -> bool:
        if not self.path:
            return self.save_as()
        try:
            self.cp.commit()
            self.cp.save(self.path)
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return False
        self.dirty = False
        self.update_title()
        self.log(f"Enregistré : {self.path}")
        return True

    def save_as(self) -> bool:
        ext = ".rt4d" if getattr(self.cp, "kind", "refw") == "stock" else ".4rdmf"
        label = "Codeplug firmware officiel (*.rt4d)" if ext == ".rt4d" else "Codeplug REFW (*.4rdmf)"
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer le codeplug",
                                              os.path.join(self.last_dir(), "RT4D" + ext), label)
        if not path:
            return False
        if not path.lower().endswith(ext):
            path += ext
        self.path = path
        self.set_last_dir(path)
        return self.save_file()

    def import_csv(self, what):
        path, _ = QFileDialog.getOpenFileName(self, "Importer un CSV", self.last_dir(),
                                              "CSV (*.csv *.txt);;Tous les fichiers (*)")
        if not path:
            return
        r = QMessageBox.question(self, "Import CSV",
                                 "Remplacer les éléments existants ?\n\n"
                                 "Oui = remplacer tout, Non = ajouter à la suite.",
                                 QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return
        try:
            fn = csvio.import_channels if what == "channels" else csvio.import_contacts
            n, warn = fn(self.cp, path, replace=(r == QMessageBox.Yes))
        except Exception as e:
            QMessageBox.critical(self, "Import CSV", str(e))
            return
        self.set_last_dir(path)
        self.load_codeplug(self.cp, self.path)
        self.mark_dirty()
        msg = f"{n} élément(s) importé(s)."
        if warn:
            msg += "\n\nAvertissements :\n" + "\n".join(warn[:20])
        QMessageBox.information(self, "Import CSV", msg)

    def export_csv(self, what):
        default = "canaux.csv" if what == "channels" else "contacts.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Exporter en CSV",
                                              os.path.join(self.last_dir(), default), "CSV (*.csv)")
        if not path:
            return
        (csvio.export_channels if what == "channels" else csvio.export_contacts)(self.cp, path)
        self.set_last_dir(path)
        self.log(f"Exporté : {path}")

    # -- radio --
    def test_link(self):
        port = self.port_name()
        if not port:
            return
        self.settings.setValue("port", port)

        def job(progress=None, cancel=None, log=None):
            return radio_ops.identify(port, log=log)
        ok, res = TaskDialog.run(self, "Test de connexion", job)
        if ok:
            self.log(res)
            QMessageBox.information(self, "Test de connexion", res)

    def read_radio(self):
        port = self.port_name()
        if not port or not self.confirm_discard():
            return
        self.settings.setValue("port", port)
        ok, cp = TaskDialog.run(self, "Lecture de la radio", radio_ops.read_codeplug, port)
        if ok:
            self.load_codeplug(cp, None)
            self.dirty = True
            self.update_title()
            self.log(f"Lecture terminée : {len(cp.channels)} canaux, {len(cp.contacts)} contacts, "
                     f"{len(cp.groups)} groupes RX, {len(cp.zones)} zones.")
            if getattr(cp, "kind", "refw") == "refw" and not cp.is_refw:
                QMessageBox.warning(self, "Format non reconnu",
                                    "Le format de cette radio n'est pas reconnu (firmware ancien ?). "
                                    "Les données affichées peuvent être fausses et l'écriture est "
                                    "bloquée.\n\nPensez à enregistrer ce fichier comme sauvegarde.")

    def write_radio(self):
        port = self.port_name()
        if not port:
            return
        problems = self.cp.validate()
        if problems:
            QMessageBox.warning(self, "Codeplug à corriger",
                                "Corrigez d'abord :\n\n• " + "\n• ".join(problems[:20]))
            return
        dlg = WriteDialog(self, self.cp, self.backup_dir())
        if dlg.exec() != QDialog.Accepted or not dlg.sections():
            return
        self.settings.setValue("backup_dir", dlg.dir.text())
        self.settings.setValue("port", port)
        self.cp.commit()
        ok, res = TaskDialog.run(self, "Écriture dans la radio", radio_ops.write_codeplug,
                                 port, self.cp, dlg.sections(), dlg.dir.text())
        if ok:
            QMessageBox.information(self, "Écriture terminée",
                                    f"Programmation réussie.\n\nSauvegarde de l'état précédent :\n{res}")
        self.load_codeplug(self.cp, self.path)
        self.dirty = True
        self.update_title()

    def backup_full(self):
        port = self.port_name()
        if not port:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Sauvegarde complète (4 Mo)",
                                              os.path.join(self.backup_dir(), "RT4D-flash-complete.bin"),
                                              "Image binaire (*.bin)")
        if not path:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ok, _ = TaskDialog.run(self, "Sauvegarde complète (environ 6 minutes)",
                               radio_ops.backup_spi, port, path)
        if ok:
            QMessageBox.information(self, "Sauvegarde", f"Sauvegarde complète enregistrée :\n{path}")

    def restore_full(self):
        port = self.port_name()
        if not port:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Sauvegarde complète à restaurer",
                                              self.backup_dir(), "Image binaire (*.bin)")
        if not path:
            return
        with open(path, "rb") as f:
            dump = f.read()
        try:
            radio_ops.restore_regions_from_dump(dump, False)
        except RadioError as e:
            QMessageBox.critical(self, "Restauration", str(e))
            return
        dlg = RestoreDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        ok, _ = TaskDialog.run(self, "Restauration", radio_ops.restore_spi, port, dump,
                               dlg.calib.isChecked())
        if ok:
            QMessageBox.information(self, "Restauration", "Restauration terminée. Redémarrez la radio.")

    def flash_fw(self):
        dlg = FirmwareDialog(self, self.port.currentData(), self.settings.value("fw_dir", self.last_dir()))
        if dlg.exec() != QDialog.Accepted:
            return
        self.settings.setValue("fw_dir", dlg.last_dir)
        port = dlg.port.currentData()
        ok, _ = TaskDialog.run(self, "Flash du firmware", protocol.flash_firmware, port, dlg.data,
                               cancellable=False)
        if ok:
            QMessageBox.information(self, "Firmware", "Firmware flashé avec succès.\n\nÉteignez puis "
                                    "rallumez la radio normalement.")

    def show_help(self):
        box = QMessageBox(self)
        box.setWindowTitle("Mode d'emploi")
        box.setTextFormat(Qt.RichText)
        box.setText(HELP)
        box.exec()

    def about(self):
        QMessageBox.about(self, "À propos", f"""<h3>{APP_NAME} {__version__}</h3>
<p>Programmation du Radtel RT-4D (firmware REFW) sous Windows et Linux.</p>
<p>Réalisé pour F4JCF. Licence GPL v3.</p>
<p>Protocole série d'après les outils de Dual Tachyon (licence Apache 2.0).
Carte mémoire du codeplug documentée par le projet RT4D Editor (jcalado).</p>
<p><b>Aucune garantie</b> : faites toujours une sauvegarde avant d'écrire ou de flasher.</p>""")

    def closeEvent(self, ev):
        if self.confirm_discard():
            ev.accept()
        else:
            ev.ignore()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    icon = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(__file__))), "icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
