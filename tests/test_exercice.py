"""Exports du journal : KML, GPX, compte rendu (idées 36 et 37), heures (35)."""
import datetime as dt
import unittest
import xml.dom.minidom

from rt4dprog import exercice as X
from rt4dprog import journal as J

# deux relevés croisés autour de Mougins, un sans azimut
REL = [(170, 75, 1, 0, 45, 435800, 69800), (160, 300, 1, 0, 315, 435800, 70400), (150, 400, 2, 0, 0xFFFF, 436000, 70000)]
DMR = [(2081349, 4000, 3, 20817, b"F6DAV\0\0\0"), (2681012, 0x80000000 | 120, 1, 208, b"\0" * 8)]


class ExerciceTest(unittest.TestCase):
    def setUp(self):
        self.jr = J.parse(J.build(REL, DMR, stamps=[3600, 3700, 0x80000000 | 50], up_save=4200))
        self.on = dt.datetime(2026, 11, 14, 9, 0, 0)

    def test_times(self):
        jr = self.jr
        self.assertEqual((jr.version, jr.up_save), (2, 4200))
        r = jr.releves
        self.assertEqual((r[0].t, r[2].t, r[2].t_prev), (3600, None, 50))
        self.assertEqual(J.when(jr, r[0].t, r[0].t_prev), "+1:00:00")
        self.assertEqual(J.when(jr, r[1].t, r[1].t_prev, self.on), "10:01:40")
        self.assertEqual(J.when(jr, r[2].t, r[2].t_prev, self.on), "session précédente")
        self.assertEqual(J.when(jr, jr.stations[1].t, jr.stations[1].t_prev), "session précédente")
        # version 1 : pas d'heures
        j1 = J.parse(J.build(REL, DMR))
        self.assertEqual((j1.version, j1.releves[0].t, J.when(j1, None, None)), (1, None, ""))

    def test_solve_kml_gpx(self):
        sol = X.solve(self.jr, 0.0)
        self.assertTrue(sol.ok)
        self.assertGreater(sol.lat, 43.58)
        self.assertGreater(sol.lon, 7.0)
        kml = X.to_kml(self.jr, 0.0, self.on)
        xml.dom.minidom.parseString(kml.encode())
        self.assertIn("Balise estimée", kml)
        self.assertEqual(kml.count("<LineString>"), 2)
        gpx = X.to_gpx(self.jr, 0.0, self.on)
        doc = xml.dom.minidom.parseString(gpx.encode())
        self.assertEqual(len(doc.getElementsByTagName("wpt")), 4)
        self.assertEqual(len(doc.getElementsByTagName("rte")), 2)
        self.assertIn("<time>", gpx)

    def test_report(self):
        h = X.report(self.jr, {"lieu": "Mougins", "operateur": "F4JCF", "notes": "Essai\nBalise <test>"},
                     2.0, self.on)
        for s in ("Mougins", "F4JCF", "F6DAV", "<svg", "Position estimée", "Balise &lt;test&gt;", "10:00:00"):
            self.assertIn(s, h)
        empty = X.report(J.parse(J.build([], [], stamps=[], up_save=10)), {})
        self.assertIn("Pas de position estimée", empty)


if __name__ == "__main__":
    unittest.main()
