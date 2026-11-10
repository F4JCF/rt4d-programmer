"""Calculs de radiogoniométrie pour la triangulation SATER (sans interface graphique).

- lecture d'une position : degrés décimaux (« 43.6012 E07.0034 » tel qu'affiché par le carnet du
  mode SATER, « 43.6012, 7.0034 », « N43.6012 O1.2345 »…) ou locator Maidenhead (4 à 10 caractères) ;
- croisement des relèvements : point le plus proche de toutes les droites au sens des moindres carrés,
  dans une projection plane locale (précise à quelques mètres sur quelques dizaines de km) ;
- projection Web Mercator pour le fond de carte OpenStreetMap.
"""

from __future__ import annotations

import math
import re

R_EARTH = 6371000.0


class PositionError(ValueError):
    pass


def locator_to_latlon(loc: str) -> tuple[float, float]:
    """Centre du carré d'un locator Maidenhead de 4, 6, 8 ou 10 caractères."""
    s = loc.strip().upper()
    if len(s) not in (4, 6, 8, 10) or not re.fullmatch(r"[A-R]{2}\d{2}([A-X]{2}(\d{2}([A-X]{2})?)?)?", s):
        raise PositionError("locator invalide : %r" % loc)
    lon, lat = -180.0, -90.0
    w, h = 20.0, 10.0
    lon += (ord(s[0]) - 65) * w
    lat += (ord(s[1]) - 65) * h
    w, h = w / 10, h / 10
    lon += int(s[2]) * w
    lat += int(s[3]) * h
    i = 4
    while i < len(s):
        if i in (4, 8):
            w, h = w / 24, h / 24
            lon += (ord(s[i]) - 65) * w
            lat += (ord(s[i + 1]) - 65) * h
        else:
            w, h = w / 10, h / 10
            lon += int(s[i]) * w
            lat += int(s[i + 1]) * h
        i += 2
    return lat + h / 2, lon + w / 2


