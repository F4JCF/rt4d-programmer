"""Exports du Journal F4JCF (idées n°36 et 37) : KML, GPX et compte rendu d'exercice (page HTML à
imprimer ou à enregistrer en PDF depuis le navigateur). Aucun accès à Internet."""
from __future__ import annotations

import datetime as dt
import html
import math

from . import geo
from . import journal as J


def bearings(jr: J.Journal):
    """Relevés utilisables pour la triangulation : (relevé, lat, lon, azimut)."""
    return [(r, r.lat, r.lon, r.azimut) for r in jr.releves
            if r.azimut is not None and r.lat is not None]


def solve(jr: J.Journal, decl: float = 0.0) -> geo.Solution:
    return geo.solve([(la, lo, az) for _, la, lo, az in bearings(jr)], decl)


def _name(jr, r, power_on):
    t = J.when(jr, r.t, r.t_prev, power_on)
    az = ("%d° " % r.azimut) if r.azimut is not None else ""
    return ("R%d %s%s" % (r.num, az, t if t and t != "session précédente" else "")).strip()


def to_kml(jr: J.Journal, decl: float = 0.0, power_on=None) -> str:
    b = bearings(jr)
    sol = solve(jr, decl)
    names = [_name(jr, r, power_on) + " (%d dBm)" % r.dbm for r, *_ in b]
    txt = geo.kml([(la, lo, az) for _, la, lo, az in b], sol, decl, names)
    # relevés avec position mais sans azimut : simples points
    extra = ['<Placemark><name>%s</name><description>%d dBm</description><Point><coordinates>%.6f,%.6f,0'
             '</coordinates></Point></Placemark>' % (html.escape(_name(jr, r, power_on)), r.dbm, r.lon, r.lat)
             for r in jr.releves if r.lat is not None and r.azimut is None]
    if extra:
        txt = txt.replace("</Document></kml>", "\n".join(extra) + "\n</Document></kml>")
    return txt


