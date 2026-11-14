"""Journal F4JCF (carnet SATER et derniers DMR, firmware F4JCF19+)."""
import os
import tempfile
import unittest

from rt4dprog import journal as J
from rt4dprog import geo

REL = [(170, 75, 1, 0, 123, 436012, 70034), (150, 200, 2, 1, 0xFFFF, 0, 0), (140, 10, 5, 1, 45, 480000, 15000)]
DMR = [(2081349, 0x80000000, 3, 20817, b"F6DAV\0\0\0"), (2681012, 0x80000000, 1, 0, b"\0" * 8)]


class JournalTest(unittest.TestCase):
    def test_parse(self):
        raw = J.build(REL, DMR)
        self.assertEqual(len(raw), J.SIZE)
        for data in (raw, bytes(J.OFFSET) + raw + bytes(4096 - J.OFFSET - J.SIZE),
                     bytes(J.SECTOR_ADDR + J.OFFSET) + raw + bytes(0x300000)):
            jr = J.parse(data)
            self.assertEqual(len(jr.releves), 3)
            r = jr.releves[0]
            self.assertEqual((r.dbm, r.chrono, r.touche, r.azimut), (-75, "1:15", 1, 123))
            self.assertEqual(r.position, "43.6012 E07.0034")
            self.assertEqual(geo.parse_position(r.position), (43.6012, 7.0034))
            self.assertIsNone(jr.releves[1].azimut)
            self.assertEqual(jr.releves[1].position, "")
            self.assertEqual(jr.releves[2].position, "48.0000 O01.5000")
            self.assertEqual([(s.indicatif, s.id, s.appel, s.passages) for s in jr.stations],
                             [("F6DAV", 2081349, "TG 20817", 3), ("", 2681012, "privé", 1)])

    def test_errors(self):
        raw = bytearray(J.build(REL, DMR))
        with self.assertRaises(J.JournalError):
            J.parse(bytes(4096))                         # pas de journal
        raw[20] ^= 1
        with self.assertRaises(J.JournalError):
            J.parse(bytes(raw))                          # somme fausse
        with self.assertRaises(J.JournalError):
            J.parse(b"\0" * 1000)                        # taille inconnue

    def test_empty_and_csv(self):
        jr = J.parse(J.build([], []))
        self.assertEqual((jr.releves, jr.stations), ([], []))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "j.csv")
            J.save_csv(J.parse(J.build(REL, DMR)), p)
            txt = open(p, encoding="utf-8-sig").read()
            self.assertIn("43.6012 E07.0034", txt)
            self.assertIn("F6DAV;2081349;TG 20817;3", txt)


if __name__ == "__main__":
    unittest.main()
