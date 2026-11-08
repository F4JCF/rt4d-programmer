import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rt4dprog import layout as L
from rt4dprog import protocol, radio_ops, tones, csvio
from rt4dprog.codeplug import Channel, Codeplug, Contact, GroupList, Zone, bcd_encode
from tests.fake_radio import FakeRadio, PtyRadio


def make_spi(zone_in_b=False):
    spi = bytearray(b"\xFF" * (4096 * 1024))
    cfg = bytearray(b"\x00" * 0x1000)
    cfg[384:388] = bcd_encode(2081234)
    cfg[76:76 + 5] = b"F4JCF"
    cfg[81:92] = b"\xFF" * 11
    cfg[0xFFC:0x1000] = L.MAGIC
    spi[0x2000:0x3000] = cfg
    # canal 1 écrit « à la main » selon la carte mémoire de référence
    ch = bytearray(b"\xFF" * 48)
    ch[0] = 0x40                       # analogique
    ch[1] = 0x10                       # CC 1
    ch[2] = 0x40                       # puissance haute
    ch[3] = 0x00
    ch[4] = 0x40                       # bande étroite
    struct.pack_into("<I", ch, 5, 14550000)
    struct.pack_into("<I", ch, 9, 14550000)
    ch[0x0D:0x0F] = struct.pack("<H", 0x1000 | 885)
    ch[0x0F:0x11] = struct.pack("<H", 0x2000 | 0o23)
    struct.pack_into("<H", ch, 0x11, 0xFFFF)
    ch[0x13] = 0
    struct.pack_into("<H", ch, 0x14, 0)
    ch[0x16:0x1A] = bcd_encode(0)
    struct.pack_into("<I", ch, 0x1A, 0)
    ch[0x20:0x25] = b"S20FM"
    spi[0x4000:0x4030] = ch
    # canal 3 : DMR TS2 CC3 contact 0 groupe 1
    ch = bytearray(b"\xFF" * 48)
    ch[0] = 0x02
    ch[1] = 0x30
    ch[2] = 0x00
    ch[3] = 0x80
    ch[4] = 0x00
    struct.pack_into("<I", ch, 5, 43050000)
    struct.pack_into("<I", ch, 9, 43890000)
    ch[0x0D:0x11] = b"\x00" * 4
    struct.pack_into("<H", ch, 0x11, 0)
    ch[0x13] = 1
    struct.pack_into("<H", ch, 0x14, 0)
    ch[0x16:0x1A] = bcd_encode(0)
    struct.pack_into("<I", ch, 0x1A, 0)
    ch[0x20:0x26] = b"RELAIS"
    spi[0x4000 + 2 * 48:0x4000 + 3 * 48] = ch
    # contact 0
    ct = bytearray(b"\xFF" * 32)
    ct[0] = 0
    ct[1] = 1
    ct[2:6] = bcd_encode(208)
    ct[0x10:0x16] = b"France"
    spi[0x5C000:0x5C020] = ct
    # groupe 0
    gl = bytearray(b"\xFF" * 80)
    gl[0] = 0
    gl[1] = 1
    gl[2:4] = b"FR"
    struct.pack_into("<H", gl, 0x10, 0)
    spi[0x7C000:0x7C050] = gl
    # zones : marqueurs vierges en banque A
    for page in range(0x1C000, 0x3C000, 0x1000):
        spi[page + 0xFF8:page + 0x1000] = L.ZONE_MARKER_PRISTINE
    z = Zone(name="Locale", channels=[0, 2], scan=[True, False]).encode()
    if zone_in_b:
        spi[0x1C000 + 0x20000:0x1C000 + 0x20000 + 512] = z
        spi[0x1C000 + 0x20000 + 0xFF8:0x1C000 + 0x20000 + 0x1000] = b"\x00\x00\x00\x00DTCN"
        spi[0x1C000 + 0xFF8:0x1C000 + 0x1000] = b"\x00" * 8
    else:
        spi[0x1C000:0x1C000 + 512] = z
    return spi


