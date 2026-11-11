import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rt4dprog import fw_library as FL
from rt4dprog.protocol import FIRMWARE_MAX_SIZE, RadioError


def image(reset=0x08002A01, size=FIRMWARE_MAX_SIZE, tag=b"F4JCF15"):
    d = bytearray(size)
    d[0:4] = (0x2000AE48).to_bytes(4, "little")
    d[4:8] = reset.to_bytes(4, "little")
    d[0x9000:0x9000 + len(tag)] = tag
    return bytes(d)


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_f4jcf_image(tmp_path):
    inf = FL.inspect(write(tmp_path, "fw.bin", image()))
    assert inf.kind == "f4jcf" and "F4JCF15" in inf.label and not inf.blocking
    assert inf.image == image() and len(inf.sha256) == 64


def test_known_hash(tmp_path, monkeypatch):
    data = image(tag=b"xx")
    monkeypatch.setitem(FL.KNOWN, FL.sha256(data), ("F4JCF99 (essai)", "contenu"))
    inf = FL.inspect(write(tmp_path, "a.bin", data))
    assert inf.kind == "f4jcf" and inf.label == "Firmware F4JCF99 (essai)" and not inf.warnings


def test_refusals(tmp_path):
    assert FL.inspect(write(tmp_path, "t.bin", image(size=200_000))).blocking   # F4JCF incomplet
    assert FL.inspect(write(tmp_path, "r.bin", image(reset=0x08000101))).blocking   # bootloader
    assert FL.inspect(write(tmp_path, "e.bin", image(reset=0x08002A00))).blocking   # pas Thumb
    other = FL.inspect(write(tmp_path, "refw.bin", image(size=150_000, tag=b"")))
    assert other.kind == "autre" and not other.blocking and other.warnings
    with pytest.raises(RadioError):
        FL.inspect(write(tmp_path, "x.bin", b"MZ" + b"\0" * 100))


def test_library_and_journal(tmp_path):
    src = write(tmp_path, "F4JCF15.bin", image())
    lib = str(tmp_path / "bk" / "firmwares")
    dest = FL.add_to_library(src, lib)
    assert FL.list_library(lib) == [dest]
    assert FL.add_to_library(src, lib) == dest                 # même fichier : accepté
    other = tmp_path / "o"
    other.mkdir()
    with pytest.raises(RadioError):
        FL.add_to_library(write(other, "F4JCF15.bin", image(tag=b"F4JCF16")), lib)
    inf = FL.inspect(dest)
    j = FL.journal(str(tmp_path / "bk"), inf, "COM3", "flash réussi")
    assert inf.sha256 in open(j, encoding="utf-8").read()
    assert FL.last_full_backup(str(tmp_path / "bk")) is None
    write(tmp_path / "bk", "RT4D-flash-complete-2026-11-11.bin", b"x")
    p, age = FL.last_full_backup(str(tmp_path / "bk"))
    assert p.endswith("2026-11-11.bin") and age < 1
