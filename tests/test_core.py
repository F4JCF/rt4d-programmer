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


# ───────────────────────── firmware officiel Radtel V3.18+ ─────────────────────────

from rt4dprog import stock as S


def make_stock_spi():
    spi = bytearray(b"\xFF" * (4096 * 1024))
    cfg = bytearray(b"\xFF" * 0x1000)
    cfg[12:14] = b"\xCD\xAB"
    cfg[16:28] = bytes([1, 0, 0, 1, 1, 0, 0, 3, 0, 0x28, 0, 0])
    cfg[76:81] = b"F4JCF"
    cfg[384:388] = bcd_encode(2080845)
    spi[0x2000:0x3000] = cfg
    spi[0x3000:0x4000] = cfg
    ch = bytearray(b"\x00" * 48)
    ch[0] = 0x40
    ch[1] = 0x10
    ch[2] = 0x40
    ch[4] = 0x40
    struct.pack_into("<I", ch, 5, 14550000)
    struct.pack_into("<I", ch, 9, 14550000)
    ch[0x16] = 1
    ch[0x20:0x30] = b"S20" + b"\xFF" * 13
    spi[0x4000:0x4030] = ch
    d = bytearray(ch)
    d[0] = 0x02
    struct.pack_into("<I", d, 5, 43050000)
    struct.pack_into("<I", d, 9, 43890000)
    struct.pack_into("<H", d, 0x11, 2)
    d[0x13] = 1
    d[0x20:0x30] = b"RELAIS" + b"\xFF" * 10
    spi[0x4030:0x4060] = d
    spi[0x10000:0x1C000] = spi[0x4000:0x10000]
    cts = [(2, 0xAAAAAAAA, "All Call"), (1, 91, "TG Monde"), (1, 208, "TG France")]
    for i, (t, idv, name) in enumerate(cts):
        r = bytearray(b"\xFF" * 21)
        r[0] = t
        r[1:5] = b"\xAA" * 4 if i == 0 else bcd_encode(idv)
        r[5:5 + len(name)] = name.encode()
        spi[0x5E000 + 21 * i:0x5E000 + 21 * (i + 1)] = r
    for i in range(250):
        r = bytearray(b"\xFF" * 80)
        nm = (" RxGrp France" if i == 0 else f"TG List-{i + 1:03d}").encode()
        r[:len(nm)] = nm
        if i == 0:
            struct.pack_into("<HH", r, 16, 2, 1)
        spi[0xC6000 + 80 * i:0xC6000 + 80 * (i + 1)] = r
    for i in range(250):
        r = bytearray(b"\xFF" * 520)
        struct.pack_into("<HH", r, 0, 0, 0)
        nm = ("Locale" if i == 0 else f"Zone-{i + 1:03d}").encode()
        r[4:4 + len(nm)] = nm
        if i == 0:
            struct.pack_into("<HH", r, 20, 0, 1)
        spi[0x1E000 + 520 * i:0x1E000 + 520 * (i + 1)] = r
    return spi


@pytest.fixture
def stock_radio(monkeypatch):
    r = FakeRadio(make_stock_spi(), stock=True)
    monkeypatch.setattr(protocol.NormalLink, "open", classmethod(lambda cls, port: cls(r)))
    return r


def test_stock_read(stock_radio):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    assert cp.kind == "stock"
    assert cp.settings.radio_id == 2080845 and cp.settings.radio_name == "F4JCF"
    assert cp.channels[1].name == "RELAIS" and cp.channels[1].contact == 2 and cp.channels[1].group == 0
    assert [c.name for c in cp.contacts.values()] == ["All Call", "TG Monde", "TG France"]
    assert cp.groups[0].name == "RxGrp France" and cp.groups[0].contacts == [2, 1]
    assert list(cp.zones) == [0] and cp.zones[0].channels == [0, 1]
    assert cp.build() == bytes(cp.image)