def _iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def to_gpx(jr: J.Journal, decl: float = 0.0, power_on=None) -> str:
    """GPX 1.1 : un point par relevé positionné (avec l'heure si l'allumage est connu), le point estimé,
    et chaque relèvement sous forme de route de deux points."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<gpx version="1.1" creator="RT4D Programmer" xmlns="http://www.topografix.com/GPX/1/1">']
    for r in jr.releves:
        if r.lat is None:
            continue
        t = _iso(J.clock(jr, r.t, power_on))
        out.append('<wpt lat="%.6f" lon="%.6f"><name>%s</name>%s<desc>%d dBm, touche %d%s</desc></wpt>' % (
            r.lat, r.lon, html.escape(_name(jr, r, power_on)), "<time>%s</time>" % t if t else "", r.dbm,
            r.touche, ", azimut %d°" % r.azimut if r.azimut is not None else ""))
    sol = solve(jr, decl)
    b = bearings(jr)
    far = 20000.0
    if sol.ok:
        far = max(2000.0, 1.5 * max(geo.distance_m((la, lo), (sol.lat, sol.lon)) for _, la, lo, _ in b))
        out.append('<wpt lat="%.6f" lon="%.6f"><name>Balise estimée</name><desc>écart moyen %.0f m</desc></wpt>'
                   % (sol.lat, sol.lon, sol.rms_m))
    for r, la, lo, az in b:
        e = geo.destination(la, lo, (az + decl) % 360, far)
        out.append('<rte><name>R%d %d°</name><rtept lat="%.6f" lon="%.6f"/><rtept lat="%.6f" lon="%.6f"/></rte>'
                   % (r.num, az, la, lo, e[0], e[1]))
    out.append("</gpx>")
    return "\n".join(out)


def _svg(jr, sol, decl, size=520):
    """Plan simple (sans fond de carte) : points de relevé, relèvements, point estimé, échelle."""
    b = bearings(jr)
    pts = [(r.lat, r.lon) for r in jr.releves if r.lat is not None]
    if sol.ok:
        pts.append((sol.lat, sol.lon))
    if not pts:
        return ""
    lat0 = sum(p[0] for p in pts) / len(pts)
    lon0 = sum(p[1] for p in pts) / len(pts)
    pl = geo.Plane(lat0, lon0)
    xy = [pl.xy(*p) for p in pts]
    span = max(500.0, max(max(abs(x), abs(y)) for x, y in xy) * 2.4)
    k = size / span
    P = lambda la, lo: (size / 2 + pl.xy(la, lo)[0] * k, size / 2 - pl.xy(la, lo)[1] * k)
    s = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
         'style="border:1px solid #999;background:#fbfbf7">' % (size, size, size, size)]
    for r, la, lo, az in b:
        x0, y0 = P(la, lo)
        e = geo.destination(la, lo, (az + decl) % 360, span * 2)
        x1, y1 = P(*e)
        s.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#1f5fa8" stroke-width="1.5"/>' % (x0, y0, x1, y1))
    for r in jr.releves:
        if r.lat is None:
            continue
        x, y = P(r.lat, r.lon)
        s.append('<circle cx="%.1f" cy="%.1f" r="4" fill="#1f5fa8"/><text x="%.1f" y="%.1f" font-size="12" '
                 'font-family="sans-serif">R%d</text>' % (x, y, x + 6, y - 6, r.num))
    if sol.ok:
        x, y = P(sol.lat, sol.lon)
        rr = max(4.0, (sol.rms_m or 0) * k)
        s.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="#c0392b" stroke-dasharray="4 3"/>' % (x, y, rr))
        s.append('<path d="M%.1f %.1fl8 8m0 -8l-8 8" stroke="#c0392b" stroke-width="2.5" transform="translate(-4 -4)"/>' % (x, y))
        s.append('<text x="%.1f" y="%.1f" font-size="12" font-family="sans-serif" fill="#c0392b">balise estimée</text>' % (x + 8, y + 14))
    # échelle et nord
    step = 10 ** math.floor(math.log10(span / 4))
    for m in (5, 2, 1):
        if m * step <= span / 3:
            step *= m
            break
    L = step * k
    s.append('<line x1="16" y1="%d" x2="%.1f" y2="%d" stroke="#333" stroke-width="3"/>' % (size - 16, 16 + L, size - 16))
    s.append('<text x="16" y="%d" font-size="12" font-family="sans-serif">%s</text>' % (
        size - 22, ("%g km" % (step / 1000)) if step >= 1000 else ("%g m" % step)))
    s.append('<text x="%d" y="22" font-size="14" font-family="sans-serif" font-weight="bold">N ↑</text>' % (size - 34))
    s.append("</svg>")
    return "".join(s)


def report(jr: J.Journal, meta: dict, decl: float = 0.0, power_on=None) -> str:
    """Compte rendu d'exercice (HTML autonome). meta : titre, date, lieu, operateur, balise, notes."""
    e = lambda x: html.escape(str(x or ""))
    sol = solve(jr, decl)
    rows = []
    for r in jr.releves:
        rows.append("<tr><td>%d</td><td>%s</td><td>%d dBm</td><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            r.num, e(J.when(jr, r.t, r.t_prev, power_on)), r.dbm, r.chrono, r.touche,
            "%d°" % r.azimut if r.azimut is not None else "—", e(r.position) or "—",
            e(geo.latlon_to_locator(r.lat, r.lon)) if r.lat is not None else "—"))
    st = ["<tr><td>%s</td><td>%d</td><td>%s</td><td>%d</td><td>%s</td></tr>" % (
        e(s.indicatif or "—"), s.id, e(s.appel), s.passages, e(J.when(jr, s.t, s.t_prev, power_on)))
        for s in jr.stations]
    if sol.ok:
        res = ("<p><b>Position estimée de la balise</b> : %s (%s), locator <b>%s</b> — écart moyen des "
               "relèvements %.0f m, %d relèvement(s), déclinaison %+.1f°.</p>" % (
                   e(geo.format_position(sol.lat, sol.lon)), "%.5f, %.5f" % (sol.lat, sol.lon),
                   e(geo.latlon_to_locator(sol.lat, sol.lon)), sol.rms_m, len(bearings(jr)), decl))
    else:
        res = "<p><b>Pas de position estimée</b> : %s</p>" % e(" ".join(sol.warnings) or
                                                               "il faut au moins deux relèvements avec position et azimut.")
    warn = "".join("<li>%s</li>" % e(w) for w in sol.warnings) if sol.ok else ""
    on = ("Radio allumée à %s." % power_on.strftime("%d/%m/%Y %H:%M")) if power_on else \
        "Heure d'allumage non renseignée : heures comptées depuis l'allumage de la radio."
    notes = "".join("<p>%s</p>" % e(x) for x in (meta.get("notes") or "").splitlines() if x.strip())
    return """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>%(titre)s</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222;max-width:900px}
h1{font-size:22px;margin-bottom:4px} h2{font-size:16px;border-bottom:1px solid #ccc;padding-bottom:3px;margin-top:22px}
table{border-collapse:collapse;width:100%%;font-size:13px} th,td{border:1px solid #bbb;padding:3px 6px;text-align:left}
th{background:#eef1f5} .meta td{border:none;padding:1px 8px 1px 0} .small{font-size:12px;color:#555}
@media print{body{margin:10mm}}
</style></head><body>
<h1>%(titre)s</h1>
<table class="meta"><tr><td><b>Date</b></td><td>%(date)s</td></tr><tr><td><b>Lieu</b></td><td>%(lieu)s</td></tr>
<tr><td><b>Opérateur</b></td><td>%(op)s</td></tr><tr><td><b>Balise / objectif</b></td><td>%(balise)s</td></tr></table>
<h2>Résultat</h2>%(res)s%(warn)s
<div>%(svg)s</div>
<p class="small">Plan sans fond de carte (nord en haut). Les fichiers KML / GPX du même journal s'ouvrent dans
Google Earth ou une application de cartes.</p>
<h2>Relevés du mode SATER</h2>
<table><tr><th>N°</th><th>Heure</th><th>Niveau max</th><th>Chrono</th><th>Touche</th><th>Azimut</th><th>Position</th><th>Locator</th></tr>
%(rows)s</table>
<p class="small">%(on)s</p>
<h2>Stations DMR entendues</h2>
<table><tr><th>Indicatif</th><th>ID</th><th>Groupe</th><th>Passages</th><th>Dernier passage</th></tr>%(st)s</table>
<h2>Observations</h2>%(notes)s
<p class="small">Établi le %(now)s avec RT4D Programmer à partir du journal de la radio (firmware F4JCF).</p>
</body></html>""" % dict(
        titre=e(meta.get("titre") or "Compte rendu d'exercice SATER"), date=e(meta.get("date")),
        lieu=e(meta.get("lieu")), op=e(meta.get("operateur")), balise=e(meta.get("balise")),
        res=res, warn="<ul>%s</ul>" % warn if warn else "", svg=_svg(jr, sol, decl),
        rows="".join(rows) or '<tr><td colspan="8">Aucun relevé</td></tr>', on=e(on),
        st="".join(st) or '<tr><td colspan="5">Aucune station</td></tr>',
        notes=notes or "<p>—</p>", now=dt.datetime.now().strftime("%d/%m/%Y %H:%M"))
