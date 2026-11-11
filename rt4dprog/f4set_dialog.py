"""Fenêtre « Réglages des modes F4JCF » (Radio → Réglages des modes F4JCF), idée n°30."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout,
)

from . import f4set as FS
from . import radio_ops
from .codeplug import fmt_mhz

INFO = (
    "Réglages de départ des modes du <b>firmware F4JCF15</b> (ou plus récent). Ils sont rangés dans "
    "la mémoire du poste, à côté du logo de démarrage (le logo est relu et réécrit à l'identique). "
    "Le poste les prend en compte <b>au prochain allumage</b>. Un poste sans F4JCF15 les ignore.<br>"
    "Lisez d'abord la radio, modifiez, puis écrivez. Le secteur d'origine est enregistré sur "
    "l'ordinateur avant chaque écriture.")


class F4SetDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.sector = None                  # secteur lu dans la radio (logo + réglages)
        self.setWindowTitle("Réglages des modes F4JCF")
        self.resize(640, 640)
        v = QVBoxLayout(self)
        lab = QLabel(INFO)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.RichText)
        v.addWidget(lab)
        self.state = QLabel("Pas encore lu : valeurs d'origine affichées.")
        self.state.setWordWrap(True)
        v.addWidget(self.state)

        g = QGroupBox("SATER — canaux des touches 1 à 4")
        f = QFormLayout(g)
        self.ch = []
        self.ch_lab = []
        for i in range(4):
            sp = QSpinBox()
            sp.setRange(1 if i == 0 else 0, 1024)
            if i:
                sp.setSpecialValueText("aucun")
            sp.valueChanged.connect(lambda _=0, i=i: self.show_ch(i))
            lb = QLabel()
            row = QHBoxLayout()
            row.addWidget(sp)
            row.addWidget(lb, 1)
            self.ch.append(sp)
            self.ch_lab.append(lb)
            f.addRow(f"Touche {i + 1}", row)
        f.addRow(QLabel("<i>D'origine : " + FS.describe_default_channels() + ".</i>"))
        v.addWidget(g)

        g = QGroupBox("Balise d'exercice — valeurs au lancement (les touches changent ensuite)")
        f = QFormLayout(g)
        self.call = QLineEdit()
        self.call.setMaxLength(8)
        self.call.setPlaceholderText("vide = début du « Nom radio »")
        self.call.textEdited.connect(lambda t: self.call.setText(
            "".join(c for c in t.upper() if c.isascii() and c.isalnum())))
        f.addRow("Indicatif envoyé en morse", self.call)
        self.cycle = QSpinBox(); self.cycle.setRange(10, 999); self.cycle.setSuffix(" s")
        f.addRow("Intervalle entre deux émissions", self.cycle)
        self.long = QSpinBox(); self.long.setRange(0, 20); self.long.setSuffix(" s")
        self.long.setSpecialValueText("aucun")
        f.addRow("Ton long après l'indicatif", self.long)
        self.tone = QSpinBox(); self.tone.setRange(300, 2500); self.tone.setSingleStep(50)
        self.tone.setSuffix(" Hz")
        f.addRow("Hauteur du ton", self.tone)
        self.num = QSpinBox(); self.num.setRange(0, 5); self.num.setSpecialValueText("aucun")
        f.addRow("Numéro de balise (tops courts)", self.num)
        f.addRow(QLabel("<i>Sécurités inchangées : 144-146 / 430-440 MHz, puissance basse, "
                        "25 s d'émission au plus, rien avant l'appui sur 5.</i>"))
        v.addWidget(g)

        g = QGroupBox("Rappel d'indicatif")
        f = QFormLayout(g)
        self.rappel = QSpinBox(); self.rappel.setRange(0, 60); self.rappel.setSuffix(" min")
        self.rappel.setSpecialValueText("arrêt")
        f.addRow("Double bip après une émission, au bout de", self.rappel)
        v.addWidget(g)

        row = QHBoxLayout()
        for text, fn in (("Lire la radio", self.read), ("Écrire dans la radio", self.write),
                         ("Valeurs d'origine", lambda: self.set_values(FS.F4Settings())),
                         ("Ouvrir…", self.open_file), ("Enregistrer…", self.save_file)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
            if text.startswith("Écrire"):
                self.wbtn = b
        v.addLayout(row)
        b = QPushButton("Fermer")
        b.clicked.connect(self.close)
        v.addWidget(b, alignment=Qt.AlignRight)
        self.set_values(FS.F4Settings())
        self.wbtn.setEnabled(False)

    # ── valeurs ──
    def show_ch(self, i):
        n = self.ch[i].value()
        cp = getattr(self.main, "cp", None)
        txt = ""
        if n and cp is not None and hasattr(cp, "channels"):
            c = cp.channels.get(n - 1)
            txt = f"{c.name}  {fmt_mhz(c.rx)} MHz" if c else "emplacement vide dans le codeplug ouvert"
        self.ch_lab[i].setText(txt)

    def set_values(self, s: FS.F4Settings):
        for sp, v in zip(self.ch, s.sater_ch):
            sp.setValue(v)
        self.call.setText(s.bal_call)
        self.cycle.setValue(s.bal_cycle)
        self.long.setValue(s.bal_long)
        self.tone.setValue(s.bal_tone)
        self.num.setValue(s.bal_num)
        self.rappel.setValue(s.rappel_min)
        for i in range(4):
            self.show_ch(i)

    def values(self) -> FS.F4Settings:
        return FS.F4Settings(sater_ch=[sp.value() for sp in self.ch], bal_cycle=self.cycle.value(),
                             bal_long=self.long.value(), bal_num=self.num.value(),
                             bal_tone=self.tone.value(), rappel_min=self.rappel.value(),
                             bal_call=self.call.text().strip())

    # ── radio ──
    def read(self):
        port = self.main.port_name()
        if not port:
            return
        from .gui import TaskDialog
        ok, sector = TaskDialog.run(self, "Lecture des réglages F4JCF", radio_ops.read_f4set, port)
        if not ok:
            return
        self.sector = sector
        s, st = FS.decode(sector[FS.SET_OFF:FS.SET_OFF + FS.SIZE])
        self.set_values(s)
        self.state.setText({
            "ok": "Réglages lus dans la radio.",
            "vide": "Aucun réglage F4JCF dans la radio : valeurs d'origine affichées.",
            "abîmé": "Réglages de la radio illisibles (abîmés) : valeurs d'origine affichées ; "
                     "le poste utilise lui aussi les valeurs d'origine."}[st])
        self.wbtn.setEnabled(True)

    def write(self):
        if self.sector is None:
            QMessageBox.information(self, "Réglages F4JCF", "Lisez d'abord la radio.")
            return
        s = self.values()
        err = s.check()
        if err:
            QMessageBox.warning(self, "Réglages F4JCF", "\n".join(err))
            return
        port = self.main.port_name()
        if not port:
            return
        from .gui import TaskDialog
        ok, bk = TaskDialog.run(self, "Écriture des réglages F4JCF", radio_ops.write_f4set, port, s,
                                self.sector, self.main.backup_dir())
        if ok:
            self.sector = FS.build_sector(self.sector, s)
            self.state.setText("Réglages écrits et vérifiés. Éteignez puis rallumez le poste pour "
                               f"qu'il les prenne en compte. Sauvegarde : {os.path.basename(bk)}")

    # ── fichiers ──
    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Réglages F4JCF", self.main.last_dir(),
            "Réglages F4JCF ou sauvegarde (*.f4set *.bin);;Tous les fichiers (*)")
        if not path:
            return
        data = open(path, "rb").read()
        if len(data) >= 0x101000:                       # sauvegarde complète de la flash
            blk = data[FS.SET_ADDR:FS.SET_ADDR + FS.SIZE]
        elif len(data) == FS.SECTOR_SIZE:               # secteur du logo enregistré
            blk = data[FS.SET_OFF:FS.SET_OFF + FS.SIZE]
        else:
            blk = data[:FS.SIZE]
        s, st = FS.decode(blk)
        if st != "ok":
            QMessageBox.warning(self, "Réglages F4JCF", "Aucun réglage F4JCF valable dans ce fichier.")
            return
        self.set_values(s)
        self.state.setText(f"Réglages ouverts : {os.path.basename(path)} (pas encore écrits dans la radio).")

    def save_file(self):
        try:
            blk = self.values().encode()
        except ValueError as e:
            QMessageBox.warning(self, "Réglages F4JCF", str(e))
            return
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer les réglages F4JCF",
                                              os.path.join(self.main.last_dir(), "reglages.f4set"),
                                              "Réglages F4JCF (*.f4set)")
        if path:
            with open(path, "wb") as f:
                f.write(blk)
