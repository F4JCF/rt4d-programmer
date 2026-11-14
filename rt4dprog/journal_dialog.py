"""Fenêtre « Journal F4JCF » (Radio → Journal F4JCF), idée n°29 : carnet de relevés SATER et derniers
DMR entendus, gardés par la radio (firmware F4JCF19 ou plus récent). Lecture seule."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from . import journal as J
from . import radio_ops

INFO = (
    "La radio (firmware <b>F4JCF19</b> ou plus récent) garde le <b>carnet de relevés du mode SATER</b> "
    "et la liste des <b>derniers DMR entendus</b>. Elle les enregistre en quittant le Menu F4JCF, "
    "s'ils ont changé. Cette fenêtre les lit dans la radio ou dans une sauvegarde complète, "
    "sans rien modifier.")

REL_COLS = ["N°", "Niveau max", "Chrono", "Touche", "Azimut", "Position"]
DMR_COLS = ["Indicatif", "ID", "Groupe", "Passages"]


def _table(cols):
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.setEditTriggers(QTableWidget.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(len(cols) - 1, QHeaderView.Stretch)
    return t


class JournalDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.jr: J.Journal | None = None
        self.setWindowTitle("Journal F4JCF — carnet SATER et derniers DMR")
        self.resize(760, 640)
        v = QVBoxLayout(self)
        lab = QLabel(INFO)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.RichText)
        v.addWidget(lab)
        self.state = QLabel("Rien de lu.")
        self.state.setWordWrap(True)
        v.addWidget(self.state)

        g = QGroupBox("Carnet SATER (touche # du mode SATER)")
        gl = QVBoxLayout(g)
        self.t_rel = _table(REL_COLS)
        gl.addWidget(self.t_rel)
        self.b_tri = QPushButton("Envoyer les relevés vers la triangulation")
        self.b_tri.setEnabled(False)
        self.b_tri.clicked.connect(self.to_triangulation)
        gl.addWidget(self.b_tri)
        v.addWidget(g, 1)

        g = QGroupBox("Derniers DMR (le plus récent en premier)")
        gl = QVBoxLayout(g)
        self.t_dmr = _table(DMR_COLS)
        gl.addWidget(self.t_dmr)
        gl.addWidget(QLabel("<i>Groupes au-delà de 65535 (TG à 6 chiffres) : la radio n'en garde que "
                            "les 16 bits de poids faible.</i>"))
        v.addWidget(g, 1)

        row = QHBoxLayout()
        b = QPushButton("Lire la radio")
        b.clicked.connect(self.read_radio)
        row.addWidget(b)
        b = QPushButton("Ouvrir une sauvegarde…")
        b.clicked.connect(self.open_file)
        row.addWidget(b)
        self.b_csv = QPushButton("Enregistrer en CSV…")
        self.b_csv.setEnabled(False)
        self.b_csv.clicked.connect(self.save_csv)
        row.addWidget(self.b_csv)
        row.addStretch(1)
        b = QPushButton("Fermer")
        b.clicked.connect(self.close)
        row.addWidget(b)
        v.addLayout(row)

    # --- sources
    def read_radio(self):
        port = self.main.port_name()
        if not port:
            return
        from .gui import TaskDialog
        ok, sector = TaskDialog.run(self, "Lecture du journal F4JCF", radio_ops.read_f4set, port)
        if ok:
            self.load(sector, "Radio")

    def open_file(self):
        fn, _ = QFileDialog.getOpenFileName(
            self, "Ouvrir une sauvegarde", self.main.backup_dir(),
            "Sauvegardes (*.bin);;Tous les fichiers (*)")
        if not fn:
            return
        with open(fn, "rb") as f:
            data = f.read()
        self.load(data, os.path.basename(fn))

    def load(self, data: bytes, src: str):
        try:
            self.jr = J.parse(data)
        except J.JournalError as e:
            self.jr = None
            self.state.setText("<b>%s</b> : %s" % (src, e))
            self.fill()
            return
        self.state.setText("<b>%s</b> : %d relevé(s) SATER, %d station(s) DMR."
                           % (src, len(self.jr.releves), len(self.jr.stations)))
        self.fill()

    def fill(self):
        jr = self.jr or J.Journal()
        self.t_rel.setRowCount(len(jr.releves))
        for i, r in enumerate(jr.releves):
            vals = [str(r.num), "%d dBm" % r.dbm, r.chrono, str(r.touche),
                    "—" if r.azimut is None else "%d°" % r.azimut, r.position or "—"]
            for c, x in enumerate(vals):
                self.t_rel.setItem(i, c, QTableWidgetItem(x))
        self.t_dmr.setRowCount(len(jr.stations))
        for i, s in enumerate(jr.stations):
            for c, x in enumerate([s.indicatif or "—", str(s.id), s.appel, str(s.passages)]):
                self.t_dmr.setItem(i, c, QTableWidgetItem(x))
        self.b_csv.setEnabled(self.jr is not None)
        self.b_tri.setEnabled(any(r.azimut is not None and r.lat is not None for r in jr.releves))

    # --- sorties
    def save_csv(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Enregistrer le journal", "journal_f4jcf.csv", "CSV (*.csv)")
        if fn:
            J.save_csv(self.jr, fn)

    def to_triangulation(self):
        rows = [(r.position, str(r.azimut), str(r.dbm), "relevé %d (touche %d, %s)" % (r.num, r.touche, r.chrono))
                for r in self.jr.releves if r.azimut is not None and r.lat is not None]
        self.main.triangulation()
        self.main._tri.set_rows(rows)
        QMessageBox.information(self, "Triangulation", "%d relevé(s) avec position et azimut envoyé(s)."
                                % len(rows))