def test_stock_write(stock_radio, tmp_path):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    cp.channels[2] = Channel(name="NOUVEAU", analog=True, rx=14500000, tx=14500000)
    cp.contacts[3] = Contact(name="TG 20806", ctype=1, dmr_id=20806)
    cp.delete_contact(1)                       # compactage : 2→1, 3→2
    cp.groups[0].contacts.append(3)
    cp.zones[5] = Zone(name="Nouvelle", channels=[2, 0])
    cp.settings.radio_name = "F4JCF-2"
    bk = radio_ops.write_codeplug("X", cp, [], str(tmp_path), log=lambda m: None)
    assert bk.endswith(".rt4d") and os.path.getsize(bk) == S.IMAGE_SIZE
    regions = [r for r, _ in stock_radio.writes]
    assert sorted(set(regions)) == [0x90, 0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98]
    assert len(regions) == 426
    cp2 = radio_ops.read_codeplug("X", log=lambda m: None)
    assert [c.name for c in cp2.contacts.values()] == ["All Call", "TG France", "TG 20806"]
    assert cp2.channels[1].contact == 1                     # suivi du compactage
    assert cp2.groups[0].contacts == [1, 2]
    assert cp2.zones[5].name == "Nouvelle" and cp2.zones[5].channels == [2, 0]
    assert cp2.channels[2].name == "NOUVEAU"
    assert cp2.settings.radio_name == "F4JCF-2"
    # zone inutilisée réécrite avec son nom par défaut
    assert stock_radio.spi[0x1E000 + 520 * 7 + 4:0x1E000 + 520 * 7 + 12] == b"Zone-008"


def test_stock_file_and_backup_open(stock_radio, tmp_path):
    p = str(tmp_path / "full.bin")
    radio_ops.backup_spi("X", p, log=lambda m: None)
    cp = radio_ops.load_any(p)
    assert cp.kind == "stock" and cp.channels[0].name == "S20"
    f = str(tmp_path / "a.rt4d")
    cp.save(f)
    assert radio_ops.load_any(f).zones[0].name == "Locale"


REAL = "/home/claude/dump/spi.bin"


@pytest.mark.skipif(not os.path.exists(REAL), reason="sauvegarde réelle absente")
def test_real_backup_roundtrip():
    data = open(REAL, "rb").read()
    cp = S.StockCodeplug(data)
    assert len(cp.channels) == 460 and len(cp.zones) == 11
    assert cp.build() == data[:S.IMAGE_SIZE]


# ───────────────────────── carnet d'adresses ─────────────────────────

from rt4dprog import addressbook as AB

RADIOID_SAMPLE = """RADIO_ID,CALLSIGN,FIRST_NAME,LAST_NAME,CITY,STATE,COUNTRY
2080845,F4JCF,Jean-Marie,Bouthors,Mougins,Provence-Alpes-Côte d'Azur,France
2220001,IZ1ABC,Mario,Rossi,"Sanremo, IM",Liguria,Italy
abc,BAD,x,y,z,w,France
2080001,F1ABC,Élodie,,Nice,PACA,France
"""


def test_addressbook_parse_and_payload(stock_radio):
    entries = AB.parse_csv_text(RADIOID_SAMPLE)
    assert len(entries) == 3
    assert entries[0].name == "Jean-Marie Bouthors" and entries[0].state == "Provence-Alpes-Cote d'Azur"
    assert entries[1].city == "Sanremo IM"
    assert dict(AB.countries(entries))["France"] == 2
    fr = [e for e in entries if e.country == "France"]
    payload = AB.build_payload(fr)
    body = b"2080001,F1ABC,Elodie,Nice,PACA,France\n2080845,F4JCF,Jean-Marie Bouthors,Mougins,Provence-Alpes-Cote d'Azur,France\n"
    assert payload == (len(body) + 4).to_bytes(4, "big") + body
    AB.write_addressbook("X", payload, log=lambda m: None)
    assert bytes(stock_radio.addressbook[:len(payload)]) == payload
    assert set(stock_radio.addressbook[len(payload):]) == {0xFF}
    assert stock_radio.closed_sessions == 1


