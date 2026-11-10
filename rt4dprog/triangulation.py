"""Fenêtre « Triangulation SATER » : relevés du carnet du mode SATER (position + azimut), croisement
des relèvements, carte (fond OpenStreetMap facultatif), export KML et CSV."""

from __future__ import annotations

import csv
import math
import os
import threading
import urllib.request

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QMessageBox, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import __version__
from . import geo

COLS = ["Position", "Azimut (°)", "Niveau (dBm)", "Note"]
COLORS = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b", "#e377c2", "#17becf"]
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

HELP = ("Recopiez ici les relevés du carnet du mode SATER (touche # de la radio) : la position "
        "(ligne du bas, ou un locator) et l'azimut. Dès deux relevés, le point où les relèvements se "
        "croisent est calculé. Azimut lu sur une boussole : indiquez la déclinaison magnétique "
        "(environ +3° est dans les Alpes-Maritimes).")


class TileLoader(QObject):
    """Téléchargement des tuiles OpenStreetMap dans un fil séparé, avec cache sur disque."""
    loaded = Signal(int, int, int)

    def __init__(self, cache_dir: str):
        super().__init__()
        self.cache_dir = cache_dir
        self.mem: dict[tuple[int, int, int], QPixmap] = {}
        self.pending: set[tuple[int, int, int]] = set()
        self.failed = False

    def path(self, z, x, y):
        return os.path.join(self.cache_dir, str(z), str(x), "%d.png" % y)

    def get(self, z, x, y):
        key = (z, x, y)
        if key in self.mem:
            return self.mem[key]
        p = self.path(z, x, y)
        if os.path.exists(p):
            pm = QPixmap(p)
            if not pm.isNull():
                self.mem[key] = pm
                return pm
        if key not in self.pending and not self.failed:
            self.pending.add(key)
            threading.Thread(target=self._fetch, args=key, daemon=True).start()
        return None

    def _fetch(self, z, x, y):
        try:
            from .updater import _urlopen
            req = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y), headers={
                "User-Agent": "RT4D-Programmer/%s (+https://github.com/F4JCF/rt4d-programmer)" % __version__})
            with _urlopen(req, 10) as r:
                data = r.read()
            p = self.path(z, x, y)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
            self.loaded.emit(z, x, y)
        except Exception:
            self.failed = True                   # pas d'Internet : carte sans fond
            self.loaded.emit(z, x, y)
        finally:
            self.pending.discard((z, x, y))