def parse_position(text: str) -> tuple[float, float]:
    """Position en degrés décimaux (nord et est positifs)."""
    s = text.strip().upper().replace(",", " ").replace(";", " ")
    if not s:
        raise PositionError("position vide")
    if re.fullmatch(r"[A-R]{2}\d{2}[A-X0-9]*", s.replace(" ", "")):
        return locator_to_latlon(s.replace(" ", ""))
    m = re.fullmatch(r"([NS])?\s*(-?\d+(?:\.\d+)?)\s*([NS])?\s+([EOW])?\s*(-?\d+(?:\.\d+)?)\s*([EOW])?", s)
    if not m:
        raise PositionError("position illisible : %r (exemple : 43.6012 E07.0034 ou JN33LP)" % text)
    lat, lon = float(m.group(2)), float(m.group(5))
    if "S" in (m.group(1), m.group(3)):
        lat = -abs(lat)
    if any(x in ("O", "W") for x in (m.group(4), m.group(6))):
        lon = -abs(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise PositionError("position hors limites : %r" % text)
    return lat, lon


def format_position(lat: float, lon: float) -> str:
    return "%.4f %s%07.4f" % (lat, "E" if lon >= 0 else "O", abs(lon))


def latlon_to_locator(lat: float, lon: float) -> str:
    lon += 180.0
    lat += 90.0
    s = chr(65 + int(lon // 20)) + chr(65 + int(lat // 10))
    lon %= 20; lat %= 10
    s += str(int(lon // 2)) + str(int(lat // 1))
    lon %= 2; lat %= 1
    s += chr(65 + int(lon / (2 / 24))) + chr(65 + int(lat / (1 / 24)))
    lon %= 2 / 24; lat %= 1 / 24
    s += str(int(lon / (2 / 240))) + str(int(lat / (1 / 240)))
    return s


class Plane:
    """Projection plane locale (mètres, x vers l'est, y vers le nord) autour d'une origine."""

    def __init__(self, lat0: float, lon0: float):
        self.lat0, self.lon0 = lat0, lon0
        self.k = math.cos(math.radians(lat0))

    def xy(self, lat: float, lon: float) -> tuple[float, float]:
        return (math.radians(lon - self.lon0) * R_EARTH * self.k, math.radians(lat - self.lat0) * R_EARTH)

    def latlon(self, x: float, y: float) -> tuple[float, float]:
        return self.lat0 + math.degrees(y / R_EARTH), self.lon0 + math.degrees(x / (R_EARTH * self.k))


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    p = Plane((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    (x1, y1), (x2, y2) = p.xy(*a), p.xy(*b)
    return math.hypot(x2 - x1, y2 - y1)


class Solution:
    def __init__(self):
        self.lat = self.lon = None        # point estimé
        self.rms_m = None                 # écart moyen des droites au point (mètres)
        self.pairs = []                   # croisements deux à deux : (i, j, lat, lon)
        self.warnings: list[str] = []

    @property
    def ok(self) -> bool:
        return self.lat is not None


def solve(stations, declination: float = 0.0) -> Solution:
    """stations : liste de (lat, lon, azimut en degrés depuis le nord, sens horaire).
    L'azimut corrigé est azimut + déclinaison (déclinaison est comptée positive vers l'est)."""
    sol = Solution()
    if len(stations) < 2:
        sol.warnings.append("Il faut au moins deux relèvements avec position et azimut.")
        return sol
    lat0 = sum(s[0] for s in stations) / len(stations)
    lon0 = sum(s[1] for s in stations) / len(stations)
    pl = Plane(lat0, lon0)
    lines = []
    for lat, lon, az in stations:
        a = math.radians((az + declination) % 360)
        x, y = pl.xy(lat, lon)
        lines.append((x, y, math.sin(a), math.cos(a)))   # point et direction unitaire
    # moindres carrés : somme des distances perpendiculaires au carré (normale n = (cos a, -sin a))
    sxx = sxy = syy = bx = by = 0.0
    for x, y, dx, dy in lines:
        nx, ny = dy, -dx
        c = nx * x + ny * y
        sxx += nx * nx; sxy += nx * ny; syy += ny * ny
        bx += nx * c; by += ny * c
    det = sxx * syy - sxy * sxy
    if abs(det) < 1e-6 * len(lines) ** 2:
        sol.warnings.append("Relèvements parallèles : pas de croisement. Changez de point de relèvement.")
        return sol
    px = (bx * syy - by * sxy) / det
    py = (by * sxx - bx * sxy) / det
    sol.lat, sol.lon = pl.latlon(px, py)
    sq = 0.0
    for i, (x, y, dx, dy) in enumerate(lines):
        nx, ny = dy, -dx
        sq += (nx * (px - x) + ny * (py - y)) ** 2
        if dx * (px - x) + dy * (py - y) < 0:
            sol.warnings.append("Relèvement n°%d : le point trouvé est derrière la station (azimut à vérifier)." % (i + 1))
    sol.rms_m = math.sqrt(sq / len(lines))
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            x1, y1, dx1, dy1 = lines[i]
            x2, y2, dx2, dy2 = lines[j]
            d = dx1 * dy2 - dy1 * dx2
            if abs(d) < 1e-9:
                continue
            t = ((x2 - x1) * dy2 - (y2 - y1) * dx2) / d
            u = ((x2 - x1) * dy1 - (y2 - y1) * dx1) / d
            if t >= 0 and u >= 0:                         # croisement devant les deux stations
                sol.pairs.append((i, j) + pl.latlon(x1 + t * dx1, y1 + t * dy1))
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            ang = abs(math.degrees(math.atan2(lines[i][2] * lines[j][3] - lines[i][3] * lines[j][2],
                                              lines[i][2] * lines[j][2] + lines[i][3] * lines[j][3])))
            if min(ang, 180 - ang) < 15 and len(lines) == 2:
                sol.warnings.append("Les deux relèvements se croisent sous un angle faible (moins de 15°) : "
                                    "position peu précise.")
    return sol


def destination(lat: float, lon: float, az: float, dist_m: float) -> tuple[float, float]:
    """Point à dist_m mètres dans la direction az (projection locale)."""
    pl = Plane(lat, lon)
    a = math.radians(az)
    return pl.latlon(math.sin(a) * dist_m, math.cos(a) * dist_m)


# Web Mercator (tuiles OpenStreetMap de 256 pixels)
def merc_px(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    n = 256 * 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(max(-85.0, min(85.0, lat))))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def merc_latlon(x: float, y: float, zoom: int) -> tuple[float, float]:
    n = 256 * 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def kml(stations, sol: Solution, declination: float, names=None) -> str:
    """Fichier KML (Google Earth, applications de cartes) : stations, relèvements, point estimé."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Triangulation SATER</name>']
    far = 20000.0
    if sol.ok:
        far = max(2000.0, 1.5 * max(distance_m((s[0], s[1]), (sol.lat, sol.lon)) for s in stations))
    for i, (lat, lon, az) in enumerate(stations):
        nm = names[i] if names else "Relevé %d" % (i + 1)
        e_lat, e_lon = destination(lat, lon, (az + declination) % 360, far)
        out.append('<Placemark><name>%s</name><description>azimut %.0f°</description>'
                   '<Point><coordinates>%.6f,%.6f,0</coordinates></Point></Placemark>' % (nm, az, lon, lat))
        out.append('<Placemark><name>%s : %.0f°</name><LineString><coordinates>%.6f,%.6f,0 %.6f,%.6f,0'
                   '</coordinates></LineString></Placemark>' % (nm, az, lon, lat, e_lon, e_lat))
    if sol.ok:
        out.append('<Placemark><name>Balise estimée</name><description>écart moyen %.0f m</description>'
                   '<Point><coordinates>%.6f,%.6f,0</coordinates></Point></Placemark>' % (sol.rms_m, sol.lon, sol.lat))
    out.append('</Document></kml>')
    return "\n".join(out)
