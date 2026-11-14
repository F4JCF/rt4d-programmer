"""Stations surveillées (firmware F4JCF22+)."""
import unittest

from rt4dprog import watch as W
from rt4dprog import f4set as FS


class WatchTest(unittest.TestCase):
    def test_roundtrip_keeps_sector(self):
        cur = bytes(range(256)) * 16
        new = W.build_sector(cur, [2081349, 2089999, 2081349])
        self.assertEqual(len(new), 4096)
        self.assertEqual(W.decode(new), [2081349, 2089999])
        self.assertEqual(new[:W.OFFSET], cur[:W.OFFSET])                 # logo, réglages, journal
        self.assertEqual(new[W.OFFSET + W.SIZE:], cur[W.OFFSET + W.SIZE:])
        self.assertEqual(new[W.OFFSET:W.OFFSET + 8], b"F4W1\x02\0\0\0")
        self.assertEqual(W.decode(bytes(4096)), [])
        self.assertEqual(W.OFFSET, 0xC00)
        self.assertGreaterEqual(W.OFFSET, 0x800 + 736)                    # après le journal v2

    def test_limits(self):
        with self.assertRaises(W.WatchError):
            W.encode(list(range(1, W.MAX + 2)))
        with self.assertRaises(W.WatchError):
            W.encode([0])
        with self.assertRaises(W.WatchError):
            W.encode([0x1000000])

    def test_f4set_keeps_watch(self):
        cur = W.build_sector(bytes(4096), [2081349])
        new = FS.build_sector(cur, FS.F4Settings())
        self.assertEqual(W.decode(new), [2081349])


if __name__ == "__main__":
    unittest.main()