class MapView(QWidget):
    def __init__(self, tiles: TileLoader):
        super().__init__()
        self.tiles = tiles
        self.use_tiles = True
        self.stations = []          # (lat, lon, azimut corrigé, n° de ligne)
        self.sol = None
        self.setMinimumSize(420, 360)
        tiles.loaded.connect(lambda *a: self.update())

    def set_data(self, stations, sol):
        self.stations, self.sol = stations, sol
        self.update()

    def _frame(self):
        pts = [(s[0], s[1]) for s in self.stations]
        far = 3000.0
        if self.sol is not None and self.sol.ok:
            pts.append((self.sol.lat, self.sol.lon))
            far = max(1000.0, 1.3 * max(geo.distance_m((s[0], s[1]), (self.sol.lat, self.sol.lon))
                                        for s in self.stations))
        for s in self.stations:
            pts.append(geo.destination(s[0], s[1], s[2], far if self.sol is not None and self.sol.ok else 3000.0))
        return pts, far

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#eef2e6"))
        if not self.stations:
            p.drawText(self.rect(), Qt.AlignCenter, "Saisissez au moins un relevé\n(position et azimut)")
            return
        pts, far = self._frame()
        W, H = self.width(), self.height()
        zoom = 18
        while zoom > 3:
            xs, ys = zip(*(geo.merc_px(a, b, zoom) for a, b in pts))
            if max(xs) - min(xs) < W * 0.85 and max(ys) - min(ys) < H * 0.85:
                break
            zoom -= 1
        xs, ys = zip(*(geo.merc_px(a, b, zoom) for a, b in pts))
        ox = (max(xs) + min(xs)) / 2 - W / 2
        oy = (max(ys) + min(ys)) / 2 - H / 2
        to = lambda lat, lon: QPointF(geo.merc_px(lat, lon, zoom)[0] - ox, geo.merc_px(lat, lon, zoom)[1] - oy)
        if self.use_tiles:
            n = 2 ** zoom
            for tx in range(int(ox // 256), int((ox + W) // 256) + 1):
                for ty in range(int(oy // 256), int((oy + H) // 256) + 1):
                    if 0 <= ty < n:
                        pm = self.tiles.get(zoom, tx % n, ty)
                        if pm is not None:
                            p.drawPixmap(int(tx * 256 - ox), int(ty * 256 - oy), pm)
        # relèvements
        for i, (lat, lon, az, row) in enumerate(self.stations):
            col = QColor(COLORS[row % len(COLORS)])
            p.setPen(QPen(col, 2))
            a = to(lat, lon)
            b = to(*geo.destination(lat, lon, az, far * 1.2))
            p.drawLine(a, b)
            p.setBrush(QBrush(col))
            p.drawEllipse(a, 5, 5)
            p.setPen(QPen(Qt.black))
            p.setFont(QFont("Sans", 9, QFont.Bold))
            p.drawText(a + QPointF(7, -7), str(row + 1))
        sol = self.sol
        if sol is not None and sol.ok:
            c = to(sol.lat, sol.lon)
            mpp = 156543.03 * math.cos(math.radians(sol.lat)) / 2 ** zoom      # mètres par pixel
            r = max(6.0, sol.rms_m / mpp)
            p.setPen(QPen(QColor("#000000"), 2))
            p.setBrush(QBrush(QColor(255, 215, 0, 110)))
            p.drawEllipse(c, r, r)
            p.drawLine(c + QPointF(-9, 0), c + QPointF(9, 0))
            p.drawLine(c + QPointF(0, -9), c + QPointF(0, 9))
        # échelle
        lat_c = geo.merc_latlon(ox + W / 2, oy + H / 2, zoom)[0]
        mpp = 156543.03 * math.cos(math.radians(lat_c)) / 2 ** zoom
        target = 120 * mpp
        step = 10 ** math.floor(math.log10(target))
        for m in (5, 2, 1):
            if m * step <= target:
                step *= m
                break
        px = step / mpp
        p.setPen(QPen(Qt.black, 3))
        p.drawLine(QPointF(12, H - 14), QPointF(12 + px, H - 14))
        p.setFont(QFont("Sans", 8))
        p.drawText(QPointF(12, H - 20), "%g km" % (step / 1000) if step >= 1000 else "%d m" % step)
        if self.use_tiles:
            p.setPen(QPen(QColor("#333333")))
            p.drawText(QRectF(0, H - 16, W - 6, 14), Qt.AlignRight,
                       "© contributeurs OpenStreetMap" if not self.tiles.failed else "Fond de carte indisponible (Internet ?)")
        p.end()


class TriangulationDialog(QDialog):
    def __init__(self, parent=None, settings=None, cache_dir=None):
        super().__init__(parent)
        self.setWindowTitle("Triangulation SATER")
        self.resize(1100, 640)
        self.settings = settings
        cache = cache_dir or os.path.join(os.path.expanduser("~"), ".cache", "rt4d-programmer", "tuiles")
        self.tiles = TileLoader(cache)

        self.table = QTableWidget(6, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeaderItem(0).setToolTip("Exemples : 43.6012 E07.0034 (carnet de la radio), "
                                                      "43.6012, 7.0034 ou un locator JN33LP")
        self.table.itemChanged.connect(lambda *_: self.recalc())

        self.decl = QDoubleSpinBox()
        self.decl.setRange(-30, 30)
        self.decl.setDecimals(1)
        self.decl.setSuffix(" ° est")
        self.decl.setToolTip("Déclinaison magnétique ajoutée aux azimuts lus sur une boussole "
                             "(environ +3° est dans les Alpes-Maritimes). 0 si les azimuts sont géographiques.")
        if settings is not None:
            self.decl.setValue(float(settings.value("tri_decl", 0.0)))
        self.decl.valueChanged.connect(lambda *_: self.recalc())

        self.osm = QCheckBox("Fond de carte OpenStreetMap (Internet)")
        self.osm.setChecked(settings.value("tri_osm", "true") == "true" if settings is not None else True)
        self.osm.toggled.connect(self._toggle_osm)

        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.map = MapView(self.tiles)
        self.map.use_tiles = self.osm.isChecked()

        b_add = QPushButton("Ajouter une ligne")
        b_add.clicked.connect(lambda: self.table.insertRow(self.table.rowCount()))
        b_del = QPushButton("Supprimer la ligne")
        b_del.clicked.connect(self._del_row)
        b_open = QPushButton("Ouvrir (CSV)…")
        b_open.clicked.connect(self.load_csv)
        b_save = QPushButton("Enregistrer (CSV)…")
        b_save.clicked.connect(self.save_csv)
        b_kml = QPushButton("Exporter KML (Google Earth)…")
        b_kml.clicked.connect(self.export_kml)
        self.b_osm = QPushButton("Voir le point sur OpenStreetMap")
        self.b_osm.clicked.connect(self.open_osm)

        left = QWidget()
        lv = QVBoxLayout(left)
        hl = QLabel(HELP)
        hl.setWordWrap(True)
        lv.addWidget(hl)
        lv.addWidget(self.table)
        row = QHBoxLayout()
        for b in (b_add, b_del, b_open, b_save):
            row.addWidget(b)
        lv.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Déclinaison :"))
        row.addWidget(self.decl)
        row.addWidget(self.osm)
        row.addStretch(1)
        lv.addLayout(row)
        lv.addWidget(self.result)
        row = QHBoxLayout()
        row.addWidget(b_kml)
        row.addWidget(self.b_osm)
        lv.addLayout(row)

        sp = QSplitter()
        sp.addWidget(left)
        sp.addWidget(self.map)
        sp.setSizes([560, 540])
        v = QVBoxLayout(self)
        v.addWidget(sp)
        self.sol = None
        self.recalc()

    # --- données
    def rows(self):
        """Lignes complètes : (n° de ligne, lat, lon, azimut) ; erreurs : liste de textes."""
        out, errs = [], []
        for r in range(self.table.rowCount()):
            cell = lambda c: (self.table.item(r, c).text().strip() if self.table.item(r, c) else "")
            pos, az = cell(0), cell(1).replace(",", ".").replace("°", "")
            if not pos and not az:
                continue
            try:
                lat, lon = geo.parse_position(pos)
                a = float(az)
                if not 0 <= a < 360:
                    raise geo.PositionError("azimut hors de 0-359")
                out.append((r, lat, lon, a))
            except (geo.PositionError, ValueError) as e:
                errs.append("Ligne %d : %s" % (r + 1, e if isinstance(e, geo.PositionError) else "azimut manquant ou illisible"))
        return out, errs

    def recalc(self):
        rows, errs = self.rows()
        d = self.decl.value()
        st = [(lat, lon, a) for _, lat, lon, a in rows]
        self.sol = geo.solve(st, d) if len(st) >= 2 else None
        self.map.set_data([(lat, lon, (a + d) % 360, r) for r, lat, lon, a in rows], self.sol)
        txt = []
        if self.sol is not None and self.sol.ok:
            txt.append("<b>Balise estimée : %s</b> — locator %s<br>Écart moyen des relèvements : %.0f m"
                       % (geo.format_position(self.sol.lat, self.sol.lon), geo.latlon_to_locator(self.sol.lat, self.sol.lon),
                          self.sol.rms_m))
            for i, (lat, lon, a) in enumerate(st):
                txt.append("Relevé %d → %.2f km" % (rows[i][0] + 1, geo.distance_m((lat, lon), (self.sol.lat, self.sol.lon)) / 1000))
        elif len(st) < 2:
            txt.append("Deux relevés complets au moins (position et azimut) pour calculer le croisement.")
        if self.sol is not None:
            txt += ["⚠ " + w for w in self.sol.warnings]
        txt += ["⚠ " + e for e in errs]
        self.result.setText("<br>".join(txt))
        self.b_osm.setEnabled(self.sol is not None and self.sol.ok)
        if self.settings is not None:
            self.settings.setValue("tri_decl", d)

    def _toggle_osm(self, on):
        self.map.use_tiles = on
        self.tiles.failed = False
        if self.settings is not None:
            self.settings.setValue("tri_osm", "true" if on else "false")
        self.map.update()

    def _del_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self.recalc()

    # --- fichiers
    def load_csv(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Ouvrir des relevés", "", "CSV (*.csv)")
        if not fn:
            return
        with open(fn, newline="", encoding="utf-8-sig") as f:
            data = [r for r in csv.reader(f, delimiter=";")]
        if data and data[0] and data[0][0].lower().startswith("position"):
            data = data[1:]
        self.table.blockSignals(True)
        self.table.setRowCount(max(6, len(data) + 2))
        self.table.clearContents()
        for r, rec in enumerate(data):
            for c in range(min(len(COLS), len(rec))):
                self.table.setItem(r, c, QTableWidgetItem(rec[c]))
        self.table.blockSignals(False)
        self.recalc()

    def save_csv(self):
        fn, _ = QFileDialog.getSaveFileName(self, "Enregistrer les relevés", "releves_sater.csv", "CSV (*.csv)")
        if not fn:
            return
        with open(fn, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Position", "Azimut", "Niveau dBm", "Note"])
            for r in range(self.table.rowCount()):
                rec = [(self.table.item(r, c).text() if self.table.item(r, c) else "") for c in range(len(COLS))]
                if any(x.strip() for x in rec):
                    w.writerow(rec)

    def export_kml(self):
        rows, _ = self.rows()
        if not rows:
            QMessageBox.information(self, "KML", "Aucun relevé complet.")
            return
        fn, _ = QFileDialog.getSaveFileName(self, "Exporter en KML", "triangulation_sater.kml", "KML (*.kml)")
        if not fn:
            return
        st = [(lat, lon, a) for _, lat, lon, a in rows]
        sol = self.sol if self.sol is not None else geo.Solution()
        with open(fn, "w", encoding="utf-8") as f:
            f.write(geo.kml(st, sol, self.decl.value(), ["Relevé %d" % (r + 1) for r, *_ in rows]))

    def open_osm(self):
        if self.sol is not None and self.sol.ok:
            QDesktopServices.openUrl(QUrl("https://www.openstreetmap.org/?mlat=%.5f&mlon=%.5f#map=15/%.5f/%.5f"
                                          % (self.sol.lat, self.sol.lon, self.sol.lat, self.sol.lon)))

