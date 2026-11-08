"""Fenêtre principale de RT4D Programmer."""

from __future__ import annotations

import os
import sys
import traceback

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QSizePolicy, QToolButton, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from . import APP_NAME, __version__, csvio
from . import layout as L
from . import addressbook as AB
from . import firmware_pkg as FW
from . import protocol, radio_ops, updater
from .codeplug import Codeplug
from .gui_tabs import ChannelsTab, ContactsTab, GroupsTab, KeysTab, SettingsTab, ZonesTab
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
<h4>Carnet d'adresses DMR</h4>
<p><b>Radio → Carnet d'adresses DMR</b> : téléchargez la base RadioID.net (ou ouvrez un CSV),
cochez les pays voulus puis envoyez. La radio affichera l'indicatif et le nom des stations
qui vous appellent.</p>
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
        self.fw = None
        self.last_dir = last_dir
        v = QVBoxLayout(self)
        warn = QLabel(
            "<b>Attention : cette opération remplace le firmware de la radio.</b><br>"
            "Faites d'abord une sauvegarde complète (menu Radio).<br><br>"
            "<b>Firmware officiel Radtel</b> : téléchargez la mise à jour RT-4D sur le site Radtel "
            "(bouton ci-dessous), décompressez le .rar puis choisissez le fichier "
            "« RT-4D Upgrade Vx_xx_xxxxxx.zip ». Le firmware est extrait sans lancer l'outil Radtel.<br>"
            "Vous pouvez aussi choisir un fichier <code>.bin</code> (par exemple REFW).<br><br>"
            "<b>Procédure :</b><br>"
            "1. Éteignez la radio, laissez le câble USB branché.<br>"
            "2. <b>Maintenez le PTT enfoncé</b> et rallumez la radio : l'écran reste éteint "
            "(mode bootloader). Vous pouvez relâcher le PTT.<br>"
            "3. Cliquez sur « Flasher ». Ne débranchez rien avant la fin.<br>"
            "4. À la fin, éteignez puis rallumez la radio normalement.<br><br>"
            "Seul le firmware principal (MCU) est flashé. La puce DMR et la table Unicode "
            "(SMS) se mettent à jour avec les outils séparés fournis dans le même paquet Radtel.")
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
        b2 = QPushButton("Site Radtel…")
        b2.setToolTip(FW.RADTEL_DOWNLOAD_PAGE)
        b2.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(FW.RADTEL_DOWNLOAD_PAGE)))
        row.addWidget(self.file, 1)
        row.addWidget(b)
        row.addWidget(b2)
        f.addRow("Firmware", row)
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
        path, _ = QFileDialog.getOpenFileName(
            self, "Firmware RT-4D", self.last_dir,
            "Firmware RT-4D (*.zip *.exe *.bin *.rar);;Tous les fichiers (*)")
        if not path:
            return
        self.last_dir = os.path.dirname(path)
        self.file.setText(path)
        try:
            self.fw = FW.load_firmware(path)
            self.data = self.fw.image
            self.status.setText(f"<span style='color:#27ae60'><b>{self.fw.label}</b> — "
                                f"{self.fw.used_bytes // 1024} Ko de code, prêt à flasher."
                                f"</span>")
        except Exception as e:
            self.fw = None
            self.data = None
            self.status.setText(f"<span style='color:#c0392b'>{e}</span>")
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


