"""Fenêtre « Importer un plan de fréquences » (Fichier → Importer un plan de fréquences)."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from . import freqplan as FP
from .codeplug import ANALOG_MODS, fmt_mhz

HELP = (
    "Choisissez un fichier de plan de fréquences :<br>"
    "• un export de <b>CHIRP</b> (.csv) ;<br>"
    "• un CSV avec une colonne <i>Fréquence</i> (ou RX, Frequency) et, si vous voulez, <i>Nom</i>, "
    "<i>TX</i> ou <i>Décalage</i>, <i>Sous-ton</i>, <i>Mode</i>, <i>Puissance</i> "
    "(une liste de relais, un tableur exporté en CSV…) ;<br>"
    "• un simple texte : une fréquence par ligne, puis le nom (« 145,500 S20 »).<br>"
    "Les canaux sont ajoutés dans les emplacements <b>libres</b> : aucun canal existant n'est modifié.")


class PlanImportDialog(QDialog):
    def __init__(self, win, cp, start_dir: str):
        super().__init__(win)
        self.cp = cp
        self.plan = None
        self.start_dir = start_dir
        self.setWindowTitle("Importer un plan de fréquences")
        self.resize(820, 600)
        v = QVBoxLayout(self)
        lab = QLabel(HELP)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.RichText)
        v.addWidget(lab)
        row = QHBoxLayout()
        self.file = QLineEdit(readOnly=True)
        b = QPushButton("Choisir le fichier…")
        b.clicked.connect(self.pick)
        row.addWidget(self.file, 1)
        row.addWidget(b)
        v.addLayout(row)
        self.info = QLabel()
        self.info.setWordWrap(True)
        v.addWidget(self.info)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Nom", "RX (MHz)", "TX (MHz)", "Mode", "Ton RX",
                                              "Ton TX", "Émission", "Remarque"])
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.table, 1)

        f = QFormLayout()
        self.start = QSpinBox()
        self.start.setRange(1, cp.LIMITS["channels"])
        free = cp.free_slot(cp.channels, cp.LIMITS["channels"])
        self.start.setValue((free or 0) + 1)
        self.start.setToolTip("Les canaux vont dans les emplacements libres à partir de ce numéro.")
        f.addRow("À partir du canal n°", self.start)
        self.zone = QLineEdit()
        self.zone.setMaxLength(16)
        self.zone.setPlaceholderText("vide = pas de zone")
        f.addRow("Créer la zone", self.zone)
        self.prefix = QLineEdit()
        self.prefix.setMaxLength(6)
        self.prefix.setPlaceholderText("facultatif, ex. « A06 »")
        f.addRow("Début des noms", self.prefix)
        v.addLayout(f)
        self.dup = QCheckBox("Ne pas ajouter une fréquence déjà présente (même RX et même TX)")
        self.dup.setChecked(True)
        self.high = QCheckBox("Puissance haute quand le plan ne précise pas la puissance")
        self.rxonly = QCheckBox("« RX seul » pour les canaux hors des bandes radioamateur "
                                "144-146 et 430-440 MHz (aviation, marine, PMR…)")
        self.rxonly.setChecked(True)
        for w in (self.dup, self.high, self.rxonly):
            w.toggled.connect(self.refresh)
            v.addWidget(w)
        note = QLabel("<i>Pour interdire vraiment l'émission hors bandes, utilisez aussi l'onglet "
                      "Limites de fréquences.</i>")
        note.setTextFormat(Qt.RichText)
        v.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.okbtn = bb.button(QDialogButtonBox.Ok)
        self.okbtn.setText("Ajouter au codeplug")
        self.okbtn.setEnabled(False)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def options(self) -> FP.ImportOptions:
        return FP.ImportOptions(start=self.start.value(), zone_name=self.zone.text().strip(),
                                skip_duplicates=self.dup.isChecked(),
                                power_high=self.high.isChecked(),
                                rx_only_outside_ham=self.rxonly.isChecked(),
                                name_prefix=(self.prefix.text().strip() + " ")
                                if self.prefix.text().strip() else "")

    def pick(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Plan de fréquences", self.start_dir,
            "Plans de fréquences (*.csv *.txt *.tsv);;Tous les fichiers (*)")
        if not path:
            return
        self.start_dir = os.path.dirname(path)
        self.file.setText(path)
        try:
            self.plan = FP.read_plan(path)
        except OSError as e:
            self.plan = None
            self.info.setText(f"<span style='color:#c0392b'>{e}</span>")
            self.okbtn.setEnabled(False)
            return
        self.refresh()

    def refresh(self):
        p = self.plan
        if p is None:
            return
        existing = {(c.rx, c.tx) for c in self.cp.channels.values()}
        self.table.setRowCount(len(p.entries))
        dups = 0
        for i, e in enumerate(p.entries):
            dup = (e.rx, e.tx) in existing
            dups += dup
            rx_only = e.rx_only or (self.rxonly.isChecked() and not e.ham_tx)
            mode = (ANALOG_MODS[e.modulation] + (" étroit" if e.narrow else "")) if e.analog else "DMR"
            remark = "déjà présent" if dup and self.dup.isChecked() else e.note
            cells = [e.name, fmt_mhz(e.rx), fmt_mhz(e.tx), mode, e.rx_tone, e.tx_tone,
                     "RX seul" if rx_only else "autorisée", remark]
            for k, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                if dup and self.dup.isChecked():
                    it.setForeground(QColor("#999999"))
                elif k == 6 and rx_only:
                    it.setForeground(QColor("#b9770e"))
                self.table.setItem(i, k, it)
        self.table.resizeColumnsToContents()
        n = len(p.entries) - (dups if self.dup.isChecked() else 0)
        free = self.cp.LIMITS["channels"] - len(self.cp.channels)
        txt = f"Format reconnu : <b>{p.fmt}</b> — {len(p.entries)} fréquence(s), {n} à ajouter"
        txt += f" ({free} emplacements libres)."
        if n > free:
            txt += " <span style='color:#c0392b'>Pas assez de place : la fin du plan ne sera pas ajoutée.</span>"
        if p.warnings:
            txt += "<br><span style='color:#b9770e'>" + "<br>".join(p.warnings[:8])
            if len(p.warnings) > 8:
                txt += f"<br>… et {len(p.warnings) - 8} autre(s)"
            txt += "</span>"
        self.info.setText(txt)
        self.okbtn.setEnabled(n > 0)
