"""Fenêtre « Journal F4JCF » (Radio → Journal F4JCF), idée n°29 : carnet de relevés SATER et derniers
DMR entendus, gardés par la radio (firmware F4JCF19 ou plus récent). Lecture seule."""
from __future__ import annotations

import os

import datetime as dt
import webbrowser

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDateTimeEdit, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from . import exercice as X
from . import journal as J
from . import radio_ops

INFO = (
    "La radio (firmware <b>F4JCF19</b> ou plus récent) garde le <b>carnet de relevés du mode SATER</b> "
    "et la liste des <b>derniers DMR entendus</b>. Elle les enregistre en quittant le Menu F4JCF, "
    "s'ils ont changé. Cette fenêtre les lit dans la radio ou dans une sauvegarde complète, "
    "sans rien modifier.")

REL_COLS = ["N°", "Heure", "Niveau max", "Chrono", "Touche", "Azimut", "Position"]
DMR_COLS = ["Indicatif", "ID", "Groupe", "Passages", "Dernier passage"]


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

        # heure d'allumage (la radio n'a pas d'horloge) et déclinaison
        row = QHBoxLayout()
        self.cb_on = QCheckBox("Radio allumée le")
        self.cb_on.setToolTip("La radio n'a pas d'horloge : elle compte les secondes depuis l'allumage. "
                              "Indiquez l'heure d'allumage pour obtenir des heures réelles.")
        self.dt_on = QDateTimeEdit(QDateTime.currentDateTime())
        self.dt_on.setDisplayFormat("dd/MM/yyyy HH:mm:ss")
        self.dt_on.setCalendarPopup(True)
        self.cb_on.toggled.connect(lambda _: self.fill())
        self.dt_on.dateTimeChanged.connect(lambda _: self.cb_on.isChecked() and self.fill())
        row.addWidget(self.cb_on)
        row.addWidget(self.dt_on)
        row.addSpacing(16)
        row.addWidget(QLabel("Déclinaison magnétique"))
        self.decl = QDoubleSpinBox()
        self.decl.setRange(-30, 30)
        self.decl.setDecimals(1)
        self.decl.setSuffix(" °")
        self.decl.setValue(2.0)
        self.decl.setToolTip("Ajoutée aux azimuts (positive vers l'est ; environ +2° en France en 2026)")
        row.addWidget(self.decl)
        row.addStretch(1)
        v.addLayout(row)

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
        self.b_csv = QPushButton("CSV…")
        self.b_csv.clicked.connect(self.save_csv)
        self.b_kml = QPushButton("KML…")
        self.b_kml.setToolTip("Google Earth, cartes : relevés, relèvements, balise estimée")
        self.b_kml.clicked.connect(lambda: self.save_map("kml"))
        self.b_gpx = QPushButton("GPX…")
        self.b_gpx.setToolTip("GPS, applications de randonnée : points de relevé, balise estimée, relèvements")
        self.b_gpx.clicked.connect(lambda: self.save_map("gpx"))
        self.b_rep = QPushButton("Compte rendu…")
        self.b_rep.setToolTip("Page à imprimer ou enregistrer en PDF : résultat, plan, relevés, stations DMR")
        self.b_rep.clicked.connect(self.make_report)
        for b in (self.b_csv, self.b_kml, self.b_gpx, self.b_rep):
            b.setEnabled(False)
            row.addWidget(b)
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
        extra = ""
        if self.jr.up_save is not None:
            h, m = divmod(self.jr.up_save // 60, 60)
            extra = (" Journal écrit %d h %02d min après l'allumage de la radio." % (h, m))
            if src == "Radio" and not self.cb_on.isChecked():
                # estimation : journal écrit juste avant la lecture (sortie du Menu F4JCF)
                self.dt_on.setDateTime(QDateTime.currentDateTime().addSecs(-self.jr.up_save))
                extra += " Heure d'allumage estimée (à vérifier), cochez la case pour l'utiliser."
        elif self.jr.version == 1:
            extra = " Journal sans heures (firmware antérieur à F4JCF22)."
        self.state.setText("<b>%s</b> : %d relevé(s) SATER, %d station(s) DMR.%s"
                           % (src, len(self.jr.releves), len(self.jr.stations), extra))
        self.fill()

    def fill(self):
        jr = self.jr or J.Journal()
        self.t_rel.setRowCount(len(jr.releves))
        for i, r in enumerate(jr.releves):
            vals = [str(r.num), J.when(jr, r.t, r.t_prev, self.power_on()), "%d dBm" % r.dbm, r.chrono, str(r.touche),
                    "—" if r.azimut is None else "%d°" % r.azimut, r.position or "—"]
            for c, x in enumerate(vals):
                self.t_rel.setItem(i, c, QTableWidgetItem(x))
        self.t_dmr.setRowCount(len(jr.stations))
        for i, s in enumerate(jr.stations):
            for c, x in enumerate([s.indicatif or "—", str(s.id), s.appel, str(s.passages),
                                   J.when(jr, s.t, s.t_prev, self.power_on())]):
                self.t_dmr.setItem(i, c, QTableWidgetItem(x))
        for b in (self.b_csv, self.b_kml, self.b_gpx, self.b_rep):
            b.setEnabled(self.jr is not None)
        self.b_tri.setEnabled(any(r.azimut is not None and r.lat is not None for r in jr.releves))

    def power_on(self):
        if not self.cb_on.isChecked():
            return None
        return self.dt_on.dateTime().toPython()

    # --- sorties
    def save_csv(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Enregistrer le journal", "journal_f4jcf.csv", "CSV (*.csv)")
        if fn:
            J.save_csv(self.jr, fn, self.power_on())

    def save_map(self, kind):
        if not any(r.lat is not None for r in self.jr.releves):
            QMessageBox.information(self, "Export", "Aucun relevé avec une position.")
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Exporter les relevés", "releves_sater." + kind,
                                            "%s (*.%s)" % (kind.upper(), kind))
        if not fn:
            return
        txt = (X.to_kml if kind == "kml" else X.to_gpx)(self.jr, self.decl.value(), self.power_on())
        with open(fn, "w", encoding="utf-8") as f:
            f.write(txt)

    def make_report(self):
        d = QDialog(self)
        d.setWindowTitle("Compte rendu d'exercice")
        f = QFormLayout(d)
        po = self.power_on()
        ed = {k: QLineEdit(v) for k, v in (
            ("titre", "Compte rendu d'exercice SATER"),
            ("date", (po or dt.datetime.now()).strftime("%d/%m/%Y")),
            ("lieu", ""), ("operateur", "F4JCF — ADRASEC 06"), ("balise", ""))}
        for k, lab in (("titre", "Titre"), ("date", "Date"), ("lieu", "Lieu"), ("operateur", "Opérateur(s)"),
                       ("balise", "Balise / objectif")):
            f.addRow(lab, ed[k])
        notes = QPlainTextEdit()
        notes.setPlaceholderText("Déroulement, conditions, matériel, remarques…")
        f.addRow("Observations", notes)
        row = QHBoxLayout()
        ok = QPushButton("Créer")
        ok.clicked.connect(d.accept)
        no = QPushButton("Annuler")
        no.clicked.connect(d.reject)
        row.addStretch(1)
        row.addWidget(ok)
        row.addWidget(no)
        f.addRow(row)
        if not d.exec():
            return
        meta = {k: w.text() for k, w in ed.items()}
        meta["notes"] = notes.toPlainText()
        fn, _ = QFileDialog.getSaveFileName(self, "Enregistrer le compte rendu", "compte_rendu_exercice.html",
                                            "Page web (*.html)")
        if not fn:
            return
        with open(fn, "w", encoding="utf-8") as fh:
            fh.write(X.report(self.jr, meta, self.decl.value(), po))
        if QMessageBox.question(self, "Compte rendu", "Compte rendu enregistré. L'ouvrir dans le navigateur "
                                "(impression ou PDF) ?") == QMessageBox.Yes:
            webbrowser.open("file:///" + fn.replace("\\", "/"))

    def to_triangulation(self):
        rows = [(r.position, str(r.azimut), str(r.dbm), "relevé %d (touche %d, %s)" % (r.num, r.touche, r.chrono))
                for r in self.jr.releves if r.azimut is not None and r.lat is not None]
        self.main.triangulation()
        self.main._tri.set_rows(rows)
        QMessageBox.information(self, "Triangulation", "%d relevé(s) avec position et azimut envoyé(s)."
                                % len(rows))
