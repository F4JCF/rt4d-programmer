import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rt4dprog import f4set as FS
from rt4dprog import logo as L
from rt4dprog import protocol, radio_ops
from tests.fake_radio import FakeRadio
from tests.test_core import make_stock_spi


def test_layout():
    px = [[False] * 128 for _ in range(64)]
    px[0][0] = True          # en haut à gauche -> dernière bande, bit 0
    px[63][127] = True       # en bas à droite -> 1re bande, bit 7
    px[9][5] = True
    d = L.encode(px)
    assert d[7 * 128 + 0] == 0x01 and d[127] == 0x80 and d[6 * 128 + 5] == 0x02
    assert sum(bin(b).count("1") for b in d) == 3
    assert L.decode(d) == px


def test_from_gray():
    g = [[0] * 64 + [255] * 64 for _ in range(64)]
    px = L.from_gray(g)
    assert px[10][10] and not px[10][100]
    assert L.from_gray(g, invert=True)[10][100]
    mid = [[128] * 128 for _ in range(64)]
    r = L.black_ratio(L.from_gray(mid, threshold=200, dither=True))
    assert 0.3 < r < 0.7                                  # tramage : demi-teinte


def test_write_logo_keeps_settings(monkeypatch, tmp_path):
    spi = make_stock_spi()
    s = FS.F4Settings(rappel_min=5)
    spi[0x100000:0x101000] = FS.build_sector(bytes(0x1000), s)
    r = FakeRadio(spi, stock=True)
    monkeypatch.setattr(protocol.NormalLink, "open", classmethod(lambda cls, port: cls(r)))
    sector = radio_ops.read_f4set("X", log=lambda *a: None)
    new = bytes(range(256)) * 4
    radio_ops.write_logo("X", new, sector, str(tmp_path), log=lambda *a: None)
    assert bytes(r.spi[0x100000:0x100400]) == new
    assert FS.decode(bytes(r.spi[0x100400:0x100430])) == (s, "ok")
    with pytest.raises(Exception):
        radio_ops.write_logo("X", new[:10], sector, str(tmp_path), log=lambda *a: None)