@pytest.fixture
def radio(monkeypatch):
    r = FakeRadio(make_spi())
    monkeypatch.setattr(protocol.NormalLink, "open", classmethod(lambda cls, port: cls(r)))
    monkeypatch.setattr(protocol.BootLink, "open", classmethod(lambda cls, port: cls(r)))
    return r


def test_tones():
    assert tones.decode(tones.encode("88.5")) == "88.5"
    assert tones.decode(tones.encode("D023N")) == "D023N"
    assert tones.decode(tones.encode("d754i")) == "D754I"
    assert tones.encode("") == b"\x00\x00"
    assert tones.normalize("88,5") == "88.5"
    with pytest.raises(ValueError):
        tones.normalize("D089N")


def test_read_codeplug(radio):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    assert cp.is_refw
    assert cp.settings.radio_id == 2081234
    assert cp.settings.radio_name == "F4JCF"
    c1 = cp.channels[0]
    assert (c1.name, c1.analog, c1.rx, c1.narrow, c1.power_high) == ("S20FM", True, 14550000, True, True)
    assert c1.rx_tone == "88.5" and c1.tx_tone == "D023N"
    c3 = cp.channels[2]
    assert (c3.analog, c3.timeslot, c3.color_code, c3.contact, c3.group, c3.scan) == (False, 1, 3, 0, 0, False)
    assert cp.contacts[0].name == "France" and cp.contacts[0].dmr_id == 208
    assert cp.groups[0].contacts == [0]
    assert cp.zones[0].name == "Locale" and cp.zones[0].channels == [0, 2]
    assert cp.zones[0].scan == [True, False]
    assert radio.closed_sessions == 1


def test_zone_bank_b(monkeypatch):
    r = FakeRadio(make_spi(zone_in_b=True))
    monkeypatch.setattr(protocol.NormalLink, "open", classmethod(lambda cls, port: cls(r)))
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    assert cp.zones[0].name == "Locale"


def test_roundtrip_unchanged_bytes(radio):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    before = bytes(cp.image)
    after = cp.build()
    # canaux, contacts, groupes, réglages identiques octet pour octet
    for s in ("settings", "channels", "contacts", "groups"):
        off, size, _, _ = L.SECTIONS[s]
        assert before[off:off + size] == after[off:off + size], s


def test_edit_and_write(radio, tmp_path):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    cp.channels[5] = Channel(name="Réseau é", analog=True, rx=14500000, tx=14500000, rx_tone="", tx_tone="123.0")
    slot = cp.free_slot(cp.contacts, 2048)
    cp.contacts[slot] = Contact(name="TG9", ctype=1, dmr_id=9)
    cp.groups[0].contacts.append(slot)
    cp.zones[0].channels.append(5)
    cp.zones[0].scan.append(True)
    cp.settings.radio_id = 2089999
    bk = radio_ops.write_codeplug("X", cp, list(L.EDITABLE_SECTIONS), str(tmp_path), log=lambda m: None)
    assert os.path.getsize(bk) == L.FILE_SIZE
    cp2 = radio_ops.read_codeplug("X", log=lambda m: None)
    assert cp2.channels[5].name == "Reseau e"
    assert cp2.channels[5].tx_tone == "123.0"
    assert cp2.contacts[slot].dmr_id == 9
    assert cp2.groups[0].contacts == [0, slot]
    assert cp2.zones[0].channels == [0, 2, 5]
    assert cp2.settings.radio_id == 2089999
    regions = {r for r, _ in radio.writes}
    assert regions == {0x90, 0x91, 0x92, 0x93, 0x94}


def test_delete_cascade(radio):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    cp.delete_contact(0)
    assert cp.channels[2].contact is None and cp.groups[0].contacts == []
    cp.delete_channel(2)
    assert cp.zones[0].channels == [0]
    cp.delete_group(0)
    assert 0 not in cp.groups


