"""Fenêtre « Calibration » : lecture seule par défaut, modification après validation de l'opérateur."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from . import calibration as C
from . import radio_ops
from .protocol import RadioError

WARNING = (
    "<b>La calibration est propre à CE poste</b> (réglée en usine). De mauvaises valeurs peuvent "
    "donner une puissance d'émission excessive ou insuffisante, des émissions hors bande ou un "
    "squelch qui ne s'ouvre plus. Ne la modifiez qu'avec un wattmètre et une charge fictive, par "
    "petits pas, en vérifiant chaque point.<br><br>"
    "Garde-fous : seules les bandes VHF 136-174 et UHF 400-480 MHz sont modifiables, seules les "
    "6 tables comprises (ampli et squelch), écart limité par rapport à l'origine (±8 pour l'ampli, "
    "±12 pour le squelch). La calibration et la copie de secours de la radio sont enregistrées sur "
    "l'ordinateur avant chaque écriture, la calibration écrite est relue et comparée, et chaque "
    "opération est notée dans le journal des calibrations.<br><br>"
    "Attention : à la fin de la session, le firmware recopie la nouvelle calibration dans sa copie "
    "de secours interne. La seule sauvegarde de l'origine est alors celle de l'ordinateur : "
    "gardez-la précieusement.")


class UnlockDialog(QDialog):
    """Validation de l'opérateur : indicatif, phrase recopiée, case à cocher."""

    def __init__(self, parent, operator: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Autoriser la modification de la calibration")
        v = QVBoxLayout(self)
        t = QLabel(WARNING)
        t.setWordWrap(True)
        t.setTextFormat(Qt.RichText)
        v.addWidget(t)
        f = QFormLayout()
        self.op = QLineEdit(operator)
        self.op.setPlaceholderText("ex. F4JCF")
        f.addRow("Opérateur (indicatif) :", self.op)
        self.hint = QLabel()
        f.addRow("Recopiez exactement :", self.hint)
        self.phrase = QLineEdit()
        f.addRow("", self.phrase)
        v.addLayout(f)
        self.ack = QCheckBox("J'ai lu l'avertissement et j'effectue moi-même ces réglages, "
                             "avec les appareils de mesure nécessaires.")
        v.addWidget(self.ack)
        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.bb.button(QDialogButtonBox.Ok).setText("Autoriser")
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        v.addWidget(self.bb)
        for w in (self.op, self.phrase):
            w.textChanged.connect(self.update_ok)
        self.ack.toggled.connect(self.update_ok)
        self.resize(620, 0)
        self.update_ok()

    def update_ok(self):
        op = self.op.text()
        bad = C.check_operator(op)
        self.hint.setText("—" if bad else f"<b>{C.confirm_phrase(op)}</b>")
        ok = (not bad and self.phrase.text().strip() == C.confirm_phrase(op) and self.ack.isChecked())
        self.bb.button(QDialogButtonBox.Ok).setEnabled(ok)

    def operator(self) -> str:
        return self.op.text().strip().upper()


class CalibrationDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Calibration du poste")
        self.cal: C.Calibration | None = None
        self.read_from_radio: bytes | None = None     # calibration lue dans la radio (référence)
        self.operator: str | None = None              # opérateur ayant validé (None = verrouillé)
        self._filling = False

        v = QVBoxLayout(self)
        h = QHBoxLayout()
        b = QPushButton("Lire la calibration de la radio")
        b.clicked.connect(self.read)
        h.addWidget(b)
        b = QPushButton("Ouvrir un fichier (consultation)…")
        b.clicked.connect(self.open_file)
        h.addWidget(b)
        self.b_save = QPushButton("Enregistrer sur l'ordinateur…")
        self.b_save.clicked.connect(self.save_file)
        h.addWidget(self.b_save)
        h.addStretch(1)
        v.addLayout(h)

        self.info = QLabel("Lisez la calibration de la radio (lecture seule, rien n'est écrit).")
        self.info.setWordWrap(True)
        v.addWidget(self.info)

        h = QHBoxLayout()
        h.addWidget(QLabel("Bande :"))
        self.band = QComboBox()
        for n, label, _, _ in C.BANDS:
            self.band.addItem(label + ("" if n in C.EDITABLE_BANDS else "  (consultation)"), n)
        self.band.currentIndexChanged.connect(self.fill)
        h.addWidget(self.band)
        h.addStretch(1)
        self.lock = QLabel()
        h.addWidget(self.lock)
        v.addLayout(h)

        self.table = QTableWidget()
        self.table.setFont(QFont("Monospace", 9))
        self.table.itemChanged.connect(self.on_edit)
        v.addWidget(self.table, 1)

        h = QHBoxLayout()
        self.b_unlock = QPushButton("Autoriser la modification…")
        self.b_unlock.clicked.connect(self.unlock)
        h.addWidget(self.b_unlock)
        self.b_undo = QPushButton("Annuler les modifications")
        self.b_undo.clicked.connect(self.undo)
        h.addWidget(self.b_undo)
        h.addStretch(1)
        self.b_restore = QPushButton("Restaurer une calibration enregistrée…")
        self.b_restore.clicked.connect(self.restore)
        h.addWidget(self.b_restore)
        self.b_write = QPushButton("Écrire dans la radio…")
        self.b_write.clicked.connect(self.write)
        h.addWidget(self.b_write)
        b = QPushButton("Fermer")
        b.clicked.connect(self.close)
        h.addWidget(b)
        v.addLayout(h)
        self.resize(1100, 520)
        self.update_state()

    def log(self, msg: str):
        self.main.log(msg)

    # ── état ──
    def editable(self) -> bool:
        return (self.operator is not None and self.cal is not None
                and self.read_from_radio is not None and self.band.currentData() in C.EDITABLE_BANDS)

    def update_state(self):
        have = self.cal is not None
        radio = self.read_from_radio is not None
        self.b_save.setEnabled(have)
        self.b_unlock.setEnabled(radio and self.operator is None)
        self.b_undo.setEnabled(have and bool(self.cal.changes()))
        self.b_write.setEnabled(radio and self.operator is not None and bool(self.cal.changes()))
        self.b_restore.setEnabled(radio and self.operator is not None)
        self.lock.setText(f"🔓 Modification autorisée : {self.operator}" if self.operator
                          else "🔒 Lecture seule")

    def set_cal(self, data: bytes, source: str, from_radio: bool, copy: bytes | None = None):
        try:
            self.cal = C.Calibration(data)
        except C.CalibrationError as e:
            QMessageBox.critical(self, "Calibration", str(e))
            return
        self.read_from_radio = bytes(data) if from_radio else None
        if not from_radio:
            self.operator = None
        txt = f"{source} — empreinte {C.sha(data)[:16]}"
        if copy is not None:
            txt += (" — copie de secours interne identique" if copy == data else
                    " — <b>copie de secours interne DIFFÉRENTE</b>"
                    + ("" if C.check(copy) is None else " (absente)"))
        self.info.setText(txt)
        self.fill()

    def fill(self):
        self._filling = True
        try:
            band = self.band.currentData()
            pts = C.points(band)
            self.table.clear()
            self.table.setRowCount(len(C.ROWS))
            self.table.setColumnCount(len(pts))
            self.table.setHorizontalHeaderLabels([f"{m}\nMHz" for _, m in pts])
            self.table.setVerticalHeaderLabels([r[2] for r in C.ROWS])
            if self.cal is None:
                return
            ed = self.editable()
            for r, (key, *_rest) in enumerate(C.ROWS):
                for c, (i, mhz) in enumerate(pts):
                    val, orig = self.cal.get(band, key, i), self.cal.get_original(band, key, i)
                    it = QTableWidgetItem(str(val))
                    it.setTextAlignment(Qt.AlignCenter)
                    it.setToolTip(f"{mhz}-{mhz + C.STEP_MHZ} MHz — origine : {orig}")
                    if not ed:
                        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                    if val != orig:
                        it.setBackground(QColor(255, 210, 120))
                    self.table.setItem(r, c, it)
            self.table.resizeColumnsToContents()
        finally:
            self._filling = False
        self.update_state()

    def on_edit(self, it: QTableWidgetItem):
        if self._filling or self.cal is None:
            return
        band = self.band.currentData()
        key = C.ROWS[it.row()][0]
        i = C.points(band)[it.column()][0]
        try:
            self.cal.set(band, key, i, int(it.text().strip()))
        except (ValueError, C.CalibrationError) as e:
            QMessageBox.warning(self, "Calibration", str(e) if isinstance(e, C.CalibrationError)
                                else "Entrez un nombre entier.")
        self.fill()

    # ── actions ──
    def read(self):
        port = self.main.port_name()
        if not port:
            return
        from .gui import TaskDialog
        ok, res = TaskDialog.run(self, "Lecture de la calibration", radio_ops.read_calibration, port)
        if ok:
            cal, copy = res
            self.operator = None
            self.set_cal(cal, "Calibration lue dans la radio", True, copy)

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Calibration ou sauvegarde complète",
                                              self.main.backup_dir(),
                                              "Fichiers binaires (*.bin);;Tous les fichiers (*)")
        if path:
            try:
                self.set_cal(C.load_file(path), f"Fichier {path}", False)
            except (OSError, C.CalibrationError) as e:
                QMessageBox.critical(self, "Calibration", str(e))
            self.update_state()

    def save_file(self):
        if self.cal is None:
            return
        path = C.save_backup(self.main.backup_dir(), self.cal.original, "lue")
        QMessageBox.information(self, "Calibration", f"Calibration enregistrée :\n{path}")

    def unlock(self):
        dlg = UnlockDialog(self, self.main.settings.value("cal_operator", ""))
        if dlg.exec() == QDialog.Accepted:
            self.operator = dlg.operator()
            self.main.settings.setValue("cal_operator", self.operator)
            self.main.log(f"Calibration : modification autorisée par {self.operator}.")
            self.fill()

    def undo(self):
        if self.cal:
            self.cal.reset()
            self.fill()

    def _confirm_and_write(self, new: bytes, changes, what: str):
        phrase = C.confirm_phrase(self.operator)
        lines = [f"{C.BANDS[b][1]} — {C.ROW_BY_KEY[k][2]} — {C.points(b)[i][1] if i < len(C.points(b)) else '-'}"
                 f" MHz : {a} → {n}" for b, k, i, a, n in changes[:30]]
        if len(changes) > 30:
            lines.append(f"… et {len(changes) - 30} autres")
        from PySide6.QtWidgets import QInputDialog
        txt, ok = QInputDialog.getText(
            self, "Confirmer l'écriture de la calibration",
            f"{what}\n\n" + ("\n".join(lines) if lines else "") +
            f"\n\nOpérateur : {self.operator}\nPour écrire dans la radio, recopiez : {phrase}")
        if not ok:
            return
        if txt.strip() != phrase:
            QMessageBox.warning(self, "Calibration", "Phrase incorrecte : rien n'a été écrit.")
            return
        port = self.main.port_name()
        if not port:
            return
        from .gui import TaskDialog
        ok, bk = TaskDialog.run(self, "Écriture de la calibration", radio_ops.write_calibration,
                                port, new, self.read_from_radio, self.operator,
                                self.main.backup_dir(), changes=changes, cancellable=False)
        if ok:
            self.main.log(f"Calibration écrite par {self.operator} ({len(changes)} valeurs). "
                          f"Sauvegarde de l'état précédent : {bk}")
            QMessageBox.information(
                self, "Calibration", "Calibration écrite et vérifiée.\n\nÉteignez puis rallumez la "
                "radio, puis contrôlez la puissance et le squelch sur chaque point modifié.\n\n"
                f"Calibration précédente enregistrée :\n{bk}")
            self.read_from_radio = new
            self.cal = C.Calibration(new)
            self.fill()
            self.main.backup.after_backup()

    def write(self):
        if not self.editable() and self.operator is None:
            return
        try:
            new = self.cal.build()
        except C.CalibrationError as e:
            QMessageBox.critical(self, "Calibration", str(e))
            return
        self._confirm_and_write(new, self.cal.changes(), "Valeurs modifiées :")

    def restore(self):
        path, _ = QFileDialog.getOpenFileName(self, "Calibration enregistrée", self.main.backup_dir(),
                                              "Fichiers binaires (*.bin);;Tous les fichiers (*)")
        if not path:
            return
        try:
            data = C.load_file(path)
        except (OSError, C.CalibrationError) as e:
            QMessageBox.critical(self, "Calibration", str(e))
            return
        cur = C.Calibration(self.read_from_radio)
        new = C.Calibration(data)
        changes = [(b, k, i, cur.get(b, k, i), new.get(b, k, i)) for b in range(len(C.BANDS))
                   for k, *_ in C.ROWS for i in range(C.NPOINTS) if cur.get(b, k, i) != new.get(b, k, i)]
        other = sum(x != y for x, y in zip(self.read_from_radio, data))
        if data == self.read_from_radio:
            QMessageBox.information(self, "Calibration", "La radio contient déjà cette calibration.")
            return
        self._confirm_and_write(data, changes,
                                f"Restauration de {path}\n({other} octets différents de la radio)")
