"""Fenêtre « Logo de démarrage » (Radio → Logo de démarrage), idée n°24."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSlider, QSpinBox, QVBoxLayout,
)

from . import f4set as FS
from . import logo as L
from . import radio_ops

INFO = ("Image affichée pendant 3 s à l'allumage : 128 × 64 points noir et blanc. Choisissez une "
        "image (PNG, JPG, BMP…), réglez le contraste, ajoutez un texte si vous voulez, puis écrivez "
        "dans la radio. Lisez d'abord la radio : son logo et les réglages des modes F4JCF rangés à "
        "côté sont enregistrés sur l'ordinateur avant l'écriture, et les réglages sont conservés.<br>"
        "L'affichage du logo se règle dans l'onglet <i>Menu démarrage</i> (« Logo au démarrage »).")

LCD_BG = QColor(178, 196, 160)
LCD_FG = QColor(25, 30, 25)


class LogoDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.sector = None          # secteur lu dans la radio
        self.src = None             # image choisie (QImage)
        self.radio_px = None        # logo lu dans la radio
        self.px = [[False] * L.W for _ in range(L.H)]
        self.setWindowTitle("Logo de démarrage")
        self.resize(640, 640)
        v = QVBoxLayout(self)
        lab = QLabel(INFO)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.RichText)
        v.addWidget(lab)
        self.view = QLabel()
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setMinimumHeight(4 * L.H + 20)
        v.addWidget(self.view)
        self.state = QLabel("Pas encore lu.")
        self.state.setWordWrap(True)
        v.addWidget(self.state)

        f = QFormLayout()
        self.fit = QComboBox()
        self.fit.addItems(["Adapter (image entière)", "Remplir (recadrer)", "Étirer"])
        self.thr = QSlider(Qt.Horizontal)
        self.thr.setRange(10, 245)
        self.thr.setValue(128)
        self.dither = QCheckBox("Tramage (pour une photo ou des dégradés)")
        self.invert = QCheckBox("Inverser noir et blanc")
        self.text = QLineEdit()
        self.text.setMaxLength(20)
        self.text.setPlaceholderText("facultatif, ex. « F4JCF  ADRASEC 06 »")
        self.tsize = QSpinBox()
        self.tsize.setRange(6, 30)
        self.tsize.setValue(11)
        self.tsize.setSuffix(" points")
        self.tpos = QComboBox()
        self.tpos.addItems(["En bas", "En haut", "Au centre"])
        f.addRow("Mise en place", self.fit)
        f.addRow("Contraste (seuil)", self.thr)
        f.addRow("", self.dither)
        f.addRow("", self.invert)
        f.addRow("Texte", self.text)
        row = QHBoxLayout()
        row.addWidget(self.tsize)
        row.addWidget(self.tpos)
        f.addRow("Taille et place du texte", row)
        v.addLayout(f)
        for w in (self.fit, self.tpos):
            w.currentIndexChanged.connect(self.render)
        for w in (self.thr, self.tsize):
            w.valueChanged.connect(self.render)
        for w in (self.dither, self.invert):
            w.toggled.connect(self.render)
        self.text.textChanged.connect(self.render)

        row = QHBoxLayout()
        buttons = (("Lire la radio", self.read), ("Ouvrir une image…", self.open_image),
                   ("Logo de la radio", self.use_radio), ("Enregistrer…", self.save),
                   ("Écrire dans la radio", self.write))
        for text, fn in buttons:
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
            if text.startswith("Écrire"):
                self.wbtn = b
            if text == "Logo de la radio":
                self.rbtn = b
        v.addLayout(row)
        b = QPushButton("Fermer")
        b.clicked.connect(self.close)
        v.addWidget(b, alignment=Qt.AlignRight)
        self.wbtn.setEnabled(False)
        self.rbtn.setEnabled(False)
        self.show_px()

    # ── image ──
    def canvas(self) -> QImage:
        """Image 128 × 64 en niveaux de gris : image choisie mise en place, puis texte."""
        img = QImage(L.W, L.H, QImage.Format_Grayscale8)
        img.fill(255)
        p = QPainter(img)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.src is not None:
            mode = self.fit.currentIndex()
            if mode == 2:
                s = self.src.scaled(L.W, L.H, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            else:
                asp = Qt.KeepAspectRatio if mode == 0 else Qt.KeepAspectRatioByExpanding
                s = self.src.scaled(L.W, L.H, asp, Qt.SmoothTransformation)
            p.drawImage((L.W - s.width()) // 2, (L.H - s.height()) // 2, s)
        t = self.text.text().strip()
        if t:
            font = QFont("DejaVu Sans")
            font.setPixelSize(self.tsize.value())
            font.setBold(True)
            p.setFont(font)
            h = self.tsize.value() + 2
            pos = self.tpos.currentIndex()
            y = L.H - h if pos == 0 else 0 if pos == 1 else (L.H - h) // 2
            if self.src is not None:                       # fond blanc sous le texte
                p.fillRect(0, y, L.W, h, QColor(255, 255, 255))
            p.setPen(QColor(0, 0, 0))
            p.drawText(0, y, L.W, h, Qt.AlignCenter, t)
        p.end()
        return img

    def render(self):
        if self.src is None and not self.text.text().strip():
            return
        img = self.canvas()
        gray = [[img.pixelColor(x, y).value() for x in range(L.W)] for y in range(L.H)]
        self.px = L.from_gray(gray, self.thr.value(), self.dither.isChecked(), self.invert.isChecked())
        self.show_px()

    def show_px(self, px=None):
        px = px or self.px
        s = 4
        pm = QPixmap(L.W * s + 16, L.H * s + 16)
        pm.fill(QColor(70, 80, 70))
        p = QPainter(pm)
        p.fillRect(8, 8, L.W * s, L.H * s, LCD_BG)
        for y in range(L.H):
            for x in range(L.W):
                if px[y][x]:
                    p.fillRect(8 + x * s, 8 + y * s, s - 1, s - 1, LCD_FG)
        p.end()
        self.view.setPixmap(pm)

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Image du logo", self.main.last_dir(),
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;Logo RT-4D (*.bin);;Tous les fichiers (*)")
        if not path:
            return
        if path.lower().endswith(".bin"):
            data = open(path, "rb").read()
            if len(data) >= 0x101000:                      # sauvegarde complète de la flash
                data = data[FS.SECTOR_ADDR:FS.SECTOR_ADDR + 1024]
            try:
                self.px = L.decode(data[:1024])
            except ValueError as e:
                QMessageBox.warning(self, "Logo", str(e))
                return
            self.src = None
            self.text.clear()
            self.show_px()
            self.state.setText(f"Logo ouvert : {os.path.basename(path)} (pas encore écrit dans la radio).")
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Logo", "Image illisible.")
            return
        if img.hasAlphaChannel():                          # transparence -> blanc
            bg = QImage(img.size(), QImage.Format_RGB32)
            bg.fill(QColor(255, 255, 255))
            p = QPainter(bg)
            p.drawImage(0, 0, img)
            p.end()
            img = bg
        self.src = img.convertToFormat(QImage.Format_Grayscale8)
        self.render()
        self.state.setText(f"Image : {os.path.basename(path)} ({img.width()} × {img.height()}) — "
                           "aperçu tel que sur l'écran du poste.")

    def use_radio(self):
        if self.radio_px:
            self.src = None
            self.text.clear()
            self.px = [row[:] for row in self.radio_px]
            self.show_px()

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer le logo",
                                              os.path.join(self.main.last_dir(), "logo-rt4d.png"),
                                              "Image PNG (*.png);;Logo RT-4D (*.bin)")
        if not path:
            return
        if path.lower().endswith(".bin"):
            open(path, "wb").write(L.encode(self.px))
        else:
            img = QImage(L.W, L.H, QImage.Format_Mono)
            img.setColorTable([QColor(255, 255, 255).rgb(), QColor(0, 0, 0).rgb()])
            for y in range(L.H):
                for x in range(L.W):
                    img.setPixel(x, y, 1 if self.px[y][x] else 0)
            img.save(path)

    # ── radio ──
    def read(self):
        port = self.main.port_name()
        if not port:
            return
        from .gui import TaskDialog
        ok, sector = TaskDialog.run(self, "Lecture du logo", radio_ops.read_f4set, port)
        if not ok:
            return
        self.sector = sector
        self.radio_px = L.decode(sector[:1024])
        self.src = None
        self.text.clear()
        self.px = [row[:] for row in self.radio_px]
        self.show_px()
        empty = L.black_ratio(self.px) in (0.0, 1.0)
        self.state.setText("Logo de la radio lu" + (" (vide)." if empty else "."))
        self.wbtn.setEnabled(True)
        self.rbtn.setEnabled(True)

    def write(self):
        if self.sector is None:
            QMessageBox.information(self, "Logo", "Lisez d'abord la radio.")
            return
        r = L.black_ratio(self.px)
        if r in (0.0, 1.0):
            if QMessageBox.question(self, "Logo", "Le logo est entièrement blanc ou noir. "
                                    "L'écrire quand même ?") != QMessageBox.Yes:
                return
        port = self.main.port_name()
        if not port:
            return
        data = L.encode(self.px)
        from .gui import TaskDialog
        ok, bk = TaskDialog.run(self, "Écriture du logo", radio_ops.write_logo, port, data,
                                self.sector, self.main.backup_dir())
        if ok:
            self.sector = data + self.sector[1024:]
            self.radio_px = [row[:] for row in self.px]
            self.state.setText("Logo écrit et vérifié. Il s'affichera au prochain allumage "
                               f"(si « Logo au démarrage » est en marche). Sauvegarde : {os.path.basename(bk)}")
