"""Calibration : modèle, garde-fous et écriture avec la radio simulée."""
import os

import pytest

from rt4dprog import calibration as C
from rt4dprog import protocol, radio_ops
from rt4dprog.protocol import RadioError
from tests.fake_radio import FakeRadio
from tests.test_core import make_stock_spi


def make_cal() -> bytes:
    d = bytearray(b"\xFF" * C.CAL_SIZE)
    d[0:16] = bytes([0x9A, 0, 0x37, 0xA0, 0x38, 0x6B, 0x40, 0xAB, 5, 0, 5, 0, 10, 0, 5, 0])
    for b in range(11):
        o = C.BAND_BASE + b * C.BAND_SIZE
        d[o:o + 16] = bytes([b, 0x80]) + bytes(range(14))
        for key, off, _, _, _, _ in C.ROWS:
            v = {"pa_high": 0x37, "pa_low": 0x19, "sq_w_noise": 0x28, "sq_w_rssi": 0x50,
                 "sq_n_noise": 0x23, "sq_n_rssi": 0x4B}[key]
            d[o + off:o + off + 16] = bytes([v] * 16)
    return bytes(d)


@pytest.fixture
def cal_radio(monkeypatch):
    spi = make_stock_spi()
    cal = make_cal()
    spi[0:0x1000] = cal
    spi[0x1000:0x2000] = cal
    r = FakeRadio(spi, stock=True)
    monkeypatch.setattr(protocol.NormalLink, "open", classmethod(lambda cls, port: cls(r)))
    return r


def test_points():
    assert [m for _, m in C.points(0)] == [135, 140, 145, 150, 155, 160, 165, 170]
    assert len(C.points(1)) == 16 and C.points(1)[-1] == (15, 475)
    assert C.point_for(145.5, 0) == 2 and C.point_for(439.0, 1) == 7 and C.point_for(999, 1) == 15


def test_model_limits():
    cal = C.Calibration(make_cal())
    cal.set(0, "pa_high", 2, 0x37 + 8)
    with pytest.raises(C.CalibrationError):
        cal.set(0, "pa_high", 2, 0x37 + 9)          # écart trop grand
    with pytest.raises(C.CalibrationError):
        cal.set(1, "pa_low", 0, 70)                 # hors plage
    with pytest.raises(C.CalibrationError):
        cal.set(2, "pa_high", 0, 0x37)              # bande non modifiable
    assert cal.changes() == [(0, "pa_high", 2, 0x37, 0x3F)]
    out = cal.build()
    assert sum(a != b for a, b in zip(out, make_cal())) == 1
    cal.data[5] ^= 1                                 # octet de l'en-tête : refusé
    with pytest.raises(C.CalibrationError):
        cal.build()
    cal.reset()
    assert cal.build() == make_cal()


def test_check_and_phrase():
    assert C.check(b"\xFF" * C.CAL_SIZE)
    assert C.check(make_cal()) is None
    assert C.check_operator("F4") and C.check_operator("F4JCF") is None
    assert C.confirm_phrase(" f4jcf ") == "F4JCF CALIBRATION"


def test_read_write(cal_radio, tmp_path):
    cal0, copy = radio_ops.read_calibration("X", log=lambda m: None)
    assert cal0 == make_cal() and copy == make_cal() and not cal_radio.writes
    cal = C.Calibration(cal0)
    cal.set(1, "sq_w_rssi", 7, 0x50 - 4)
    new = cal.build()
    bk = radio_ops.write_calibration("X", new, cal0, "F4JCF", str(tmp_path), log=lambda m: None,
                                     changes=cal.changes())
    assert bytes(cal_radio.spi[0:0x1000]) == new
    assert {r for r, _ in cal_radio.writes} == {0x40} and len(cal_radio.writes) == 4
    assert open(bk, "rb").read() == make_cal() * 2              # calibration + copie de secours
    assert C.load_file(bk) == make_cal()
    j = open(os.path.join(tmp_path, "journal-calibration.txt"), encoding="utf-8").read()
    assert "F4JCF" in j and "point 7" in j and "80 -> 76" in j
    # calibration de la radio différente de celle lue : refus, rien d'écrit
    n = len(cal_radio.writes)
    with pytest.raises(RadioError):
        radio_ops.write_calibration("X", make_cal(), cal0, "F4JCF", str(tmp_path), log=lambda m: None)
    assert len(cal_radio.writes) == n
    # restauration de l'origine
    radio_ops.write_calibration("X", make_cal(), new, "F4JCF", str(tmp_path), log=lambda m: None)
    assert bytes(cal_radio.spi[0:0x1000]) == make_cal()


def test_write_refuses_bad(cal_radio, tmp_path):
    with pytest.raises(RadioError):
        radio_ops.write_calibration("X", b"\xFF" * 4096, make_cal(), "F4JCF", str(tmp_path),
                                    log=lambda m: None)
    with pytest.raises(RadioError):
        radio_ops.write_calibration("X", make_cal(), make_cal(), "", str(tmp_path), log=lambda m: None)
    assert not cal_radio.writes


def test_radio_menu_fields(tmp_path):
    from rt4dprog import stock as S
    from rt4dprog.radio_menu import RADIO_FIELDS
    from tests.test_core import make_stock_spi
    keys = [(f[3], f[4]) for f in RADIO_FIELDS]
    assert len(keys) == len(set(keys)) and len(RADIO_FIELDS) >= 40
    startup = {(a, o) for _, a, o, *_ in S.STARTUP_FIELDS}
    assert not startup & set(keys)
    spi = make_stock_spi()
    img = bytes(spi[:S.IMAGE_SIZE])
    cp = S.StockCodeplug(img)
    cp.radio[("cfg", 258)] = 7                       # niveau squelch analogique
    cp.radio[("cfg2", 5)] = 9                        # pas de fréquence 5 kHz
    out = cp.build()
    assert out[S.A_CFG + 258] == 7 and out[S.A_CFG2 + 5] == 9
    assert sum(a != b for a, b in zip(out, cp.build())) == 0
    cp2 = S.StockCodeplug(out)
    assert cp2.radio[("cfg", 258)] == 7 and cp2.radio[("cfg2", 5)] == 9