def test_addressbook_radtel_format():
    txt = "No,Radio ID,Callsign,Name,City,State,Country,Remarks\n1,2080845,F4JCF,Jean Marie,Mougins,PACA,France,\n"
    e = AB.parse_csv_text(txt)
    assert e[0].dmr_id == 2080845 and e[0].name == "Jean Marie"


# ───────────────────────── mise à jour (méthode CAT Pilot) ─────────────────────────

from rt4dprog import updater


def test_versions():
    assert updater.parse_version("v0.10.0") > updater.parse_version("0.9.9")
    assert updater.is_newer("v1.0", "0.3.0") and not updater.is_newer("v0.3.0", "0.3.0")


def _serve(tmp_path, content):
    import http.server, threading, functools
    (tmp_path / "f.bin").write_bytes(content)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/f.bin"


def test_update_download_sha256(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(updater, "_urlopen", lambda req, timeout: updater.urllib.request.urlopen(req, timeout=timeout))
    exe = b"\x7fELF" + b"x" * 5000 if not sys.platform.startswith("win") else b"MZ" + b"x" * 5000
    srv, url = _serve(tmp_path, exe)
    try:
        u = updater.Updater()
        ok = u._download_once({"version": "9.9.9", "url": url, "size": len(exe),
                               "sha256": hashlib.sha256(exe).hexdigest()})
        assert open(ok, "rb").read() == exe
        with pytest.raises(RuntimeError, match="empreinte"):
            u._download_once({"version": "9.9.8", "url": url, "size": len(exe), "sha256": "00" * 32})
    finally:
        srv.shutdown()


def test_install_and_restart_linux(monkeypatch, tmp_path):
    cmds = []
    monkeypatch.setattr(updater, "spawn_detached", lambda c: cmds.append(c))
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(updater.sys, "executable", str(tmp_path / "RT4D-Programmer"))
    new = tmp_path / "new"
    new.write_bytes(b"\x7fELF")
    updater.install_and_restart(str(new))
    assert cmds and "mv -f" in cmds[0][2] and str(tmp_path / "RT4D-Programmer") in cmds[0][2]


def test_stock_keys(stock_radio, tmp_path):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    assert len(cp.keys) == 16
    cp.keys[0] = 5          # touche latérale 1 courte = balayage
    cp.keys[15] = 8         # touche 9 longue = radio FM
    radio_ops.write_codeplug("X", cp, [], str(tmp_path), log=lambda m: None)
    assert stock_radio.spi[0x1C060 + 31] == 5 and stock_radio.spi[0x1C060 + 46] == 8
    assert radio_ops.read_codeplug("X", log=lambda m: None).keys[0] == 5


# ───────────────────────── firmware officiel (paquet Radtel) ─────────────────────────

import zipfile
from rt4dprog import firmware_pkg as FW


def _hexrec(addr, typ, data):
    b = bytes([len(data), (addr >> 8) & 0xFF, addr & 0xFF, typ]) + data
    return ":" + (b + bytes([(-sum(b)) & 0xFF])).hex().upper()


def fake_upgrade_exe():
    code = bytes([0x48, 0xAE, 0x00, 0x20, 0xC1, 0x2A, 0x00, 0x08]) + bytes(range(24))
    text = _hexrec(0, 4, b"\x08\x00") + _hexrec(0x2800, 0, code[:16]) + \
        _hexrec(0x2810, 0, code[16:]) + _hexrec(0, 5, b"\x08\x00\x29\xE1") + _hexrec(0, 1, b"")
    return b"MZ" + b"\x00" * 100 + text.encode("utf-16-le") + b"\x00\x00" * 4, code


def test_official_package(tmp_path, monkeypatch):
    exe, code = fake_upgrade_exe()
    z = tmp_path / "RT-4D Upgrade V3_25_260205.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("RT-4D Upgrade V3_25_SLOW_260205/RT-4D Upgrade V3_25_SLOW_260205.exe", exe)
        zf.writestr("RT-4D Upgrade V3_25_260205/RT-4D Upgrade V3_25_260205.exe", exe)
    fw = FW.load_firmware(str(z))
    assert fw.official and "V3.25 (260205)" in fw.label and fw.image[:len(code)] == code
    assert len(fw.image) == protocol.FIRMWARE_MAX_SIZE
    with pytest.raises(protocol.RadioError, match="rar"):
        FW.load_firmware(str(tmp_path / "x.rar"))
    r = FakeRadio(bootloader=True)
    monkeypatch.setattr(protocol.BootLink, "open", classmethod(lambda cls, port: cls(r)))
    protocol.flash_firmware("X", fw.image, log=lambda m: None, full_image=True)
    assert bytes(r.firmware) == fw.image and len(r.firmware) == 251904


REAL_UPG = ("/home/claude/fw_in/RT-4D 20260205 Upgrade/RT-4D 20260205 Upgrade/"
            "RT-4D Upgrade V3_25_260205.zip")


@pytest.mark.skipif(not os.path.exists(REAL_UPG), reason="paquet Radtel absent")
def test_real_radtel_package():
    fw = FW.load_firmware(REAL_UPG)
    assert fw.official and fw.used_bytes == 155740 and "V3.25" in fw.label


def test_stock_startup(stock_radio, tmp_path):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    cp.startup["tone"] = 0
    cp.startup["line"] = 5
    cp.startup["col"] = 300 % 128
    cp.startup["work_a"] = 2
    cp.settings.startup_message = "ADRASEC 06"
    radio_ops.write_codeplug("X", cp, [], str(tmp_path), log=lambda m: None)
    cp2 = radio_ops.read_codeplug("X", log=lambda m: None)
    assert cp2.startup["tone"] == 0 and cp2.startup["line"] == 5 and cp2.startup["work_a"] == 2
    assert cp2.settings.startup_message == "ADRASEC 06"
    assert stock_radio.spi[0x2000 + 23] == 5 and stock_radio.spi[0x1C060 + 20] == 2


def test_stock_freq_limits(stock_radio, tmp_path):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    assert cp.LIMITS["freq_limits"]
    assert len(cp.freq_limits) == S.LIMIT_N
    assert cp.build() == bytes(cp.image)                  # relecture sans modification : identique
    cp.freq_limits = [list(x) for x in S.HAM_PRESET]
    assert cp.validate() == []
    radio_ops.write_codeplug("X", cp, [], str(tmp_path), log=lambda m: None)
    raw = bytes(stock_radio.spi[0x2000 + 142:0x2000 + 162])
    assert raw == bytes.fromhex("011200" "9000" "019200" "ae01" "01b801" "e803" "001200" "e803")
    cp2 = radio_ops.read_codeplug("X", log=lambda m: None)
    assert cp2.freq_limits == [list(x) for x in S.HAM_PRESET]
    # les canaux du codeplug d'essai (145,500 et 438,900 MHz) restent autorisés
    assert all(S.tx_allowed(cp2.freq_limits, ch.tx) for ch in cp2.channels.values())


def test_tx_allowed_rules():
    ham = S.HAM_PRESET
    none = [S.LIMIT_UNUSED] * 4
    mhz = lambda f: round(f * 100_000)
    assert S.tx_allowed(none, mhz(145.5)) and S.tx_allowed(none, mhz(27.0))
    assert not S.tx_allowed(none, mhz(118.0))             # bande aviation, toujours refusée
    assert S.tx_allowed(ham, mhz(144.8)) and S.tx_allowed(ham, mhz(439.975))
    assert not S.tx_allowed(ham, mhz(144.0)) and not S.tx_allowed(ham, mhz(146.0))   # bornes incluses
    assert not S.tx_allowed(ham, mhz(446.00625)) and not S.tx_allowed(ham, mhz(70.0))


def test_freq_limits_validation(stock_radio):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    cp.freq_limits = [[1, 500, 400], [2, 10, 100], [0, 0, 0], [1, 18, 1000]]
    pb = cp.validate()
    assert len(pb) == 2 and "n°1" in pb[0] and "n°2" in pb[1]


def _fake_font() -> bytes:
    f = bytearray(b"\x00" * 0x2000)
    for c in range(33, 127):
        o = (c - 32) * 14
        f[o:o + 14] = bytes([0x3C, 0x00, 0x42, 0x00, 0x81, 0x01, 0x81, 0x01, 0x42, 0x00, 0x3C, 0x00, 0, 0])
    return bytes(f)


def test_fontpatch():
    from rt4dprog import fontpatch as P
    font = _fake_font()
    assert P.check_font(font) is None and not P.is_accented(font)
    acc = P.build_accented(font)
    assert P.is_accented(acc) and P.build_accented(acc) == acc
    changed = {i // 14 + 32 for i in range(len(font)) if font[i] != acc[i]}
    assert {chr(c) for c in changed} == set(P.MAP)
    assert P.to_radio("Réglages Arrêt Reçus Éclairage") == "R`glages Arr^t Re|us \\clairage"
    assert P.check_font(b"\xFF" * 0x2000)


def test_write_font(stock_radio, tmp_path):
    from rt4dprog import fontpatch as P
    stock_radio.spi[S.FONT_ADDR:S.FONT_ADDR + S.FONT_SIZE] = _fake_font()
    font = radio_ops.read_font("X", log=lambda m: None)
    acc = P.build_accented(font)
    bk = radio_ops.write_font("X", acc, str(tmp_path), log=lambda m: None)
    assert bytes(stock_radio.spi[S.FONT_ADDR:S.FONT_ADDR + S.FONT_SIZE]) == acc
    assert open(bk, "rb").read() == font
    assert {r for r, _ in stock_radio.writes} == {0xA0} and len(stock_radio.writes) == 8
    assert radio_ops.font_from_file(bk) == font
    radio_ops.write_font("X", font, str(tmp_path), log=lambda m: None)      # retour à l'origine
    assert bytes(stock_radio.spi[S.FONT_ADDR:S.FONT_ADDR + S.FONT_SIZE]) == font


def test_restore_stock_map():
    dump = bytes(make_stock_spi())
    regs = radio_ops.restore_regions_from_dump(dump, False)
    codes = [r for r, _, _ in regs]
    assert 0x40 not in codes and not {0x99, 0x9B, 0xA2} & set(codes)
    assert codes[:9] == [0x90, 0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98]
    sizes = {r: len(d) for r, _, d in regs}
    assert sizes[0x93] == 128 * 1024 and sizes[0x94] == 208 * 1024 and sizes[0xA0] == 8 * 1024
    with pytest.raises(protocol.RadioError, match="même organisation"):
        radio_ops.restore_regions_from_dump(dump, False, "refw")


def test_restore_stock_roundtrip(stock_radio):
    dump = bytearray(stock_radio.spi)
    dump[S.A_CH + 0x20:S.A_CH + 0x24] = b"ABCD"                  # nom du canal 1 modifié
    dump[S.FONT_ADDR + 100] ^= 0xFF
    radio_ops.restore_spi("X", bytes(dump), False, log=lambda m: None)
    assert bytes(stock_radio.spi[S.A_CH:S.A_CH + 48]) == bytes(dump[S.A_CH:S.A_CH + 48])
    assert stock_radio.spi[S.FONT_ADDR + 100] == dump[S.FONT_ADDR + 100]
    assert {r for r, _ in stock_radio.writes} == {r for r, *_ in S.RESTORE_REGIONS} - {0x40}


# ───────────── ancien firmware officiel (V3.14 – V3.17), lecture seule ─────────────
from rt4dprog import legacy as O

REAL_OLD = "/home/claude/dump2/spi.bin"


def make_legacy_spi() -> bytes:
    spi = bytearray(b"\xFF" * (4096 * 1024))
    cfg = bytearray(b"\xFF" * 1024)
    cfg[12:14] = b"\xCD\xAB"
    cfg[76:81] = b"F4MGJ"
    cfg[384:388] = bcd_encode(2081860)
    spi[O.A_CFG:O.A_CFG + 1024] = cfg
    # contacts : All Call, TG 208, privé
    for i, (t, did, nm) in enumerate([(2, None, b"All Call"), (1, 208, b"FRANCE TG208"),
                                      (0, 2081234, b"Gilbert")]):
        r = bytearray(b"\xFF" * 32)
        r[0], r[1] = i + 1, t
        r[2:6] = b"\xAA" * 4 if did is None else bcd_encode(did)
        r[16:16 + len(nm)] = nm
        spi[O.A_CONTACT + 32 * i:O.A_CONTACT + 32 * (i + 1)] = r
    # groupe RX n°1
    g = bytearray(b"\xFF" * 48)
    g[0], g[1] = 0, 1
    g[2:11] = b"TG FRANCE"
    struct.pack_into("<H", g, 0x10, 1)
    spi[O.A_TGLIST:O.A_TGLIST + 48] = g
    # canal numérique et canal analogique (CTCSS 74.4, AM, bande étroite)
    d = bytearray(b"\x00" * 48)
    d[0:6] = bytes([1, 1, 0, 1, 6, 0])
    struct.pack_into("<II", d, 6, 43023750, 43963750)
    d[0x0F], d[0x10], d[0x12] = 0xFF, 1, 0xFF
    struct.pack_into("<HH", d, 0x16, 1, 1)
    d[32:48] = b"Bertagne 83".ljust(16, b"\xFF")
    a = bytearray(b"\x00" * 48)
    a[0:4] = bytes([1, 1, 1, 1])
    a[4:6] = bytes([0xE8, 0x12])
    struct.pack_into("<II", a, 6, 12345000, 12345000)
    a[0x0E:0x10] = bytes([0xE8, 0x12])
    a[0x13] = 0x80
    a[32:48] = b"Pilote".ljust(16, b"\xFF")
    spi[O.A_CH:O.A_CH + 48] = d
    spi[O.A_CH + 48:O.A_CH + 96] = a
    # zones : une réelle, une vide au nom par défaut, une au-delà de 250
    for i, (nm, chans) in enumerate([(b"Reseau", [0, 1]), (b"Zone-002", [])]):
        z = bytearray(b"\xFF" * 512)
        z[0] = z[1] = len(chans)
        z[2] = z[3] = 0
        z[4:4 + len(nm)] = nm
        for k, c in enumerate(chans):
            struct.pack_into("<H", z, 0x14 + 2 * k, c)
        spi[O.A_ZONE + 512 * i:O.A_ZONE + 512 * (i + 1)] = z
    z = bytearray(b"\xFF" * 512)
    z[0:4] = bytes([1, 1, 0, 0])
    z[4:10] = b"ITALIA"
    struct.pack_into("<H", z, 0x14, 1)
    spi[O.A_ZONE + 512 * 255:O.A_ZONE + 512 * 256] = z
    return bytes(spi)


@pytest.fixture
def legacy_radio(monkeypatch):
    r = FakeRadio(make_legacy_spi(), legacy=True)
    monkeypatch.setattr(protocol.NormalLink, "open", classmethod(lambda cls, port: cls(r)))
    return r


def test_legacy_read(legacy_radio, tmp_path):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    assert cp.kind == "legacy"
    assert cp.settings.radio_id == 2081860 and cp.settings.radio_name == "F4MGJ"
    d, a = cp.channels[0], cp.channels[1]
    assert (d.name, d.analog, d.rx, d.tx, d.timeslot, d.color_code, d.power_high) == \
        ("Bertagne 83", False, 43023750, 43963750, 1, 6, True)
    assert d.contact == 1 and d.group == 0
    assert (a.name, a.analog, a.modulation, a.narrow, a.rx_tone, a.tx_tone, a.scan) == \
        ("Pilote", True, 1, True, "74.4", "74.4", False)
    assert [c.name for c in cp.contacts.values()] == ["All Call", "FRANCE TG208", "Gilbert"]
    assert cp.groups[0].contacts == [1]
    assert sorted(cp.zones) == [0, 255] and cp.zones[0].channels == [0, 1]
    assert "ancien" in radio_ops.identify("X").lower()
    # fichier enregistré puis rouvert
    p = tmp_path / "old.rt4d"
    cp.save(str(p))
    assert radio_ops.load_any(str(p)).kind == "legacy"


def test_legacy_convert_and_write(stock_radio, tmp_path):
    old = O.LegacyCodeplug(make_legacy_spi())
    cp = radio_ops.read_codeplug("X", log=lambda m: None)     # radio mise à jour (V3.18+)
    assert cp.kind == "stock"
    notes = O.convert_into(old, cp)
    assert any("ITALIA" in n for n in notes)
    radio_ops.write_codeplug("X", cp, [], str(tmp_path), log=lambda m: None)
    cp2 = radio_ops.read_codeplug("X", log=lambda m: None)
    assert cp2.settings.radio_id == 2081860 and cp2.settings.radio_name == "F4MGJ"
    assert [c.name for c in cp2.contacts.values()] == ["All Call", "FRANCE TG208", "Gilbert"]
    d = cp2.channels[0]
    assert d.name == "Bertagne 83" and d.rx == 43023750 and d.timeslot == 1 and d.color_code == 6
    assert d.contact == 1 and cp2.groups[d.group].name == "TG FRANCE"
    a = cp2.channels[1]
    assert a.analog and a.modulation == 1 and a.narrow and a.rx_tone == "74.4" and not a.scan
    names = [z.name for z in cp2.zones.values()]
    assert "Reseau" in names and "ITALIA" in names


@pytest.mark.skipif(not os.path.exists(REAL_OLD), reason="sauvegarde V3.14 absente")
def test_real_legacy_dump():
    cp = radio_ops.load_any(REAL_OLD)
    assert cp.kind == "legacy"
    assert len(cp.channels) == 477 and len(cp.contacts) == 154 and len(cp.groups) == 11
    assert len(cp.zones) == 18
    assert cp.channels[32].name == "Bertagne 83" and cp.contacts[cp.channels[32].contact].name == "VAR-Toulon"


def test_legacy_write(legacy_radio, tmp_path):
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    before = bytes(legacy_radio.spi)
    # écriture sans modification : régions identiques (sauf zones vides nommées comme le CPS)
    radio_ops.write_codeplug("X", cp, ["all"], str(tmp_path), log=lambda m: None)
    regions = [r for r, _ in legacy_radio.writes]
    assert [r for i, r in enumerate(regions) if i == 0 or regions[i - 1] != r] == \
        [0x90, 0x91, 0x92, 0x93, 0x94, 0x95, 0x97, 0x99]
    assert len(regions) == 273
    spi = legacy_radio.spi
    assert spi[O.A_CH:O.A_CH + 0xC000] == before[O.A_CH:O.A_CH + 0xC000]
    assert spi[O.A_TGLIST:O.A_TGLIST + 0x3000] == before[O.A_TGLIST:O.A_TGLIST + 0x3000]
    assert spi[O.A_CFG:O.A_CFG + 12] == before[O.A_CFG:O.A_CFG + 12]
    # modifications
    legacy_radio.writes.clear()
    cp = radio_ops.read_codeplug("X", log=lambda m: None)
    cp.channels[5] = Channel(name="NOUVEAU", analog=True, rx=14550000, tx=14550000,
                             rx_tone="88.5", narrow=True, power_high=False)
    cp.channels[6] = Channel(name="DMR NEW", analog=False, rx=43000000, tx=43000000,
                             timeslot=1, color_code=3, contact=2, group=0)
    cp.contacts[7] = Contact(name="TG 20806", ctype=1, dmr_id=20806)
    cp.delete_contact(1)                       # compactage : 2→1, 7→2
    cp.zones[3] = Zone(name="Nouvelle", channels=[5, 6, 0])
    cp.settings.radio_name = "F4MGJ-2"
    cp.settings.radio_id = 2081861
    radio_ops.write_codeplug("X", cp, ["all"], str(tmp_path), log=lambda m: None)
    cp2 = radio_ops.read_codeplug("X", log=lambda m: None)
    assert [c.name for c in cp2.contacts.values()] == ["All Call", "Gilbert", "TG 20806"]
    assert cp2.channels[0].contact == 0          # ancien contact supprimé → All Call
    n = cp2.channels[5]
    assert (n.name, n.analog, n.rx, n.rx_tone, n.narrow, n.power_high) == \
        ("NOUVEAU", True, 14550000, "88.5", True, False)
    d = cp2.channels[6]
    assert (d.timeslot, d.color_code, d.contact, d.group) == (1, 3, 1, 0)
    assert cp2.zones[3].channels == [5, 6, 0]
    assert cp2.settings.radio_name == "F4MGJ-2" and cp2.settings.radio_id == 2081861
    assert legacy_radio.spi[O.A_CONTACT + 32 * 5] == 6           # n° de contact (base 1)
    assert legacy_radio.spi[O.A_ZONE + 512 * 3:O.A_ZONE + 512 * 3 + 2] == bytes([3, 3])


@pytest.mark.skipif(not os.path.exists(REAL_OLD), reason="sauvegarde V3.14 absente")
def test_real_legacy_roundtrip():
    with open(REAL_OLD, "rb") as f:
        data = f.read()
    cp = O.LegacyCodeplug(data)
    assert cp.validate() == []
    assert cp.build()[:0xE0000] == data[:0xE0000]


def test_linux_desktop_entry(tmp_path, monkeypatch):
    from rt4dprog import linux_desktop as LD
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert LD.install("/opt/rt4d/RT4D-Programmer") is True
    assert LD.install("/opt/rt4d/RT4D-Programmer") is False       # déjà à jour
    txt = (tmp_path / "applications" / "rt4d-programmer.desktop").read_text()
    assert 'Exec="/opt/rt4d/RT4D-Programmer"' in txt
    assert (tmp_path / "icons/hicolor/256x256/apps/rt4d-programmer.png").exists()
    assert LD.install("/home/x/RT4D-Programmer") is True           # exécutable déplacé


def test_accent_names():
    from rt4dprog import codeplug as C
    buf = bytearray(b"\xFF" * 16)
    try:
        C.ACCENTS = False
        C.put_name(buf, 0, "Détresse Aéro", 16)
        assert C.get_name(buf, 0, 16) == "Detresse Aero"
        C.ACCENTS = True
        buf = bytearray(b"\xFF" * 16)
        C.put_name(buf, 0, "Détresse Aéro", 16)
        assert bytes(buf[:13]) == b"D`tresse A`ro"
        assert C.get_name(buf, 0, 16) == "Détresse Aéro"
        buf = bytearray(b"\xFF" * 16)
        C.put_name(buf, 0, "Rôle {x} Ç", 16)         # ô → o ; { réservé → _ ; Ç sans glyphe → C
        assert bytes(buf[:10]) == b"Role _x_ C"
    finally:
        C.ACCENTS = False


def test_backup_16mb(radio, tmp_path):
    """Sauvegarde de toute la mémoire (16 Mo) : lecture seule, et ses 4 premiers Mo servent partout."""
    from rt4dprog.protocol import SPI_FULL_KB
    radio.spi.extend(bytes((i * 7) & 0xFF for i in range(12 * 1024 * 1024)))   # flash 25Q128
    p = str(tmp_path / "spi16.bin")
    radio.writes.clear()
    radio_ops.backup_spi("X", p, log=lambda m: None, size_kb=SPI_FULL_KB)
    dump = open(p, "rb").read()
    assert len(dump) == 16 * 1024 * 1024 and dump == bytes(radio.spi)
    assert not radio.writes                                   # rien n'a été écrit
    assert radio_ops.dump_4mb(dump) == dump[:4 * 1024 * 1024]
    r4 = radio_ops.restore_regions_from_dump(dump[:4 * 1024 * 1024], False)
    r16 = radio_ops.restore_regions_from_dump(dump, False)
    assert r4 == r16
