"""Calculs de la triangulation SATER (rt4dprog.geo)."""
import math

from rt4dprog import geo


def _az(frm, to):
    x, y = geo.Plane(*frm).xy(*to)
    return math.degrees(math.atan2(x, y)) % 360


def test_positions():
    assert geo.parse_position("43.6012 E07.0034") == (43.6012, 7.0034)
    assert geo.parse_position("43.6012 O01.2500") == (43.6012, -1.25)
    assert geo.parse_position("43.6012, 7.0034") == (43.6012, 7.0034)
    lat, lon = geo.parse_position("JN33LP")
    assert abs(lat - 43.6458) < 1e-3 and abs(lon - 6.9583) < 1e-3
    assert geo.latlon_to_locator(lat, lon).startswith("JN33LP")


def test_croisement_trois_releves():
    cible = (43.62, 7.03)
    st = [(a, b, _az((a, b), cible)) for a, b in [(43.60, 7.00), (43.65, 7.01), (43.60, 7.07)]]
    s = geo.solve(st)
    assert s.ok and geo.distance_m((s.lat, s.lon), cible) < 5 and not s.warnings


def test_azimut_arrondi_et_declinaison():
    cible = (43.62, 7.03)
    st = [(a, b, round(_az((a, b), cible)) - 3) for a, b in [(43.60, 7.00), (43.65, 7.01)]]
    s = geo.solve(st, declination=3.0)
    assert s.ok and geo.distance_m((s.lat, s.lon), cible) < 150


def test_paralleles_et_derriere():
    assert not geo.solve([(43.6, 7.0, 90), (43.61, 7.0, 90)]).ok
    s = geo.solve([(43.6, 7.0, 270), (43.64, 7.0, 135)])
    assert any("derrière" in w for w in s.warnings)


def test_kml():
    st = [(43.6, 7.0, 45), (43.64, 7.0, 135)]
    k = geo.kml(st, geo.solve(st), 0.0)
    assert k.count("<Placemark>") == 5 and "Balise estimée" in k