class AddressBookDialog(QDialog):
    """Carnet d'adresses DMR : source, filtre par pays, envoi à la radio."""

    PRESETS = {
        "France seulement": {"France"},
        "France et pays voisins": {"France", "Italy", "Monaco", "Switzerland", "Belgium",
                                   "Spain", "Germany", "Luxembourg", "Andorra"},
    }

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Carnet d'adresses DMR (noms des correspondants)")
        self.setMinimumSize(560, 600)
        self.entries: list = []
        self.payload = b""
        v = QVBoxLayout(self)
        intro = QLabel("La radio affiche l'indicatif et le nom de la station qui vous appelle en DMR, "
                       "grâce à la base mondiale des identifiants (RadioID.net). Choisissez les pays "
                       "à inclure : la base complète prend environ 20 minutes à envoyer.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        row = QHBoxLayout()
        b1 = QPushButton("Télécharger depuis RadioID.net")
        b1.clicked.connect(self.download)
        b2 = QPushButton("Ouvrir un CSV…")
        b2.clicked.connect(self.open_csv)
        row.addWidget(b1)
        row.addWidget(b2)
        v.addLayout(row)
        self.src = QLabel("Aucune base chargée.")
        v.addWidget(self.src)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Pays :"))
        for name in list(self.PRESETS) + ["Tous", "Aucun"]:
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, n=name: self.preset(n))
            prow.addWidget(b)
        v.addLayout(prow)
        self.filter = QLineEdit(placeholderText="Filtrer la liste des pays…")
        self.filter.textChanged.connect(self.apply_filter)
        v.addWidget(self.filter)
        self.list = QListWidget()
        self.list.itemChanged.connect(self.update_summary)
        v.addWidget(self.list, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.okbtn = bb.button(QDialogButtonBox.Ok)
        self.okbtn.setText("Envoyer à la radio")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.update_summary()

    def _load(self, entries, label):
        self.entries = entries
        nb = f"{len(entries):,}".replace(",", " ")
        self.src.setText(f"{label} : {nb} identifiants.")
        self.list.blockSignals(True)
        self.list.clear()
        for name, n in AB.countries(entries):
            it = QListWidgetItem(f"{name}  ({n})")
            it.setData(Qt.UserRole, name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if name == "France" else Qt.Unchecked)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.update_summary()

    def download(self):
        ok, res = TaskDialog.run(self, "Téléchargement RadioID.net", AB.download_radioid)
        if ok:
            self._load(res, "RadioID.net")

    def open_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Base d'identifiants DMR", "",
                                              "CSV (*.csv *.txt);;Tous les fichiers (*)")
        if not path:
            return
        try:
            self._load(AB.load_csv(path), os.path.basename(path))
        except Exception as e:
            QMessageBox.critical(self, "CSV", str(e))

    def preset(self, name):
        keep = self.PRESETS.get(name)
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            it = self.list.item(i)
            on = name == "Tous" or (keep is not None and it.data(Qt.UserRole) in keep)
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self.list.blockSignals(False)
        self.update_summary()

    def apply_filter(self, text):
        q = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def selected(self) -> list:
        keep = {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked}
        return [e for e in self.entries if (e.country or "(inconnu)") in keep]

    def update_summary(self, *_):
        sel = self.selected() if self.entries else []
        if not sel:
            self.payload = b""
            self.summary.setText("Sélectionnez au moins un pays.")
            self.okbtn.setEnabled(False)
            return
        try:
            self.payload = AB.build_payload(sel)
        except RadioError as e:
            self.payload = b""
            self.summary.setText(str(e))
            self.okbtn.setEnabled(False)
            return
        n, minutes = AB.estimate(len(self.payload))
        nb = f"{len(sel):,}".replace(",", " ")
        ko = f"{len(self.payload) // 1024:,}".replace(",", " ")
        self.summary.setText(f"<b>{nb} identifiants</b>, {ko} Ko — envoi estimé : environ "
                             f"{max(1, round(minutes))} min. Le carnet actuel de la radio "
                             f"sera remplacé.")
        self.okbtn.setEnabled(True)


