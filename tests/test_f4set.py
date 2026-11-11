import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rt4dprog import f4set as FS
from rt4dprog import protocol, radio_ops
from rt4dprog.protocol import RadioError
from tests.fake_radio import FakeRadio
from tests.test_core import make_stock_spi


def test_encode_decode():
    s = FS.F4Settings(sater_ch=[3, 1024, 0, 309], bal_cycle=45, bal_long=7, bal_num=2,
                      bal_tone=1200, rappel_min=1, bal_call="F1ABC")
    b = s.encode()
    assert len(b) == 48 and b[:4] == b"F4S1"
    # même octets que le bloc vérifié dans l'émulateur avec F4JCF15 (somme sur les octets 8 à 47)
    assert int.from_bytes(b[6:8], "little") == sum(b[8:]) & 0xFFFF
    assert FS.decode(b) == (s, "ok")
    assert FS.decode(b"\0" * 48)[1] == "vide" and FS.decode(b"\xFF" * 48)[1] == "vide"
    bad = bytearray(b); bad[20] ^= 1
    assert FS.decode(bytes(bad)) == (FS.F4Settings(), "abîmé")


def test_checks():
    assert not FS.F4Settings().check()
    for kw in (dict(sater_ch=[0, 1, 2, 3]), dict(sater_ch=[1, 1025, 0, 0]), dict(bal_cycle=5),
               dict(bal_long=21), dict(bal_num=6), dict(bal_tone=200), dict(rappel_min=61),
               dict(bal_call="f4jcf"), dict(bal_call="F4JCF-12"), dict(bal_call="ABCDEFGHI")):
        assert FS.F4Settings(**kw).check(), kw
        with pytest.raises(ValueError):
            FS.F4Settings(**kw).encode()


def test_build_sector_keeps_logo():
    sec = bytes(range(256)) * 16
    out = FS.build_sector(sec, FS.F4Settings(rappel_min=0))
    assert out[:0x400] == sec[:0x400] and out[0x800:] == sec[0x800:]
    assert FS.decode(out[0x400:0x430])[0].rappel_min == 0
    assert out[0x430:0x800] == bytes(0x3D0)


@pytest.fixture
def radio(monkeypatch):
    spi = make_stock_spi()
    spi[0x100000:0x100400] = bytes(range(256)) * 4          # logo
    spi[0x100400:0x101000] = bytes(0xC00)
    r = FakeRadio(spi, stock=True)
    monkeypatch.setattr(protocol.NormalLink, "open", classmethod(lambda cls, port: cls(r)))
    return r


def test_read_write(radio, tmp_path):
    sector = radio_ops.read_f4set("X", log=lambda *a: None)
    assert FS.decode(sector[0x400:0x430])[1] == "vide"
    s = FS.F4Settings(sater_ch=[3, 1024, 0, 309], bal_call="F4JCF")
    bk = radio_ops.write_f4set("X", s, sector, str(tmp_path), log=lambda *a: None)
    assert open(bk, "rb").read() == sector
    assert bytes(radio.spi[0x100000:0x100400]) == bytes(range(256)) * 4
    assert FS.decode(bytes(radio.spi[0x100400:0x100430])) == (s, "ok")
    assert {w[0] for w in radio.writes} == {0x9A}
    # secteur modifié entre la lecture et l'écriture : refus, rien d'écrit
    radio.writes.clear()
    with pytest.raises(RadioError):
        radio_ops.write_f4set("X", FS.F4Settings(), sector, str(tmp_path), log=lambda *a: None)
    assert not radio.writes
