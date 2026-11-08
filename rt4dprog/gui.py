"""Fenêtre principale de RT4D Programmer."""

from __future__ import annotations

import os
import sys
import traceback

from PySide6.QtCore import (QAbstractTableModel, QModelIndex, QObject, QSettings, Qt, QThread,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import QDesktopServices
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter
from PySide6.QtWidgets import (QApplication, QCheckBox, QMenuBar, QProgressDialog, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QAbstractItemView, QSplitter,
                               QTableView,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from . import APP_NAME, __version__, csvio
from . import layout as L
from . import addressbook as AB
from . import firmware_pkg as FW
from . import legacy, linux_desktop, protocol, radio_ops, updater
from .codeplug import Codeplug
from .gui_tabs import (ChannelsTab, ContactsTab, FreqLimitsTab, GroupsTab, KeysTab, SettingsTab, StartupTab,
                       ZonesTab)
from .protocol import Cancelled, RadioError

HELP = """
<h3>RT4D Programmer — mode d'emploi rapide</h3>
<p><b>Ce logiciel programme le Radtel RT-4D</b> avec le <b>firmware officiel Radtel
V3.18 ou plus récent</b> (testé avec les V3.22 et V3.25) ou avec le firmware communautaire
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
<h4>Limites de fréquences (firmware officiel)</h4>
<p>Onglet <b>Limites de fréquences</b> : jusqu'à 4 plages où l'émission est interdite.
Le bouton « Émission limitée aux bandes radioamateur » n'autorise l'émission que de 144 à 146
et de 430 à 440 MHz. Écrivez ensuite dans la radio. Hors de ces bandes, le poste affiche
« Prohibit TX » au lieu d'émettre. « Aucune limite » rétablit le réglage d'usine.</p>
<h4>Police avec accents (firmware officiel)</h4>
<p><b>Radio → Police avec accents</b> : redessine 7 caractères inutilisés de la police du poste en
é è ê à ç î É. L'ancienne police est enregistrée dans le dossier des sauvegardes, et le bouton
« Remettre une police enregistrée » la réinstalle.</p>
<h4>Changer de firmware (mise à jour ou retour arrière)</h4>
<ol>
<li>Faites d'abord une <b>sauvegarde complète</b> (Radio → Sauvegarde complète, environ 6 min).</li>
<li>Menu <b>Firmware → Flasher un firmware</b>, choisissez le paquet officiel Radtel
(« RT-4D Upgrade Vx_xx_xxxxxx.zip ») ou un fichier <code>.bin</code>.</li>
<li>Éteignez la radio, câble branché, puis <b>rallumez-la en maintenant le PTT</b>
(mode bootloader : l'écran reste éteint).</li>
<li>Lancez le flash et ne touchez à rien jusqu'à la fin.</li>
</ol>
<p>Ce logiciel flashe le firmware principal (MCU). Il ne flashe pas le firmware
de la puce DMR. Le firmware est extrait du paquet officiel Radtel sans lancer l'outil Radtel.</p>
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
        if getattr(cp, "kind", "refw") in ("stock", "legacy"):
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


class EntriesModel(QAbstractTableModel):
    HEAD = ["ID DMR", "Indicatif", "Nom", "Ville", "Région", "Pays"]

    def __init__(self):
        super().__init__()
        self.rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 6

    def data(self, index, role=Qt.DisplayRole):
        if role != Qt.DisplayRole or not index.isValid():
            return None
        e = self.rows[index.row()]
        return (e.dmr_id, e.callsign, e.name, e.city, e.state, e.country)[index.column()]

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEAD[section]
        return None


class AddressBookTab(QWidget):
    """Onglet « Carnet adresse DMR » : base RadioID.net gardée sur le PC, choix des pays, envoi."""

    PRESETS = {
        "France seulement": {"France"},
        "France et pays voisins": {"France", "Italy", "Monaco", "Switzerland", "Belgium",
                                   "Spain", "Germany", "Luxembourg", "Andorra"},
    }

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.entries: list = []
        self.payload = b""
        v = QVBoxLayout(self)
        intro = QLabel("La radio affiche l'indicatif et le nom de la station qui vous appelle en DMR, "
                       "grâce à la base mondiale des identifiants (RadioID.net). Mettez la liste à jour, "
                       "cochez les pays voulus puis envoyez-la à la radio.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        row = QHBoxLayout()
        b = QPushButton("⟳  Mettre à jour la liste (RadioID.net)")
        b.clicked.connect(self.download)
        row.addWidget(b)
        b = QPushButton("Ouvrir un CSV…")
        b.clicked.connect(self.open_csv)
        row.addWidget(b)
        row.addStretch()
        self.btn_send = QPushButton("Envoyer à la radio")
        self.btn_send.setStyleSheet("font-weight: bold;")
        self.btn_send.clicked.connect(self.send)
        row.addWidget(self.btn_send)
        v.addLayout(row)
        self.src = QLabel()
        v.addWidget(self.src)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Pays à inclure :"))
        prow = QHBoxLayout()
        for name in list(self.PRESETS) + ["Tous", "Aucun"]:
            pb = QPushButton(name)
            pb.clicked.connect(lambda _=False, n=name: self.preset(n))
            prow.addWidget(pb)
        lv.addLayout(prow)
        self.cfilter = QLineEdit(placeholderText="Filtrer la liste des pays…")
        self.cfilter.textChanged.connect(self.filter_countries)
        lv.addWidget(self.cfilter)
        self.list = QListWidget()
        self.list.itemChanged.connect(self.selection_changed)
        lv.addWidget(self.list, 1)
        split.addWidget(left)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit(placeholderText="Rechercher un indicatif, un nom, un ID ou une ville "
                                                "(parmi les pays cochés)…")
        self.search.textChanged.connect(lambda: self._search_timer.start(300))
        rv.addWidget(self.search)
        self.model = EntriesModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        rv.addWidget(self.table, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        v.addWidget(split, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.refresh_table)
        self._loaded = False

    def showEvent(self, e):
        super().showEvent(e)
        if not self._loaded:
            self._loaded = True
            cached = AB.load_cache()
            if cached:
                import datetime
                d = datetime.datetime.fromtimestamp(cached[1]).strftime("%d/%m/%Y à %H:%M")
                self._load(cached[0], f"Liste RadioID.net mise à jour le {d}")
            else:
                self.src.setText("Aucune liste sur ce PC : cliquez sur « Mettre à jour la liste ».")
                self.update_summary()

    def _saved_countries(self):
        val = self.main.settings.value("ab_countries", "France")
        return set(v for v in str(val).split("|") if v)

    def _load(self, entries, label):
        self.entries = entries
        nb = f"{len(entries):,}".replace(",", " ")
        self.src.setText(f"{label} : {nb} identifiants.")
        keep = self._saved_countries()
        self.list.blockSignals(True)
        self.list.clear()
        for name, n in AB.countries(entries):
            it = QListWidgetItem(f"{name}  ({n})")
            it.setData(Qt.UserRole, name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if name in keep else Qt.Unchecked)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.selection_changed()

    def download(self):
        ok, res = TaskDialog.run(self.main, "Mise à jour de la liste RadioID.net", AB.download_radioid)
        if ok:
            import datetime
            self._load(res, "Liste RadioID.net mise à jour le "
                       + datetime.datetime.now().strftime("%d/%m/%Y à %H:%M"))
            self.main.log(f"Liste RadioID.net mise à jour : {len(res)} identifiants.")

    def open_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Base d'identifiants DMR", self.main.last_dir(),
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
        self.selection_changed()

    def filter_countries(self, text):
        q = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(q) and q not in it.text().lower())

    def checked_countries(self) -> set:
        return {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked}

    def selected(self) -> list:
        keep = self.checked_countries()
        return [e for e in self.entries if (e.country or "(inconnu)") in keep]

    def selection_changed(self, *_):
        if self.entries:
            self.main.settings.setValue("ab_countries", "|".join(sorted(self.checked_countries())))
        self.refresh_table()
        self.update_summary()

    def refresh_table(self):
        rows = self.selected() if self.entries else []
        q = self.search.text().strip().lower()
        if q:
            rows = [e for e in rows if q in e.callsign.lower() or q in e.name.lower()
                    or q in e.city.lower() or q in str(e.dmr_id)]
        self.model.set_rows(rows[:200000])

    def update_summary(self):
        sel = self.selected() if self.entries else []
        self.payload = b""
        if not sel:
            self.summary.setText("Sélectionnez au moins un pays.")
            self.btn_send.setEnabled(False)
            return
        try:
            self.payload = AB.build_payload(sel)
        except RadioError as e:
            self.summary.setText(str(e))
            self.btn_send.setEnabled(False)
            return
        n, minutes = AB.estimate(len(self.payload))
        nb = f"{len(sel):,}".replace(",", " ")
        ko = f"{len(self.payload) // 1024:,}".replace(",", " ")
        self.summary.setText(f"<b>{nb} identifiants</b> à envoyer, {ko} Ko — envoi estimé : environ "
                             f"{max(1, round(minutes))} min. Le carnet actuel de la radio sera remplacé.")
        self.btn_send.setEnabled(True)

    def send(self):
        port = self.main.port_name()
        if not port or not self.payload:
            return
        if QMessageBox.question(self, "Carnet d'adresses",
                                "Envoyer ce carnet à la radio ? (radio allumée normalement)") != QMessageBox.Yes:
            return
        self.main.settings.setValue("port", port)
        ok, _ = TaskDialog.run(self.main, "Envoi du carnet d'adresses", AB.write_addressbook, port,
                               self.payload)
        if ok:
            QMessageBox.information(self, "Carnet d'adresses",
                                    "Carnet d'adresses envoyé. Redémarrez la radio si les noms "
                                    "n'apparaissent pas tout de suite.")


class UpdateMenuBar(QMenuBar):
    """Barre de menus : le titre « Mise à jour » passe en orange (gras) quand une version est disponible."""
    ORANGE = QColor("#ff8c1a")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlight_menu = None

    def paintEvent(self, e):
        super().paintEvent(e)
        menu = self.highlight_menu
        if menu is None:
            return
        act = menu.menuAction()
        r = self.actionGeometry(act)
        if r.isEmpty():
            return
        p = QPainter(self)
        active = self.activeAction() is act
        p.fillRect(r, QColor(255, 140, 26, 70) if active else self.palette().color(self.backgroundRole()))
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.setPen(self.ORANGE)
        p.drawText(r, Qt.AlignCenter, menu.title().replace("&", ""))
        p.end()


# ───────────────────────────── fenêtre principale ─────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("F4JCF", "RT4D-Programmer")
        self.setMenuBar(UpdateMenuBar(self))
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
        self.t_start = StartupTab()
        self.t_lim = FreqLimitsTab()
        self.t_ab = AddressBookTab(self)
        self.logbox = QPlainTextEdit(readOnly=True)
        self.logbox.setFont(QFont("Monospace", 9))
        for t, name in ((self.t_ch, "Canaux"), (self.t_ct, "Contacts"), (self.t_gl, "Groupes RX"),
                        (self.t_zn, "Zones"), (self.t_st, "ID DMR"),
                        (self.t_keys, "Réglages touches"), (self.t_start, "Menu démarrage"),
                        (self.t_lim, "Limites de fréquences")):
            self.tabs.addTab(t, name)
            t.changed.connect(self.mark_dirty)
        self.tabs.addTab(self.t_ab, "Carnet adresse DMR")
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
        updater.cleanup_old()
        self.upd = updater.Updater()
        self.upd.checked.connect(self.on_update_checked)
        self.upd.progress.connect(self.on_update_progress)
        self.upd.downloaded.connect(self.on_update_downloaded)
        self._upd_dialog = None
        self._silent_check = False
        if self.act_upd_start.isChecked():
            QTimer.singleShot(4000, lambda: self.upd.check(manual=False))
        # vérification discrète toutes les 6 heures : « Mise à jour » passe en orange sans fenêtre
        self._upd_timer = QTimer(self)
        self._upd_timer.timeout.connect(self._periodic_update_check)
        self._upd_timer.start(6 * 3600 * 1000)
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
        self._act(m, "Importer un codeplug d'ancien firmware (V3.17 ou plus ancien)…",
                  self.import_legacy)
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
        m.addSeparator()
        self._act(m, "Police avec accents (é è ê à ç î É)…", self.font_accents)

        m = self.menuBar().addMenu("F&irmware")
        self._act(m, "Flasher un firmware (mise à jour / retour arrière)…", self.flash_fw)

        m = self.menuBar().addMenu("&Aide")
        self._act(m, "Mode d'emploi", self.show_help, "F1")
        m.addSeparator()
        if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
            self._act(m, "Ajouter l'icône sur le bureau", self.linux_shortcut)
            m.addSeparator()
        self._act(m, "À propos", self.about)

        # Mise à jour, à côté de « Aide » (comme CAT Pilot)
        self.menu_upd = self.menuBar().addMenu("Mise à jour")
        self._act(self.menu_upd, "Vérifier les mises à jour…", lambda: self.upd.check(manual=True))
        self.act_upd_start = QAction("Vérifier au démarrage", self, checkable=True)
        self.act_upd_start.setChecked(self.settings.value("update_check", "true") == "true")
        self.act_upd_start.toggled.connect(
            lambda on: self.settings.setValue("update_check", "true" if on else "false"))
        self.menu_upd.addAction(self.act_upd_start)
        self._act(self.menu_upd, "Voir toutes les versions sur GitHub", self.open_releases_page)

    def linux_shortcut(self):
        linux_desktop.install(desktop_shortcut=True)
        QMessageBox.information(
            self, "Icône", "L'icône RT4D Programmer a été ajoutée sur le bureau et dans le menu "
            "des applications.\n\nSur certains bureaux (GNOME), faites un clic droit sur l'icône "
            "puis « Autoriser le lancement » la première fois.")

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
        if getattr(self.cp, "read_only", False):
            return
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
        elif getattr(cp, "kind", "refw") == "legacy":
            txt, col = ("Ancien firmware officiel Radtel (V3.17 ou plus ancien) : lecture et "
                        "écriture disponibles, comme avec le logiciel Radtel CPS V1.19."), "#1e7e34"
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
        for t in (self.t_ch, self.t_ct, self.t_gl, self.t_zn, self.t_st, self.t_keys, self.t_start,
                  self.t_lim):
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
        ext = ".rt4d" if getattr(self.cp, "kind", "refw") in ("stock", "legacy") else ".4rdmf"
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

    def import_legacy(self):
        if getattr(self.cp, "kind", "refw") != "stock":
            QMessageBox.information(
                self, "Importer un ancien codeplug",
                "Lisez d'abord la radio mise à jour (firmware officiel V3.18 ou plus récent) : "
                "l'ancien codeplug sera copié dans ce qui a été lu, puis vous pourrez l'écrire.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Codeplug d'ancien firmware", self.last_dir(),
            "Codeplug ou sauvegarde (*.rt4d *.bin);;Tous les fichiers (*)")
        if not path:
            return
        try:
            old = radio_ops.load_any(path)
        except Exception as e:
            QMessageBox.critical(self, "Importer un ancien codeplug", str(e))
            return
        if getattr(old, "kind", "") != "legacy":
            QMessageBox.warning(self, "Importer un ancien codeplug",
                                "Ce fichier ne vient pas d'un ancien firmware Radtel "
                                "(V3.17 ou plus ancien). Ouvrez-le avec Fichier > Ouvrir.")
            return
        r = QMessageBox.question(
            self, "Importer un ancien codeplug",
            f"{len(old.channels)} canaux, {len(old.contacts)} contacts, {len(old.groups)} groupes RX "
            f"et {len(old.zones)} zones vont REMPLACER ceux du codeplug affiché, avec l'ID DMR "
            f"et le nom de la radio.\n\nLes autres réglages de la radio sont conservés. Continuer ?")
        if r != QMessageBox.Yes:
            return
        notes = legacy.convert_into(old, self.cp)
        self.cp.commit()
        self.set_last_dir(path)
        self.load_codeplug(self.cp, self.path)
        self.mark_dirty()
        self.log(f"Ancien codeplug importé : {path}")
        for n in notes:
            self.log("  " + n)
        msg = ("Import terminé.\n\nVérifiez surtout, canal par canal, la puissance, la largeur de "
               "bande et le balayage : ces réglages de l'ancien firmware ont été convertis au "
               "mieux. Écrivez ensuite dans la radio.")
        if notes:
            msg += "\n\nRemarques :\n• " + "\n• ".join(notes[:15])
        QMessageBox.information(self, "Importer un ancien codeplug", msg)

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
        self.tabs.setCurrentWidget(self.t_ab)

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

    def font_accents(self):
        port = self.port_name()
        if not port:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Police avec accents")
        box.setTextFormat(Qt.RichText)
        box.setText(
            "<b>Police avec accents pour le firmware officiel Radtel</b><br><br>"
            "La police du poste n'a pas d'accents. Cette fonction redessine 7 caractères inutilisés "
            "par les menus :<br>"
            "<code>`</code> → é &nbsp; <code>{</code> → è &nbsp; <code>^</code> → ê &nbsp; "
            "<code>}</code> → à &nbsp; <code>|</code> → ç &nbsp; <code>~</code> → î &nbsp; "
            "<code>\\</code> → É<br><br>"
            "Elle est nécessaire au firmware traduit avec accents (FR03). Sans le firmware FR03, "
            "les menus ne changent pas.<br><br>"
            "Seule la police (8 Ko) est réécrite. L'ancienne police est d'abord enregistrée dans le "
            "dossier des sauvegardes, puis la police écrite est relue et vérifiée.<br>"
            "Radio allumée <b>normalement</b> (pas en mode PTT).")
        b_inst = box.addButton("Installer les accents", QMessageBox.AcceptRole)
        b_rest = box.addButton("Remettre une police enregistrée…", QMessageBox.ActionRole)
        box.addButton("Annuler", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        from . import fontpatch as P
        if clicked is b_inst:
            ok, font = TaskDialog.run(self, "Lecture de la police", radio_ops.read_font, port)
            if not ok:
                return
            if P.is_accented(font):
                QMessageBox.information(self, "Police", "La radio contient déjà la police avec accents.")
                return
            new = P.build_accented(font)
        elif clicked is b_rest:
            path, _ = QFileDialog.getOpenFileName(
                self, "Police ou sauvegarde complète", self.backup_dir(),
                "Police (8 Ko) ou sauvegarde complète (4 Mo) (*.bin);;Tous les fichiers (*)")
            if not path:
                return
            try:
                new = radio_ops.font_from_file(path)
            except RadioError as e:
                QMessageBox.critical(self, "Police", str(e))
                return
        else:
            return
        ok, res = TaskDialog.run(self, "Écriture de la police", radio_ops.write_font, port, new,
                                 self.backup_dir())
        if ok:
            QMessageBox.information(self, "Police", "Police écrite et vérifiée.\n\nÉteignez puis "
                                    f"rallumez la radio.\n\nAncienne police enregistrée :\n{res}")

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

    # -- mises à jour (même fonctionnement que CAT Pilot) --
    def set_update_flag(self, available, version=""):
        """Titre « Mise à jour » en orange tant qu'une nouvelle version attend."""
        self.menuBar().highlight_menu = self.menu_upd if available else None
        self.menu_upd.menuAction().setToolTip(f"Nouvelle version disponible : {version}" if available else "")
        self.menuBar().update()

    def _periodic_update_check(self):
        if self.act_upd_start.isChecked():
            self._silent_check = True
            self.upd.check(manual=False)

    def open_releases_page(self):
        QDesktopServices.openUrl(QUrl(updater.PAGE))

    def on_update_checked(self, info, manual):
        silent, self._silent_check = self._silent_check, False
        if isinstance(info, Exception):
            self.log(f"Vérification des mises à jour impossible : {info}")
            if manual:
                QMessageBox.warning(self, "Mise à jour",
                                    f"Impossible de vérifier les mises à jour.\n\n{info}")
            return
        if not updater.is_newer(info["version"]):
            self.set_update_flag(False)
            if manual:
                QMessageBox.information(self, "Mise à jour",
                                        f"Vous avez déjà la dernière version ({__version__}).")
            return
        self.set_update_flag(True, info["version"])
        self.log(f"Nouvelle version disponible : {info['version']}")
        if silent and not manual:
            return
        if not manual and self.settings.value("update_skip", "") == info["version"]:
            return
        notes = info["notes"].strip()
        if len(notes) > 900:
            notes = notes[:900] + "…"
        box = QMessageBox(self)
        box.setWindowTitle("Mise à jour disponible")
        box.setText(f"<b>RT4D Programmer {info['version']}</b> est disponible "
                    f"(vous avez la {__version__}).")
        box.setInformativeText((notes + "\n\n" if notes else "") +
                               ("Le logiciel sera fermé, mis à jour puis relancé automatiquement."
                                if updater.can_self_update() and info.get("url") else
                                "La page de téléchargement va s'ouvrir."))
        b_now = box.addButton("Mettre à jour maintenant", QMessageBox.AcceptRole)
        box.addButton("Plus tard", QMessageBox.RejectRole)
        b_skip = box.addButton("Ignorer cette version", QMessageBox.DestructiveRole) if not manual else None
        box.setDefaultButton(b_now)
        box.exec()
        if b_skip is not None and box.clickedButton() is b_skip:
            self.settings.setValue("update_skip", info["version"])
            return
        if box.clickedButton() is not b_now:
            return
        if not updater.can_self_update() or not info.get("url"):
            self.open_releases_page()
            return
        if not self.confirm_discard():
            return
        self._upd_dialog = QProgressDialog(f"Téléchargement de RT4D Programmer {info['version']}…",
                                           "Annuler", 0, 100, self)
        self._upd_dialog.setWindowTitle("Mise à jour")
        self._upd_dialog.setWindowModality(Qt.WindowModal)
        self._upd_dialog.setMinimumDuration(0)
        self._upd_dialog.canceled.connect(self.upd.cancel)
        self._upd_dialog.setValue(0)
        self.upd.download(info)

    def on_update_progress(self, done, total):
        dlg = self._upd_dialog
        if dlg and total:
            dlg.setLabelText(f"Téléchargement… {done / 1e6:.1f} / {total / 1e6:.1f} Mo")
            dlg.setValue(min(99, int(done * 100 / total)))

    def on_update_downloaded(self, path):
        if self._upd_dialog:
            self._upd_dialog.reset()
            self._upd_dialog = None
        if isinstance(path, Exception):
            if "annulé" not in str(path):
                if QMessageBox.question(self, "Mise à jour",
                                        f"Le téléchargement a échoué.\n\n{path}\n\n"
                                        "Ouvrir la page de téléchargement ?") == QMessageBox.Yes:
                    self.open_releases_page()
            return
        try:
            updater.install_and_restart(path)
        except Exception as e:
            QMessageBox.warning(self, "Mise à jour", f"L'installation n'a pas pu démarrer.\n\n{e}")
            return
        self.dirty = False
        self.log("Mise à jour : redémarrage de RT4D Programmer…")
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
    if "--installer-menu" in sys.argv:          # utilisé par install.sh (Linux)
        linux_desktop.install(desktop_shortcut="--bureau" in sys.argv)
        print("RT4D Programmer : icône et entrée de menu installées.")
        return
    if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
        linux_desktop.install()                   # icône + menu des applications
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName(linux_desktop.APP_ID)
    app.setStyle("Fusion")
    icon = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(__file__))), "icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