class UpdateCheck(QThread):
    """Vérification silencieuse des mises à jour en arrière-plan."""
    found = Signal(object)          # Release ou None
    failed = Signal(str)

    def run(self):
        try:
            self.found.emit(updater.check_latest())
        except Exception as e:
            self.failed.emit(str(e))


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
        self.toolbar = tb

        self.tabs = QTabWidget()
        self.t_ch = ChannelsTab()
        self.t_ct = ContactsTab()
        self.t_gl = GroupsTab()
        self.t_zn = ZonesTab()
        self.t_st = SettingsTab()
        self.t_keys = KeysTab()
        self.logbox = QPlainTextEdit(readOnly=True)
        self.logbox.setFont(QFont("Monospace", 9))
        for t, name in ((self.t_ch, "Canaux"), (self.t_ct, "Contacts"), (self.t_gl, "Groupes RX"),
                        (self.t_zn, "Zones"), (self.t_st, "Réglages"),
                        (self.t_keys, "Réglages touches")):
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
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.update_btn = QToolButton()
        self.update_btn.setStyleSheet("QToolButton { background: #1e7e34; color: white; "
                                      "font-weight: bold; padding: 3px 10px; border-radius: 4px; }")
        self.update_btn.clicked.connect(self.install_update)
        self.update_action = tb.addWidget(self.update_btn)
        self.update_action.setVisible(False)
        self.pending_release = None
        self.update_thread = None
        updater.cleanup_old()
        if self.settings.value("auto_update", True, type=bool):
            QTimer.singleShot(1500, lambda: self.check_update(silent=True))
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
        self._act(m, "Carnet d'adresses DMR (noms des correspondants)…", self.address_book)
        m.addSeparator()
        self._act(m, "Sauvegarde complète de la flash (4 Mo)…", self.backup_full)
        self._act(m, "Restaurer une sauvegarde complète…", self.restore_full)

        m = self.menuBar().addMenu("F&irmware")
        self._act(m, "Flasher un firmware (mise à jour / retour arrière)…", self.flash_fw)

        m = self.menuBar().addMenu("&Aide")
        self._act(m, "Mode d'emploi", self.show_help, "F1")
        m.addSeparator()
        self._act(m, "Rechercher une mise à jour", lambda: self.check_update(silent=False))
        a = QAction("Vérifier les mises à jour au démarrage", self, checkable=True)
        a.setChecked(self.settings.value("auto_update", True, type=bool))
        a.toggled.connect(lambda on: self.settings.setValue("auto_update", on))
        m.addAction(a)
        m.addSeparator()
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
        for t in (self.t_ch, self.t_ct, self.t_gl, self.t_zn, self.t_st, self.t_keys):
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

    def address_book(self):
        port = self.port_name()
        if not port:
            return
        dlg = AddressBookDialog(self)
        if dlg.exec() != QDialog.Accepted or not dlg.payload:
            return
        self.settings.setValue("port", port)
        ok, _ = TaskDialog.run(self, "Envoi du carnet d'adresses", AB.write_addressbook, port,
                               dlg.payload)
        if ok:
            QMessageBox.information(self, "Carnet d'adresses",
                                    "Carnet d'adresses envoyé. Redémarrez la radio si les noms "
                                    "n'apparaissent pas tout de suite.")

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
                               cancellable=False, full_image=bool(dlg.fw and dlg.fw.official))
        if ok:
            QMessageBox.information(self, "Firmware", "Firmware flashé avec succès.\n\nÉteignez puis "
                                    "rallumez la radio normalement.")

    # -- mises à jour --
    def check_update(self, silent: bool):
        if self.update_thread and self.update_thread.isRunning():
            return
        self.update_thread = UpdateCheck(self)

        def found(rel):
            if rel:
                self.pending_release = rel
                self.update_btn.setText(f"⬆  Mise à jour {rel.version} disponible")
                self.update_btn.setToolTip("Cliquez pour voir les nouveautés et installer")
                self.update_action.setVisible(True)
                self.log(f"Nouvelle version disponible : {rel.version}")
                if not silent:
                    self.install_update()
            elif not silent:
                QMessageBox.information(self, "Mise à jour",
                                        f"Vous utilisez la dernière version ({__version__}).")

        def failed(msg):
            self.log(f"Vérification des mises à jour impossible : {msg}")
            if not silent:
                QMessageBox.warning(self, "Mise à jour",
                                    f"Impossible de vérifier les mises à jour :\n{msg}")

        self.update_thread.found.connect(found)
        self.update_thread.failed.connect(failed)
        self.update_thread.start()

    def install_update(self):
        rel = self.pending_release
        if not rel:
            return
        notes = rel.notes.strip() or "(pas de description)"
        box = QMessageBox(self)
        box.setWindowTitle("Mise à jour disponible")
        box.setIcon(QMessageBox.Information)
        box.setText(f"<b>Version {rel.version} disponible</b> (vous avez la {__version__}).")
        box.setInformativeText("Installer maintenant ? Le logiciel redémarrera automatiquement."
                               if updater.can_self_update() else
                               "Ouvrir la page de téléchargement ?")
        box.setDetailedText(notes)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.button(QMessageBox.Yes).setText("Installer" if updater.can_self_update() else "Ouvrir")
        box.button(QMessageBox.No).setText("Plus tard")
        if box.exec() != QMessageBox.Yes:
            return
        if not updater.can_self_update():
            QDesktopServices.openUrl(QUrl(rel.page))
            return
        if not self.confirm_discard():
            return

        def job(progress=None, cancel=None, log=None):
            return updater.download_and_install(rel, progress, cancel, log)
        ok, path = TaskDialog.run(self, f"Mise à jour vers la version {rel.version}", job)
        if not ok:
            return
        try:
            updater.restart(path)
        except Exception as e:
            QMessageBox.information(self, "Mise à jour",
                                    f"Mise à jour installée. Relancez le logiciel.\n({e})")
        self.dirty = False
        QApplication.quit()

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
