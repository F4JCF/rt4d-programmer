"""Onglets d'édition : canaux, contacts, groupes RX, zones, réglages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QStyledItemDelegate, QCheckBox, QComboBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import layout as L
from . import tones
from .codeplug import (ANALOG_MODS, CONTACT_TYPES, RX_TX, Channel, Codeplug, Contact,
                       GroupList, Zone, fmt_mhz, mhz_to_raw)

TOT_LABELS = ["Désactivé"] + [f"{(i * 5) if i <= 3 else (i - 2) * 15} s" for i in range(1, 64)]


def _ro(text) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text))
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    return it


class _Block:
    """Bloque temporairement les signaux d'une liste de widgets."""

    def __init__(self, widgets):
        self.w = widgets

    def __enter__(self):
        for w in self.w:
            w.blockSignals(True)

    def __exit__(self, *a):
        for w in self.w:
            w.blockSignals(False)


class BaseTab(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.cp: Codeplug | None = None

    @property
    def ro(self) -> bool:
        """Codeplug en lecture seule (ancien firmware Radtel)."""
        return bool(getattr(self.cp, "read_only", False))

    def set_codeplug(self, cp: Codeplug):
        self.cp = cp
        for b in self.findChildren(QPushButton):
            b.setEnabled(not self.ro)
        self.refresh()
        for t in self.findChildren(QTableWidget):
            if self.ro:
                if not hasattr(t, "_rw_triggers"):
                    t._rw_triggers = t.editTriggers()
                t.setEditTriggers(QAbstractItemView.NoEditTriggers)
            elif hasattr(t, "_rw_triggers"):
                t.setEditTriggers(t._rw_triggers)

    def refresh(self):
        raise NotImplementedError


# ───────────────────────────── Canaux ─────────────────────────────

class ChannelsTab(BaseTab):
    COLS = ["N°", "Nom", "Mode", "RX (MHz)", "TX (MHz)", "Puiss.", "Modul.", "Largeur",
            "Ton RX", "Ton TX", "CC", "Slot", "Contact", "Groupe RX", "Scan"]

    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.filter = QLineEdit(placeholderText="Rechercher un canal…")
        self.filter.textChanged.connect(self.refresh)
        bar.addWidget(self.filter, 1)
        for text, fn in (("Ajouter", self.add), ("Dupliquer", self.duplicate),
                         ("Supprimer", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.count = QLabel()
        bar.addWidget(self.count)
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.itemSelectionChanged.connect(self.on_select)
        split.addWidget(self.table)

        form_box = QGroupBox("Canal sélectionné")
        f = QFormLayout(form_box)
        self.e_name = QLineEdit(maxLength=16)
        self.e_mode = QComboBox()
        self.e_mode.addItems(["Analogique", "Numérique (DMR)"])
        self.e_rx = QDoubleSpinBox(decimals=5, minimum=18.0, maximum=1000.0, singleStep=0.0125)
        self.e_tx = QDoubleSpinBox(decimals=5, minimum=0.0, maximum=1000.0, singleStep=0.0125)
        self.e_tx.setSpecialValueText("RX seul (pas d'émission)")
        txrow = QHBoxLayout()
        txrow.addWidget(self.e_tx, 1)
        self.e_shift = QComboBox()
        self.e_shift.addItems(["Décalage…", "Simplex", "−0,600", "+0,600", "−1,600",
                               "−7,600", "+7,600", "−9,400", "+9,400"])
        txrow.addWidget(self.e_shift)
        self.e_power = QComboBox()
        self.e_power.addItems(["Haute", "Basse"])
        self.e_scan = QCheckBox("Inclus dans le balayage")
        self.e_rxtx = QComboBox()
        self.e_rxtx.addItems(RX_TX)
        self.e_tot = QComboBox()
        self.e_tot.addItems(TOT_LABELS)
        # analogique
        self.e_mod = QComboBox()
        self.e_mod.addItems(ANALOG_MODS)
        self.e_bw = QComboBox()
        self.e_bw.addItems(["Large (25 kHz)", "Étroite (12,5 kHz)"])
        self.e_rxtone = QComboBox(editable=True)
        self.e_txtone = QComboBox(editable=True)
        for c in (self.e_rxtone, self.e_txtone):
            c.addItems(tones.tone_choices())
            c.setItemText(0, "Aucun")
        # numérique
        self.e_cc = QSpinBox(minimum=0, maximum=15)
        self.e_ts = QComboBox()
        self.e_ts.addItems(["Slot 1", "Slot 2"])
        self.e_contact = QComboBox()
        self.e_group = QComboBox()
        self.e_idsel = QComboBox()
        self.e_idsel.addItems(["ID de la radio", "ID propre au canal"])
        self.e_dmrid = QSpinBox(minimum=0, maximum=16_777_215)
        self.e_monitor = QCheckBox("Mode promiscuité (écoute tous les groupes)")

        f.addRow("Nom", self.e_name)
        f.addRow("Mode", self.e_mode)
        f.addRow("Fréquence RX (MHz)", self.e_rx)
        f.addRow("Fréquence TX (MHz)", txrow)
        f.addRow("Puissance", self.e_power)
        f.addRow("RX / TX", self.e_rxtx)
        f.addRow("Limite d'émission", self.e_tot)
        f.addRow("", self.e_scan)
        self.ana_rows = [("Modulation", self.e_mod), ("Largeur de bande", self.e_bw),
                         ("Sous-ton RX", self.e_rxtone), ("Sous-ton TX", self.e_txtone)]
        self.dig_rows = [("Code couleur", self.e_cc), ("Slot", self.e_ts),
                         ("Contact émission", self.e_contact), ("Groupe RX", self.e_group),
                         ("Identifiant", self.e_idsel), ("ID du canal", self.e_dmrid),
                         ("", self.e_monitor)]
        for lab, w in self.ana_rows + self.dig_rows:
            f.addRow(lab, w)
        self.form = f
        self.form_box = form_box
        split.addWidget(form_box)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        self.e_name.editingFinished.connect(self.save_form)
        for w in (self.e_mode, self.e_power, self.e_rxtx, self.e_tot, self.e_mod, self.e_bw,
                  self.e_ts, self.e_contact, self.e_group, self.e_idsel):
            w.currentIndexChanged.connect(self.save_form)
        for w in (self.e_rx, self.e_tx, self.e_cc, self.e_dmrid):
            w.editingFinished.connect(self.save_form)
        for w in (self.e_rxtone, self.e_txtone):
            w.lineEdit().editingFinished.connect(self.save_form)
            w.activated.connect(self.save_form)
        self.e_scan.toggled.connect(self.save_form)
        self.e_monitor.toggled.connect(self.save_form)
        self.e_shift.activated.connect(self.apply_shift)
        self.form_widgets = [self.e_name, self.e_mode, self.e_rx, self.e_tx, self.e_power,
                             self.e_scan, self.e_rxtx, self.e_tot, self.e_mod, self.e_bw,
                             self.e_rxtone, self.e_txtone, self.e_cc, self.e_ts, self.e_contact,
                             self.e_group, self.e_idsel, self.e_dmrid, self.e_monitor]
        self.current: int | None = None
        self.form_box.setEnabled(False)

    # -- tableau --
    def refresh(self):
        if not self.cp:
            return
        cp = self.cp
        q = self.filter.text().strip().lower()
        slots = [s for s in sorted(cp.channels) if not q or q in cp.channels[s].name.lower()]
        self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(slots))
        for r, s in enumerate(slots):
            self._fill_row(r, s)
        self.table.resizeColumnsToContents()
        self.table.setUpdatesEnabled(True)
        self.table.blockSignals(False)
        self.count.setText(f"{len(cp.channels)} / {cp.LIMITS['channels']}")
        self._reload_refs()
        if self.current is not None and self.current in slots:
            self.table.blockSignals(True)
            self.table.clearSelection()
            self.table.selectRow(slots.index(self.current))
            self.table.blockSignals(False)
            self.table.scrollToItem(self.table.item(slots.index(self.current), 0))
            self.load_form(cp.channels[self.current])
        else:
            self.current = None
            self.form_box.setEnabled(False)

    def _fill_row(self, r, s):
        cp = self.cp
        ch = cp.channels[s]
        ct = cp.contacts.get(ch.contact) if ch.contact is not None else None
        gl = cp.groups.get(ch.group) if ch.group is not None else None
        vals = [s + 1, ch.name, "Analog." if ch.analog else "DMR", fmt_mhz(ch.rx), fmt_mhz(ch.tx),
                "H" if ch.power_high else "B",
                ANALOG_MODS[ch.modulation] if ch.analog else "",
                ("12,5" if ch.narrow else "25") if ch.analog else "",
                ch.rx_tone if ch.analog else "", ch.tx_tone if ch.analog else "",
                "" if ch.analog else ch.color_code, "" if ch.analog else ch.timeslot + 1,
                (ct.name if ct else "") if not ch.analog else "",
                (gl.name if gl else "") if not ch.analog else "",
                "Oui" if ch.scan else "Non"]
        for c, v in enumerate(vals):
            it = _ro(v)
            if c == 0:
                it.setData(Qt.UserRole, s)
            self.table.setItem(r, c, it)

    def _reload_refs(self):
        cp = self.cp
        with _Block([self.e_contact, self.e_group]):
            self.e_contact.clear()
            if not cp.LIMITS["contact0_fixed"]:
                self.e_contact.addItem("(aucun)", None)
            for s, c in sorted(cp.contacts.items()):
                self.e_contact.addItem(f"{c.name} ({c.dmr_id})", s)
            self.e_group.clear()
            self.e_group.addItem("(aucun)", None)
            for s, g in sorted(cp.groups.items()):
                self.e_group.addItem(g.name, s)

    def selected_slots(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows]

    def on_select(self):
        sel = self.selected_slots()
        if len(sel) != 1:
            self.current = None
            self.form_box.setEnabled(False)
            return
        self.current = sel[0]
        self.load_form(self.cp.channels[self.current])

    # -- formulaire --
    def load_form(self, ch: Channel):
        with _Block(self.form_widgets):
            self.e_name.setText(ch.name)
            self.e_mode.setCurrentIndex(0 if ch.analog else 1)
            self.e_rx.setValue(ch.rx / 100000)
            self.e_tx.setValue(ch.tx / 100000)
            self.e_power.setCurrentIndex(0 if ch.power_high else 1)
            self.e_scan.setChecked(ch.scan)
            self.e_rxtx.setCurrentIndex(min(ch.rx_tx, 2))
            self.e_tot.setCurrentIndex(min(ch.tot, 63))
            self.e_mod.setCurrentIndex(ch.modulation)
            self.e_bw.setCurrentIndex(1 if ch.narrow else 0)
            self.e_rxtone.setCurrentText(ch.rx_tone or "Aucun")
            self.e_txtone.setCurrentText(ch.tx_tone or "Aucun")
            self.e_cc.setValue(ch.color_code)
            self.e_ts.setCurrentIndex(ch.timeslot)
            i = self.e_contact.findData(ch.contact)
            self.e_contact.setCurrentIndex(max(i, 0))
            i = self.e_group.findData(ch.group)
            self.e_group.setCurrentIndex(max(i, 0))
            self.e_idsel.setCurrentIndex(0 if ch.use_radio_id else 1)
            self.e_dmrid.setValue(ch.dmr_id)
            self.e_monitor.setChecked(bool(ch.monitor))
        self._show_mode_rows(ch.analog)
        self.form_box.setEnabled(not self.ro)

    def _show_mode_rows(self, analog: bool):
        for _, w in self.ana_rows:
            self.form.setRowVisible(w, analog)
        for _, w in self.dig_rows:
            self.form.setRowVisible(w, not analog)

    def save_form(self, *_):
        if self.current is None or self.current not in self.cp.channels:
            return
        ch = self.cp.channels[self.current]
        ch.name = self.e_name.text()
        ch.analog = self.e_mode.currentIndex() == 0
        ch.rx = mhz_to_raw(self.e_rx.value())
        ch.tx = mhz_to_raw(self.e_tx.value())
        ch.power_high = self.e_power.currentIndex() == 0
        ch.scan = self.e_scan.isChecked()
        ch.rx_tx = self.e_rxtx.currentIndex()
        ch.tot = self.e_tot.currentIndex()
        ch.modulation = self.e_mod.currentIndex()
        ch.narrow = self.e_bw.currentIndex() == 1
        for combo, attr in ((self.e_rxtone, "rx_tone"), (self.e_txtone, "tx_tone")):
            t = combo.currentText()
            try:
                setattr(ch, attr, tones.normalize("" if t == "Aucun" else t))
            except ValueError as e:
                QMessageBox.warning(self, "Sous-ton invalide", str(e))
                with _Block([combo]):
                    combo.setCurrentText(getattr(ch, attr) or "Aucun")
        ch.color_code = self.e_cc.value()
        ch.timeslot = self.e_ts.currentIndex()
        ch.contact = self.e_contact.currentData()
        ch.group = self.e_group.currentData()
        ch.use_radio_id = self.e_idsel.currentIndex() == 0
        ch.dmr_id = self.e_dmrid.value()
        ch.monitor = 1 if self.e_monitor.isChecked() else 0
        self._show_mode_rows(ch.analog)
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(Qt.UserRole) == self.current:
                self.table.blockSignals(True)
                self._fill_row(r, self.current)
                self.table.blockSignals(False)
                break
        self.changed.emit()

    def apply_shift(self, idx):
        if idx <= 0 or self.current is None:
            return
        shifts = {1: 0.0, 2: -0.6, 3: 0.6, 4: -1.6, 5: -7.6, 6: 7.6, 7: -9.4, 8: 9.4}
        self.e_tx.setValue(self.e_rx.value() + shifts[idx])
        self.e_shift.setCurrentIndex(0)
        self.save_form()

    # -- actions --
    def add(self):
        slot = self.cp.free_slot(self.cp.channels, self.cp.LIMITS["channels"])
        if slot is None:
            QMessageBox.warning(self, "Mémoire pleine", "Tous les canaux sont utilisés.")
            return
        self.cp.channels[slot] = Channel(name=f"CANAL {slot + 1}", analog=True,
                                         rx=14550000, tx=14550000, narrow=False)
        self.current = slot
        self.filter.clear()
        self.refresh()
        self.changed.emit()
        self.e_name.setFocus()
        self.e_name.selectAll()

    def duplicate(self):
        import copy
        for s in self.selected_slots():
            slot = self.cp.free_slot(self.cp.channels, self.cp.LIMITS["channels"])
            if slot is None:
                break
            ch = copy.deepcopy(self.cp.channels[s])
            self.cp.channels[slot] = ch
            self.current = slot
        self.refresh()
        self.changed.emit()

    def delete(self):
        sel = self.selected_slots()
        if not sel:
            return
        if QMessageBox.question(self, "Supprimer", f"Supprimer {len(sel)} canal(aux) ?\n"
                                "Ils seront aussi retirés des zones.") != QMessageBox.Yes:
            return
        for s in sel:
            self.cp.delete_channel(s)
        self.current = None
        self.refresh()
        self.changed.emit()


# ───────────────────────────── Contacts ─────────────────────────────

class _ComboDelegate(QStyledItemDelegate):
    def __init__(self, choices, parent=None):
        super().__init__(parent)
        self.choices = choices

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.addItems(self.choices)
        cb.activated.connect(lambda *_: self.commitData.emit(cb))
        return cb

    def setEditorData(self, editor, index):
        t = index.data()
        editor.setCurrentIndex(max(self.choices.index(t) if t in self.choices else 0, 0))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText())


class ContactsTab(BaseTab):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.filter = QLineEdit(placeholderText="Rechercher (nom ou ID)…")
        self.filter.textChanged.connect(self.refresh)
        bar.addWidget(self.filter, 1)
        for text, fn in (("Ajouter", self.add), ("Supprimer", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.count = QLabel()
        bar.addWidget(self.count)
        root.addLayout(bar)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Nom", "Type", "ID DMR"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setItemDelegateForColumn(1, _ComboDelegate(CONTACT_TYPES, self.table))
        self.table.itemChanged.connect(self.on_edit)
        root.addWidget(self.table, 1)
        root.addWidget(QLabel("Double-cliquez sur une case pour la modifier. "
                              "Type : Privé, Groupe ou Tous."))

    def refresh(self):
        if not self.cp:
            return
        q = self.filter.text().strip().lower()
        items = [(s, c) for s, c in sorted(self.cp.contacts.items())
                 if not q or q in c.name.lower() or q in str(c.dmr_id)]
        self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(items))
        for r, (s, c) in enumerate(items):
            it = QTableWidgetItem(c.name)
            it.setData(Qt.UserRole, s)
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, QTableWidgetItem(CONTACT_TYPES[c.ctype]))
            self.table.setItem(r, 2, QTableWidgetItem(str(c.dmr_id)))
        self.table.setUpdatesEnabled(True)
        self.table.blockSignals(False)
        self.count.setText(f"{len(self.cp.contacts)} / {self.cp.LIMITS['contacts']}")

    def on_edit(self, item):
        slot = self.table.item(item.row(), 0).data(Qt.UserRole)
        c = self.cp.contacts.get(slot)
        if c is None:
            return
        if slot == 0 and self.cp.LIMITS["contact0_fixed"] and item.column() != 0:
            QMessageBox.information(self, "All Call", "Le contact « All Call » est fixe : "
                                    "seul son nom peut être modifié.")
            self.refresh()
            return
        if item.column() == 0:
            c.name = item.text()[:16]
        elif item.column() == 1:
            if item.text() in CONTACT_TYPES:
                c.ctype = CONTACT_TYPES.index(item.text())
        elif item.column() == 2:
            try:
                v = int(item.text().strip())
                if not 1 <= v <= 16_777_215:
                    raise ValueError
                c.dmr_id = v
            except ValueError:
                QMessageBox.warning(self, "ID invalide", "L'ID DMR doit être un nombre entre 1 et 16777215.")
                self.table.blockSignals(True)
                item.setText(str(c.dmr_id))
                self.table.blockSignals(False)
                return
        self.changed.emit()

    def add(self):
        slot = self.cp.free_slot(self.cp.contacts, self.cp.LIMITS["contacts"])
        if slot is None:
            QMessageBox.warning(self, "Mémoire pleine", "Tous les contacts sont utilisés.")
            return
        self.cp.contacts[slot] = Contact(name="Nouveau TG", ctype=1, dmr_id=91)
        self.filter.clear()
        self.refresh()
        self.table.scrollToBottom()
        self.changed.emit()

    def delete(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            return
        if QMessageBox.question(self, "Supprimer", f"Supprimer {len(rows)} contact(s) ?\n"
                                "Ils seront retirés des canaux et des groupes RX.") != QMessageBox.Yes:
            return
        for r in rows:
            slot = self.table.item(r, 0).data(Qt.UserRole)
            if slot == 0 and self.cp.LIMITS["contact0_fixed"]:
                QMessageBox.information(self, "All Call", "Le contact « All Call » ne peut pas être supprimé.")
                continue
            self.cp.delete_contact(slot)
        self.refresh()
        self.changed.emit()


# ───────────────────────── Listes avec membres (groupes, zones) ─────────────────────────

class _MembersTab(BaseTab):
    """Liste d'objets à gauche ; à droite nom + membres choisis parmi des disponibles."""
    TITLE = ""
    K_ITEMS = ""
    K_MEMBERS = ""
    K_SCAN = None

    @property
    def MAX_ITEMS(self):
        return self.cp.LIMITS[self.K_ITEMS] if self.cp else 0

    @property
    def MAX_MEMBERS(self):
        return self.cp.LIMITS[self.K_MEMBERS] if self.cp else 0

    @property
    def SCAN(self):
        return bool(self.cp and self.K_SCAN and self.cp.LIMITS[self.K_SCAN])

    def __init__(self):
        super().__init__()
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.on_select)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, fn in (("Ajouter", self.add), ("Supprimer", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        left.addLayout(row)
        self.count = QLabel()
        left.addWidget(self.count)
        root.addLayout(left, 1)

        self.right = QGroupBox(self.TITLE)
        rv = QVBoxLayout(self.right)
        nf = QFormLayout()
        self.e_name = QLineEdit()
        self.e_name.editingFinished.connect(self.save_name)
        nf.addRow("Nom", self.e_name)
        rv.addLayout(nf)
        lists = QHBoxLayout()
        av = QVBoxLayout()
        av.addWidget(QLabel("Disponibles"))
        self.avail_filter = QLineEdit(placeholderText="Filtrer…")
        self.avail_filter.textChanged.connect(self.fill_members)
        av.addWidget(self.avail_filter)
        self.avail = QListWidget()
        self.avail.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.avail.itemDoubleClicked.connect(lambda *_: self.add_members())
        av.addWidget(self.avail, 1)
        lists.addLayout(av, 1)
        mid = QVBoxLayout()
        mid.addStretch()
        for text, fn in (("Ajouter →", self.add_members), ("← Retirer", self.remove_members),
                         ("Monter ↑", lambda: self.move(-1)), ("Descendre ↓", lambda: self.move(1))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            mid.addWidget(b)
        mid.addStretch()
        lists.addLayout(mid)
        mv = QVBoxLayout()
        self.members_label = QLabel()
        mv.addWidget(self.members_label)
        self.members = QListWidget()
        self.members.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.members.itemDoubleClicked.connect(lambda *_: self.remove_members())
        self.members.itemChanged.connect(self.on_member_check)
        mv.addWidget(self.members, 1)
        lists.addLayout(mv, 1)
        rv.addLayout(lists, 1)
        root.addWidget(self.right, 3)
        self.current = None
        self.right.setEnabled(False)

    # à fournir par les sous-classes
    def items(self) -> dict: ...
    def new_item(self): ...
    def member_ids(self, obj) -> list: ...
    def set_member_ids(self, obj, ids): ...
    def candidates(self) -> list: ...      # [(id, libellé)]
    def remove_item(self, slot): ...

    def refresh(self):
        if not self.cp:
            return
        self.list.blockSignals(True)
        self.list.clear()
        keys = sorted(self.items())
        for s in keys:
            it = QListWidgetItem(self.items()[s].name)
            it.setData(Qt.UserRole, s)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.count.setText(f"{len(keys)} / {self.MAX_ITEMS}")
        if self.current in keys:
            row = keys.index(self.current)
            self.list.blockSignals(True)
            self.list.setCurrentRow(row)
            self.list.blockSignals(False)
            self.on_select(row)
        else:
            self.current = None
            self.right.setEnabled(False)
            self.e_name.clear()
            self.members.clear()
            self.avail.clear()

    def on_select(self, row):
        if row < 0:
            self.current = None
            self.right.setEnabled(False)
            return
        self.current = self.list.item(row).data(Qt.UserRole)
        obj = self.items()[self.current]
        self.e_name.setText(obj.name)
        self.right.setEnabled(not self.ro)
        self.fill_members()

    def fill_members(self):
        if self.current is None:
            return
        obj = self.items()[self.current]
        ids = self.member_ids(obj)
        labels = dict(self.candidates())
        self.members.blockSignals(True)
        self.members.clear()
        for k, i in enumerate(ids):
            it = QListWidgetItem(labels.get(i, f"#{i}"))
            it.setData(Qt.UserRole, i)
            if self.SCAN:
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if obj.scan[k] else Qt.Unchecked)
            self.members.addItem(it)
        self.members.blockSignals(False)
        extra = " — cochés = inclus dans le balayage" if self.SCAN else ""
        self.members_label.setText(f"Membres ({len(ids)} / {self.MAX_MEMBERS}){extra}")
        q = self.avail_filter.text().strip().lower()
        self.avail.clear()
        used = set(ids)
        for i, lab in self.candidates():
            if i in used or (q and q not in lab.lower()):
                continue
            it = QListWidgetItem(lab)
            it.setData(Qt.UserRole, i)
            self.avail.addItem(it)

    def save_name(self):
        if self.current is None:
            return
        obj = self.items()[self.current]
        name = self.e_name.text().strip()
        if not name:
            self.e_name.setText(obj.name)
            return
        if name != obj.name:
            obj.name = name
            self.list.currentItem().setText(name)
            self.changed.emit()

    def add_members(self):
        if self.current is None:
            return
        obj = self.items()[self.current]
        ids = list(self.member_ids(obj))
        new = [it.data(Qt.UserRole) for it in self.avail.selectedItems()]
        room = self.MAX_MEMBERS - len(ids)
        if len(new) > room:
            QMessageBox.warning(self, "Limite atteinte", f"Maximum {self.MAX_MEMBERS} membres.")
            new = new[:max(room, 0)]
        self.set_member_ids(obj, ids + new)
        self.fill_members()
        self.changed.emit()

    def remove_members(self):
        if self.current is None:
            return
        obj = self.items()[self.current]
        rows = {self.members.row(it) for it in self.members.selectedItems()}
        ids = [i for k, i in enumerate(self.member_ids(obj)) if k not in rows]
        self.set_member_ids(obj, ids, keep_rows=[k for k in range(len(self.member_ids(obj))) if k not in rows])
        self.fill_members()
        self.changed.emit()

    def move(self, d):
        if self.current is None:
            return
        row = self.members.currentRow()
        obj = self.items()[self.current]
        ids = list(self.member_ids(obj))
        j = row + d
        if row < 0 or not 0 <= j < len(ids):
            return
        order = list(range(len(ids)))
        order[row], order[j] = order[j], order[row]
        self.set_member_ids(obj, [ids[k] for k in order], keep_rows=order)
        self.fill_members()
        self.members.setCurrentRow(j)
        self.changed.emit()

    def on_member_check(self, item):
        if not self.SCAN or self.current is None:
            return
        obj = self.items()[self.current]
        obj.scan[self.members.row(item)] = item.checkState() == Qt.Checked
        self.changed.emit()

    def add(self):
        slot = self.cp.free_slot(self.items(), self.MAX_ITEMS)
        if slot is None:
            QMessageBox.warning(self, "Mémoire pleine", f"Maximum {self.MAX_ITEMS}.")
            return
        self.items()[slot] = self.new_item()
        self.current = slot
        self.refresh()
        self.changed.emit()
        self.e_name.setFocus()
        self.e_name.selectAll()

    def delete(self):
        if self.current is None:
            return
        name = self.items()[self.current].name
        if QMessageBox.question(self, "Supprimer", f"Supprimer « {name} » ?") != QMessageBox.Yes:
            return
        self.remove_item(self.current)
        self.current = None
        self.refresh()
        self.changed.emit()


class GroupsTab(_MembersTab):
    TITLE = "Groupe RX (talkgroups écoutés)"
    K_ITEMS = "groups"
    K_MEMBERS = "group_ids"

    def items(self):
        return self.cp.groups

    def new_item(self):
        return GroupList(name="Nouveau groupe")

    def member_ids(self, obj):
        return obj.contacts

    def set_member_ids(self, obj, ids, keep_rows=None):
        obj.contacts = ids

    def candidates(self):
        return [(s, f"{c.name} ({c.dmr_id})") for s, c in sorted(self.cp.contacts.items())]

    def remove_item(self, slot):
        self.cp.delete_group(slot)


class ZonesTab(_MembersTab):
    TITLE = "Zone"
    K_ITEMS = "zones"
    K_MEMBERS = "zone_channels"
    K_SCAN = "zone_scan"

    def items(self):
        return self.cp.zones

    def new_item(self):
        return Zone(name="Nouvelle zone")

    def member_ids(self, obj):
        return obj.channels

    def set_member_ids(self, obj, ids, keep_rows=None):
        old_scan = dict(zip(obj.channels, obj.scan))
        obj.channels = ids
        obj.scan = [old_scan.get(i, True) for i in ids]

    def candidates(self):
        return [(s, f"{s + 1:4d}  {c.name}  {fmt_mhz(c.rx)}") for s, c in sorted(self.cp.channels.items())]

    def remove_item(self, slot):
        self.cp.delete_zone(slot)


# ───────────────────────────── Réglages ─────────────────────────────

class SettingsTab(BaseTab):
    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        box = QGroupBox("Identité DMR de la radio")
        f = QFormLayout(box)
        self.e_id = QSpinBox(minimum=0, maximum=16_777_215)
        self.e_name = QLineEdit(maxLength=16)
        self.e_msg = QLineEdit(maxLength=32)
        f.addRow("ID DMR de la radio", self.e_id)
        f.addRow("Nom / indicatif", self.e_name)
        self.msg_label = QLabel("Message de démarrage")
        f.addRow(self.msg_label, self.e_msg)
        root.addWidget(box)
        self.info = QLabel()
        self.info.setWordWrap(True)
        root.addWidget(self.info)
        root.addStretch()
        self.e_id.editingFinished.connect(self.save)
        self.e_name.editingFinished.connect(self.save)
        self.e_msg.editingFinished.connect(self.save)

    def refresh(self):
        if not self.cp:
            return
        s = self.cp.settings
        with _Block([self.e_id, self.e_name, self.e_msg]):
            self.e_id.setValue(s.radio_id)
            self.e_name.setText(s.radio_name)
            self.e_msg.setText(s.startup_message)
        ok = self.cp.has_settings and (self.cp.is_refw or self.cp.kind in ("stock", "legacy")) and not self.ro
        # firmware officiel : le message d'accueil se règle dans « Menu démarrage »
        stock = self.cp.kind == "stock"
        self.msg_label.setVisible(not stock)
        self.e_msg.setVisible(not stock)
        self.setEnabled(True)
        for w in (self.e_id, self.e_name, self.e_msg):
            w.setEnabled(ok)
        self.info.setText(
            "L'ID DMR est celui attribué par RadioID.net (7 chiffres pour la France : 208xxxx). "
            "Les autres réglages de la radio sont conservés tels quels."
            if ok else
            "Lecture seule : ancien firmware Radtel." if self.ro else
            "Réglages indisponibles : ce codeplug ne contient pas de réglages REFW valides. "
            "Lisez la radio pour les obtenir.")

    def save(self):
        s = self.cp.settings
        new = (self.e_id.value(), self.e_name.text(), self.e_msg.text())
        if new != (s.radio_id, s.radio_name, s.startup_message):
            s.radio_id, s.radio_name, s.startup_message = new
            self.changed.emit()


# ───────────────────────────── Touches ─────────────────────────────

class KeysTab(BaseTab):
    """Fonctions des touches programmables (firmware officiel Radtel)."""

    def __init__(self):
        super().__init__()
        from . import stock as S
        self.S = S
        root = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        root.addWidget(self.info)
        self.box = QGroupBox("Fonction de chaque touche")
        f = QFormLayout(self.box)
        self.combos = []
        for _, label in S.KEY_SLOTS:
            cb = QComboBox()
            cb.addItems(S.KEY_FUNCTIONS)
            cb.setMaxVisibleItems(20)
            cb.currentIndexChanged.connect(self.save)
            f.addRow(label, cb)
            self.combos.append(cb)
        root.addWidget(self.box)
        row = QHBoxLayout()
        b = QPushButton("Tout remettre à « Aucune fonction »")
        b.clicked.connect(self.reset_all)
        row.addWidget(b)
        row.addStretch()
        root.addLayout(row)
        root.addStretch()

    def refresh(self):
        if not self.cp:
            return
        ok = self.cp.LIMITS.get("keys", False)
        self.box.setEnabled(ok)
        if not ok:
            self.info.setText("Le réglage des touches n'est disponible qu'avec le firmware officiel "
                              "Radtel (V3.18 ou plus récent).")
            return
        self.info.setText("Choisissez l'action de chaque touche, puis écrivez dans la radio. "
                          "« Appui long » sur les touches 0 à 9 = raccourcis du clavier.")
        with _Block(self.combos):
            for cb, val in zip(self.combos, self.cp.keys):
                if val >= len(self.S.KEY_FUNCTIONS):
                    if cb.count() == len(self.S.KEY_FUNCTIONS):
                        cb.addItem(f"Valeur inconnue ({val})")
                    cb.setCurrentIndex(cb.count() - 1)
                else:
                    cb.setCurrentIndex(val)

    def save(self, *_):
        if not self.cp or not self.cp.LIMITS.get("keys", False):
            return
        changed = False
        for i, cb in enumerate(self.combos):
            idx = cb.currentIndex()
            if idx < len(self.S.KEY_FUNCTIONS) and self.cp.keys[i] != idx:
                self.cp.keys[i] = idx
                changed = True
        if changed:
            self.changed.emit()

    def reset_all(self):
        for cb in self.combos:
            cb.setCurrentIndex(0)


# ───────────────────────────── Menu démarrage ─────────────────────────────

class StartupTab(BaseTab):
    """Allumage et affichage (firmware officiel Radtel)."""

    def __init__(self):
        super().__init__()
        from . import stock as S
        self.S = S
        outer = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        outer.addWidget(self.info)
        cols = QHBoxLayout()
        outer.addLayout(cols, 1)
        left, right = QVBoxLayout(), QVBoxLayout()
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        self.widgets = {}
        self.boxes = []
        forms = {}
        # message d'accueil (texte, 32 caractères)
        for _, _, _, _, _, _, group in S.STARTUP_FIELDS:
            if group not in forms:
                box = QGroupBox(group)
                forms[group] = QFormLayout(box)
                (left if group in ("Allumage", "Au démarrage") else right).addWidget(box)
                self.boxes.append(box)
        self.e_msg = QLineEdit(maxLength=32)
        self.e_msg.editingFinished.connect(self.save_msg)
        forms["Allumage"].addRow("Message d'accueil", self.e_msg)
        for key, _, _, _, choices, label, group in S.STARTUP_FIELDS:
            if isinstance(choices, tuple):
                w = QSpinBox(minimum=choices[0], maximum=choices[1])
                w.valueChanged.connect(self.save)
            else:
                w = QComboBox()
                w.setMaxVisibleItems(20)
                if choices != "zones":
                    w.addItems(choices)
                w.currentIndexChanged.connect(self.save)
            self.widgets[key] = w
            forms[group].addRow(label, w)
        left.addStretch()
        right.addStretch()

    def refresh(self):
        if not self.cp:
            return
        ok = self.cp.LIMITS.get("startup", False)
        for b in self.boxes:
            b.setEnabled(ok)
        if not ok:
            self.info.setText("Ces réglages ne sont disponibles qu'avec le firmware officiel Radtel "
                              "(V3.18 ou plus récent).")
            return
        self.info.setText("Réglages appliqués à l'allumage du poste et à l'affichage. "
                          "Écrivez dans la radio pour les envoyer.")
        widgets = list(self.widgets.values()) + [self.e_msg]
        with _Block(widgets):
            self.e_msg.setText(self.cp.settings.startup_message)
            for key, _, _, _, choices, _, _ in self.S.STARTUP_FIELDS:
                w = self.widgets[key]
                val = self.cp.startup[key]
                if choices == "zones":
                    w.clear()
                    for i in range(self.S.ZN_MAX):
                        z = self.cp.zones.get(i)
                        w.addItem(f"{i + 1} — {z.name}" if z else f"{i + 1} — (zone vide)")
                if isinstance(w, QSpinBox):
                    w.setValue(max(w.minimum(), min(val, w.maximum())))
                else:
                    if val >= w.count():
                        w.addItem(f"Valeur inconnue ({val})")
                        w.setCurrentIndex(w.count() - 1)
                    else:
                        w.setCurrentIndex(val)

    def save(self, *_):
        if not self.cp or not self.cp.LIMITS.get("startup", False):
            return
        changed = False
        for key, _, _, _, choices, _, _ in self.S.STARTUP_FIELDS:
            w = self.widgets[key]
            if isinstance(w, QSpinBox):
                v = w.value()
            else:
                v = w.currentIndex()
                n = self.S.ZN_MAX if choices == "zones" else len(choices)
                if v >= n:
                    continue                  # valeur inconnue conservée telle quelle
            if self.cp.startup[key] != v:
                self.cp.startup[key] = v
                changed = True
        if changed:
            self.changed.emit()

    def save_msg(self):
        if self.cp and self.e_msg.text() != self.cp.settings.startup_message:
            self.cp.settings.startup_message = self.e_msg.text()
            self.changed.emit()


# ───────────────────────────── Limites de fréquences ─────────────────────────────

class FreqLimitsTab(BaseTab):
    """Les 4 plages de limites de fréquences du firmware officiel Radtel (V3.18+)."""

    def __init__(self):
        super().__init__()
        from . import stock as S
        self.S = S
        v = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        v.addWidget(self.info)
        self.box = QGroupBox("Plages (bornes incluses, en MHz entiers)")
        g = QFormLayout(self.box)
        self.rows = []
        for i in range(S.LIMIT_N):
            t = QComboBox()
            t.addItems(S.LIMIT_TYPES)
            a = QSpinBox(minimum=0, maximum=65535, suffix=" MHz")
            b = QSpinBox(minimum=0, maximum=65535, suffix=" MHz")
            for w in (a, b):
                w.valueChanged.connect(self.save)
            t.currentIndexChanged.connect(self.save)
            h = QHBoxLayout()
            h.addWidget(t, 2)
            h.addWidget(QLabel("de"))
            h.addWidget(a, 1)
            h.addWidget(QLabel("à"))
            h.addWidget(b, 1)
            g.addRow(f"Plage {i + 1}", h)
            self.rows.append((t, a, b))
        v.addWidget(self.box)
        bar = QHBoxLayout()
        self.b_ham = QPushButton("Émission limitée aux bandes radioamateur (144-146 et 430-440 MHz)")
        self.b_ham.clicked.connect(lambda: self.apply([list(x) for x in S.HAM_PRESET]))
        self.b_none = QPushButton("Aucune limite (réglage d'usine)")
        self.b_none.clicked.connect(lambda: self.apply([list(S.LIMIT_UNUSED)] * S.LIMIT_N))
        bar.addWidget(self.b_ham)
        bar.addWidget(self.b_none)
        bar.addStretch()
        v.addLayout(bar)
        self.check = QLabel()
        self.check.setWordWrap(True)
        self.check.setTextFormat(Qt.RichText)
        v.addWidget(self.check)
        help_ = QLabel(
            "<b>Comment ça marche</b> (analyse du firmware V3.25) :<br>"
            "• <b>Émission interdite</b> : le poste refuse d'émettre dans la plage et affiche "
            "« Prohibit TX ». Les bornes sont incluses : « 18 à 144 » interdit aussi 144,000 MHz.<br>"
            "• <b>Réception seulement dans cette plage</b> : si au moins une plage de ce type existe, "
            "le poste ne reçoit que dans ces plages.<br>"
            "• Quels que soient ces réglages, le poste n'émet jamais de 108 à 136 MHz (bande aviation) "
            "et ne reçoit que de 18 à 620 et de 840 à 1000 MHz.<br>"
            "• Écrivez dans la radio pour appliquer les limites. Pour les retirer, choisissez "
            "« Aucune limite » puis écrivez à nouveau.")
        help_.setWordWrap(True)
        help_.setTextFormat(Qt.RichText)
        v.addWidget(help_)
        v.addStretch()

    def _ok(self) -> bool:
        return bool(self.cp) and self.cp.LIMITS.get("freq_limits", False)

    def refresh(self):
        ok = self._ok()
        for w in (self.box, self.b_ham, self.b_none):
            w.setEnabled(ok and not self.ro)
        if not ok:
            self.info.setText("Les limites de fréquences ne sont disponibles qu'avec le firmware "
                              "officiel Radtel (V3.18 ou plus récent).")
            self.check.clear()
            return
        self.info.setText("Le firmware officiel peut interdire l'émission sur certaines plages. "
                          "Par défaut, les 4 plages sont inactives : le poste peut alors émettre de "
                          "18 à 1000 MHz (sauf 108-136 MHz).")
        ws = [w for row in self.rows for w in row]
        with _Block(ws):
            for (t, a, b), (lt, la, lb) in zip(self.rows, self.cp.freq_limits):
                while t.count() > len(self.S.LIMIT_TYPES):
                    t.removeItem(t.count() - 1)
                if lt >= len(self.S.LIMIT_TYPES):
                    t.addItem(f"Valeur inconnue ({lt})")
                    t.setCurrentIndex(t.count() - 1)
                else:
                    t.setCurrentIndex(lt)
                a.setValue(la)
                b.setValue(lb)
        self.update_check()

    def apply(self, limits):
        self.cp.freq_limits = limits
        self.refresh()
        self.changed.emit()

    def save(self, *_):
        if not self._ok():
            return
        new = []
        for (t, a, b), old in zip(self.rows, self.cp.freq_limits):
            ti = t.currentIndex()
            new.append([ti if ti < len(self.S.LIMIT_TYPES) else old[0], a.value(), b.value()])
        if new != self.cp.freq_limits:
            self.cp.freq_limits = new
            self.changed.emit()
        self.update_check()

    def update_check(self):
        S = self.S
        lim = self.cp.freq_limits
        pb = [f"Plage {i + 1} : début après la fin, ou hors de {S.LIMIT_MHZ[0]}-{S.LIMIT_MHZ[1]} MHz."
              for i, (t, a, b) in enumerate(lim)
              if t in (1, 2) and not (S.LIMIT_MHZ[0] <= a <= b <= S.LIMIT_MHZ[1])]
        blocked = [f"{slot + 1} « {ch.name} » ({fmt_mhz(ch.tx)} MHz)"
                   for slot, ch in sorted(self.cp.channels.items())
                   if ch.tx and not S.tx_allowed(lim, ch.tx)]
        html = []
        if pb:
            html.append("<span style='color:#c0392b'><b>À corriger :</b> " + " ".join(pb) + "</span>")
        if blocked:
            more = f" … et {len(blocked) - 15} autres" if len(blocked) > 15 else ""
            html.append(f"<b>{len(blocked)} canal(aux) ne pourront pas émettre</b> avec ces "
                        f"limites : " + ", ".join(blocked[:15]) + more + ".")
        elif not pb:
            html.append("<span style='color:#27ae60'>Tous les canaux du codeplug pourront émettre "
                        "avec ces limites.</span>")
        self.check.setText("<br>".join(html))