def test_refuse_non_refw(monkeypatch, tmp_path):
    spi = make_spi()
    spi[0x2FFC:0x3000] = b"\xFF" * 4
    r = FakeRadio(spi)
    monkeypatch.setattr(protocol.NormalLink, "open", classmethod(lambda cls, port: cls(r)))
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    assert not cp.is_refw
    with pytest.raises(protocol.RadioError):
        radio_ops.write_codeplug("X", cp, ["channels"], str(tmp_path), log=lambda m: None)
    assert r.writes == []


def test_bootloader_detected(monkeypatch):
    r = FakeRadio(make_spi(), bootloader=True)
    monkeypatch.setattr(protocol.NormalLink, "open", classmethod(lambda cls, port: cls(r)))
    with pytest.raises(protocol.RadioError, match="bootloader"):
        radio_ops.read_codeplug("X", log=lambda m: None)


def test_flash_firmware(monkeypatch):
    r = FakeRadio(bootloader=True)
    monkeypatch.setattr(protocol.BootLink, "open", classmethod(lambda cls, port: cls(r)))
    fw = bytes([0x00, 0x80, 0x00, 0x20]) + os.urandom(200_000)
    protocol.flash_firmware("X", fw, log=lambda m: None)
    assert bytes(r.firmware[:len(fw)]) == fw
    assert len(r.firmware) == ((len(fw) + 1023) // 1024) * 1024


def test_firmware_checks():
    assert protocol.check_firmware_file(b"MZ" + b"\0" * 100)
    assert protocol.check_firmware_file(b"\0" * 300_000)
    assert protocol.check_firmware_file(bytes([0, 0x80, 0, 0x20]) + b"\0" * 1000) is None


def test_backup_restore(radio, tmp_path):
    p = str(tmp_path / "spi.bin")
    radio_ops.backup_spi("X", p, log=lambda m: None)
    dump = open(p, "rb").read()
    assert dump == bytes(radio.spi)
    radio.writes.clear()
    radio_ops.restore_spi("X", dump, include_calibration=False, log=lambda m: None)
    assert 0x40 not in {r for r, _ in radio.writes}
    assert bytes(radio.spi) == dump


def test_file_roundtrip_and_csv(radio, tmp_path):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    f = str(tmp_path / "a.4rdmf")
    cp.save(f)
    cp2 = Codeplug.load(f)
    assert cp2.channels[0].name == "S20FM"
    csv_path = str(tmp_path / "c.csv")
    csvio.export_channels(cp2, csv_path)
    cp3 = Codeplug.load(f)
    n, warn = csvio.import_channels(cp3, csv_path, replace=True)
    assert n == 2 and not warn, warn
    assert cp3.channels[2].contact == 0 and cp3.channels[2].timeslot == 1
    assert cp3.channels[0].rx_tone == "88.5"
    ct_path = str(tmp_path / "k.csv")
    csvio.export_contacts(cp2, ct_path)
    n, warn = csvio.import_contacts(cp3, ct_path, replace=False)
    assert n == 1


def test_real_serial_over_pty():
    r = FakeRadio(make_spi())
    pty = PtyRadio(r)
    pty.start()
    try:
        assert "REFW" in radio_ops.identify(pty.port, log=lambda m: None)
        cp = radio_ops.read_codeplug(pty.port, log=lambda m: None)
        assert cp.channels[0].name == "S20FM"
    finally:
        pty.stop = True


def test_flash_over_pty():
    r = FakeRadio(bootloader=True)
    pty = PtyRadio(r)
    pty.start()
    try:
        fw = bytes([0x00, 0x80, 0x00, 0x20]) + os.urandom(50_000)
        protocol.flash_firmware(pty.port, fw, log=lambda m: None)
        assert bytes(r.firmware[:len(fw)]) == fw
    finally:
        pty.stop = True
