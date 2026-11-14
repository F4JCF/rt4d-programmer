"""Fenêtre « Stations surveillées » (Radio → Stations surveillées), idée n°34."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from . import addressbook as AB
from . import radio_ops
from . import watch as W

INFO = (
    "Quand une de ces stations commence à parler en DMR, la radio fait un <b>double bip</b>, comme pour "
    "un contact privé, sans qu'il faille la créer dans les contacts. Firmware <b>F4JCF22</b> ou plus "
    "récent. La radio doit recevoir l'appel : même fréquence et même slot, TG dans le groupe RX du canal "
    "(ou mode promiscuité). Pas de bip en mode discret. %d stations au plus." % W.MAX)


class WatchDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.sector: bytes | None = None
        self._book = None
        self.setWindowTitle("Stations surveillées (double bip)")
        self.resize(640, 560)
        v = QVBoxLayout(self)
        lab = QLabel(INFO)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.RichText)
        v.addWidget(lab)

        row = QHBoxLayout()
        self.ed = QLineEdit()
        self.ed.setPlaceholderText("Indicatif (cherché dans la liste RadioID) ou numéro ID DMR")
        self.ed.returnPressed.connect(self.add)
        row.addWidget(self.ed, 1)
        b = QPushButton("Ajouter")
        b.clicked.connect(self.add)
        row.addWidget(b)
        b = QPushButton("Contacts privés du codeplug")
        b.setToolTip("Ajoute les contacts de type « Privé » du codeplug ouvert")
        b.clicked.connect(self.add_contacts)
        row.addWidget(b)
        v.addLayout(row)

        self.t = QTableWidget(0, 4)
        self.t.setHorizontalHeaderLabels(["ID DMR", "Indicatif", "Prénom", "Ville / pays"])
        self.t.setEditTriggers(QTableWidget.NoEditTriggers)
        self.t.setSelectionBehavior(QTableWidget.SelectRows)
        self.t.verticalHeader().setVisible(False)
        self.t.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        v.addWidget(self.t, 1)
        self.state = QLabel("")
        self.state.setWordWrap(True)
        v.addWidget(self.state)

        row = QHBoxLayout()
        b = QPushButton("Retirer")
        b.clicked.connect(self.remove)
        row.addWidget(b)
        b = QPushButton("Lire la radio")
        b.clicked.connect(self.read_radio)
        row.addWidget(b)
        self.b_write = QPushButton("Écrire dans la radio")
        self.b_write.clicked.connect(self.write_radio)
        row.addWidget(self.b_write)
        row.addStretch(1)
        b = QPushButton("Fermer")
        b.clicked.connect(self.close)
        row.addWidget(b)
        v.addLayout(row)
        self.ids: list[int] = []
        self.refresh()

    # --- liste RadioID (gardée sur le PC par l'onglet Carnet adresse DMR)
    def book(self):
        if self._book is None:
            entries = getattr(getattr(self.main, "t_ab", None), "entries", None) or []
            if not entries:
                c = AB.load_cache()
                entries = c[0] if c else []
            self._book = {e.dmr_id: e for e in entries}
            self._calls = {}
            for e in entries:
                self._calls.setdefault(e.callsign.upper(), []).append(e.dmr_id)
        return self._book

    def refresh(self):
        bk = self.book()
        self.t.setRowCount(len(self.ids))
        for r, i in enumerate(self.ids):
            e = bk.get(i)
            vals = [str(i), e.callsign if e else "", e.name.split(" ")[0] if e else "",
                    ", ".join(x for x in ((e.city, e.country) if e else ()) if x)]
            for c, x in enumerate(vals):
                self.t.setItem(r, c, QTableWidgetItem(x))
        self.state.setText("%d station(s) sur %d.%s" % (
            len(self.ids), W.MAX, "" if bk else " Liste RadioID absente : chargez-la dans l'onglet "
            "« Carnet adresse DMR » pour chercher par indicatif et voir les noms."))

    def _add_ids(self, ids):
        new = [i for i in ids if i not in self.ids]
        if len(self.ids) + len(new) > W.MAX:
            QMessageBox.warning(self, "Stations surveillées", "%d stations au plus." % W.MAX)
            new = new[:W.MAX - len(self.ids)]
        self.ids += new
        self.refresh()
        return len(new)

    def add(self):
        txt = self.ed.text().strip().upper()
        if not txt:
            return
        if txt.isdigit():
            try:
                ids = W.clean([int(txt)])
            except W.WatchError as e:
                QMessageBox.warning(self, "Stations surveillées", str(e))
                return
        else:
            self.book()
            ids = self._calls.get(txt, [])
            if not ids:
                QMessageBox.information(self, "Stations surveillées",
                                        "Indicatif « %s » absent de la liste RadioID chargée : "
                                        "entrez son numéro ID DMR." % txt)
                return
        self._add_ids(ids)
        self.ed.clear()

    def add_contacts(self):
        cp = getattr(self.main, "cp", None)
        ids = [c.dmr_id for c in (cp.contacts if cp else []) if c and c.ctype == 0 and 0 < c.dmr_id < 0xFFFFFF]
        if not ids:
            QMessageBox.information(self, "Stations surveillées", "Aucun contact privé dans le codeplug ouvert.")
            return
        self.state.setText("%d contact(s) privé(s) ajouté(s)." % self._add_ids(ids))

    def remove(self):
        rows = sorted({i.row() for i in self.t.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.ids[r]
        self.refresh()

    # --- radio
    def read_radio(self):
        port = self.main.port_name()
        if not port:
            return
        from .gui import TaskDialog
        ok, sector = TaskDialog.run(self, "Lecture des stations surveillées", radio_ops.read_f4set, port)
        if ok:
            self.sector = sector
            self.ids = W.decode(sector)
            self.refresh()

    def write_radio(self):
        if self.sector is None:
            QMessageBox.information(self, "Stations surveillées",
                                    "Lisez d'abord la radio : la liste est écrite dans le même secteur que le "
                                    "logo, les réglages des modes et le journal, qui sont conservés.")
            return
        port = self.main.port_name()
        if not port:
            return
        if QMessageBox.question(self, "Stations surveillées", "Écrire ces %d station(s) dans la radio ?"
                                % len(self.ids)) != QMessageBox.Yes:
            return
        from .gui import TaskDialog
        ids = list(self.ids)
        ok, _ = TaskDialog.run(self, "Écriture des stations surveillées", radio_ops.write_logo_sector, port,
                               lambda cur: W.build_sector(cur, ids), self.sector, self.main.backup_dir(),
                               "Stations surveillées")
        if ok:
            self.sector = W.build_sector(self.sector, ids)
            self.state.setText("Écrit : %d station(s). Pris en compte dès le prochain appel." % len(ids))
