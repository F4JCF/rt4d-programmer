"""Modèle du codeplug RT-4D (format REFW beta41+).

Principe de sécurité : l'image binaire lue dans la radio est conservée telle
quelle ; on ne réécrit que les champs connus de chaque enregistrement, les
octets inconnus sont préservés.
"""

from __future__ import annotations

import struct
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from . import layout as L
from . import tones

ANALOG_MODS = ["FM", "AM", "SSB"]
CONTACT_TYPES = ["Privé", "Groupe", "Tous"]
RX_TX = ["RX+TX", "RX seul", "TX seul"]


# ───────────────────────────── utilitaires ─────────────────────────────

def to_ascii(text: str, maxlen: int) -> bytes:
    """Nom → octets (accents retirés, tronqué)."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = "".join(c if 32 <= ord(c) < 127 else "_" for c in t)
    return t.encode("ascii")[:maxlen]


def put_name(buf: bytearray, off: int, text: str, length: int):
    if get_name(buf, off, length) == (text or "").strip():
        return          # inchangé : on conserve les octets d'origine
    b = to_ascii(text, length)
    buf[off:off + length] = b + b"\xFF" * (length - len(b))


def get_name(buf: bytes, off: int, length: int) -> str:
    raw = bytes(c for c in buf[off:off + length] if c not in (0xFF, 0x00))
    return raw.decode("gbk", "replace").strip()


def bcd_decode(b: bytes) -> int:
    if all(x == 0xFF for x in b):
        return 0
    v = 0
    for byte in reversed(b):
        hi, lo = byte >> 4, byte & 0x0F
        hi = 0 if hi == 0xF else hi
        lo = 0 if lo == 0xF else lo
        if hi > 9 or lo > 9:
            return 0
        v = v * 100 + hi * 10 + lo
    return v


def bcd_encode(v: int) -> bytes:
    out = bytearray(4)
    for i in range(4):
        lo = v % 10
        v //= 10
        hi = v % 10
        v //= 10
        out[i] = (hi << 4) | lo
    return bytes(out)


def mhz_to_raw(mhz: float) -> int:
    return int(round(mhz * 100000))


def raw_to_mhz(raw: int) -> float:
    return raw / 100000.0


def fmt_mhz(raw: int) -> str:
    return f"{raw / 100000:.5f}" if raw else ""


# ───────────────────────────── enregistrements ─────────────────────────────

@dataclass
class Channel:
    name: str = ""
    analog: bool = True
    rx: int = 0                 # unités de 10 Hz
    tx: int = 0
    power_high: bool = True
    scan: bool = True
    tot: int = 0                # index 0-63
    modulation: int = 0         # 0 FM, 1 AM, 2 SSB
    narrow: bool = False
    rx_tone: str = ""
    tx_tone: str = ""
    ctdcs_select: int = 0
    tail_tone: int = 0
    ana_busy: int = 0
    dmr_busy: int = 0
    color_code: int = 1
    timeslot: int = 0           # 0 = TS1, 1 = TS2
    contact: Optional[int] = None   # emplacement contact (0-based) ou None
    group: Optional[int] = None     # emplacement groupe RX (0-based) ou None
    encrypt: int = 0
    use_radio_id: bool = True
    dmr_id: int = 0
    rx_tx: int = 0
    monitor: int = 0
    dcdm: int = 0
    scramble: int = 0
    mute_code: int = 0
    raw: Optional[bytes] = None

    @classmethod
    def parse(cls, b: bytes) -> Optional["Channel"]:
        if all(x == 0xFF for x in b):
            return None
        rx = struct.unpack_from("<I", b, 0x05)[0]
        tx = struct.unpack_from("<I", b, 0x09)[0]
        name = get_name(b, 0x20, 16)
        rx = 0 if rx == 0xFFFFFFFF else rx
        tx = 0 if tx == 0xFFFFFFFF else tx
        if not name and not rx and not tx:
            return None
        ct = struct.unpack_from("<H", b, 0x11)[0]
        gl = b[0x13]
        return cls(
            name=name, analog=bool(b[0] & 0x40), rx=rx, tx=tx,
            power_high=bool(b[2] & 0x40), scan=not (b[3] & 0x80), tot=b[2] & 0x3F,
            modulation=min((b[4] >> 4) & 0x03, 2), narrow=bool((b[4] >> 6) & 1),
            rx_tone=tones.decode(b[0x0D:0x0F]), tx_tone=tones.decode(b[0x0F:0x11]),
            ctdcs_select=(b[4] >> 1) & 0x07, tail_tone=b[3] & 0x07,
            ana_busy=(b[3] >> 3) & 0x03, dmr_busy=(b[3] >> 5) & 0x03,
            color_code=(b[1] >> 4) & 0x0F, timeslot=(b[0] >> 1) & 1,
            contact=None if ct == 0xFFFF or ct >= L.CT_MAX else ct,
            group=None if gl in (0, 0xFF) or gl > L.GL_MAX else gl - 1,
            encrypt=struct.unpack_from("<H", b, 0x14)[0],
            use_radio_id=not ((b[0] >> 3) & 1), dmr_id=bcd_decode(b[0x16:0x1A]),
            rx_tx=(b[0] >> 4) & 0x03, monitor=b[0] & 1, dcdm=(b[0] >> 2) & 1,
            scramble=b[1] & 0x0F, mute_code=struct.unpack_from("<I", b, 0x1A)[0],
            raw=bytes(b),
        )

    def encode(self) -> bytes:
        if self.raw and not all(x == 0xFF for x in self.raw):
            d = bytearray(self.raw)
        else:
            d = bytearray(b"\xFF" * L.CH_SIZE)
            d[0:5] = b"\x00" * 5
            d[0x1A:0x1E] = b"\x00" * 4
        d[0] = (d[0] & 0x80) | (self.monitor & 1) | ((self.timeslot & 1) << 1) \
            | ((self.dcdm & 1) << 2) | (0 if self.use_radio_id else 0x08) \
            | ((self.rx_tx & 3) << 4) | (0x40 if self.analog else 0)
        d[1] = (self.scramble & 0x0F) | ((self.color_code & 0x0F) << 4)
        d[2] = (d[2] & 0x80) | (self.tot & 0x3F) | (0x40 if self.power_high else 0)
        d[3] = (self.tail_tone & 7) | ((self.ana_busy & 3) << 3) | ((self.dmr_busy & 3) << 5) \
            | (0 if self.scan else 0x80)
        d[4] = (d[4] & 0x81) | ((self.ctdcs_select & 7) << 1) | ((self.modulation & 3) << 4) \
            | (0x40 if self.narrow else 0)
        struct.pack_into("<I", d, 0x05, self.rx)
        struct.pack_into("<I", d, 0x09, self.tx)
        d[0x0D:0x0F] = tones.encode(self.rx_tone)
        d[0x0F:0x11] = tones.encode(self.tx_tone)
        struct.pack_into("<H", d, 0x11, 0xFFFF if self.contact is None else self.contact)
        d[0x13] = 0 if self.group is None else self.group + 1
        struct.pack_into("<H", d, 0x14, self.encrypt & 0xFFFF)
        d[0x16:0x1A] = bcd_encode(0 if self.use_radio_id else self.dmr_id)
        struct.pack_into("<I", d, 0x1A, self.mute_code & 0xFFFFFFFF)
        put_name(d, 0x20, self.name, 16)
        return bytes(d)


@dataclass
class Contact:
    name: str = ""
    ctype: int = 1              # 0 privé, 1 groupe, 2 tous
    dmr_id: int = 0
    raw: Optional[bytes] = None

    @classmethod
    def parse(cls, b: bytes) -> Optional["Contact"]:
        if b[1] > 2:
            return None
        return cls(name=get_name(b, 0x10, 16), ctype=b[1], dmr_id=bcd_decode(b[2:6]), raw=bytes(b))

    def encode(self) -> bytes:
        d = bytearray(self.raw) if self.raw and self.raw[1] <= 2 else bytearray(b"\xFF" * L.CT_SIZE)
        d[0] = 0
        d[1] = self.ctype & 3
        d[2:6] = bcd_encode(self.dmr_id)
        put_name(d, 0x10, self.name, 16)
        return bytes(d)


@dataclass
class GroupList:
    name: str = ""
    contacts: list = field(default_factory=list)    # emplacements de contacts
    raw: Optional[bytes] = None

    @classmethod
    def parse(cls, b: bytes) -> Optional["GroupList"]:
        if b[1] != 0x01:
            return None
        name = get_name(b, 0x02, 14)
        if not name:
            return None
        ids = []
        for i in range(L.GL_IDS):
            v = struct.unpack_from("<H", b, 0x10 + 2 * i)[0]
            if v != 0xFFFF and v < L.CT_MAX:
                ids.append(v)
        return cls(name=name, contacts=ids, raw=bytes(b))

    def encode(self) -> bytes:
        d = bytearray(self.raw) if self.raw and self.raw[1] == 1 else bytearray(b"\xFF" * L.GL_SIZE)
        d[0] = 0
        d[1] = 1
        put_name(d, 0x02, self.name, 14)
        for i in range(L.GL_IDS):
            v = self.contacts[i] if i < len(self.contacts) else 0xFFFF
            struct.pack_into("<H", d, 0x10 + 2 * i, v)
        return bytes(d)


@dataclass
class Zone:
    name: str = ""
    channels: list = field(default_factory=list)    # emplacements de canaux
    scan: list = field(default_factory=list)        # bool par canal
    cur: tuple = (0, 0)

    @classmethod
    def parse(cls, b: bytes) -> Optional["Zone"]:
        if b[0] == 0xFF:
            return None
        name = get_name(b, 0x04, 16)
        if not name:
            return None
        count = min(b[0] | (b[1] << 8), L.ZN_CH_MAX)
        chans = []
        for i in range(L.ZN_CH_MAX):
            if len(chans) >= count:
                break
            v = struct.unpack_from("<H", b, 0x14 + 2 * i)[0]
            if v != 0xFFFF and v < L.CH_MAX:
                chans.append(v)
        sb = b[L.ZN_SCAN_OFF:L.ZN_SCAN_OFF + 25]
        all_ff = all(x == 0xFF for x in sb)
        scan = [True if all_ff else bool(sb[i // 8] & (1 << (i % 8))) for i in range(len(chans))]
        return cls(name=name, channels=chans, scan=scan, cur=(b[2], b[3]))

    def encode(self) -> bytes:
        # Même construction que l'éditeur de référence : base vierge 0xFF.
        d = bytearray(b"\xFF" * L.ZN_SIZE)
        chans = self.channels[:L.ZN_CH_MAX]
        n = len(chans)
        d[0], d[1] = n & 0xFF, n >> 8
        last = max(n - 1, 0)
        d[2] = min(self.cur[0] if self.cur[0] != 0xFF else 0, last)
        d[3] = min(self.cur[1] if self.cur[1] != 0xFF else 0, last)
        put_name(d, 0x04, self.name, 16)
        for i, c in enumerate(chans):
            struct.pack_into("<H", d, 0x14 + 2 * i, c)
        d[L.ZN_SCAN_OFF:L.ZN_SCAN_OFF + 25] = b"\x00" * 25
        for i in range(n):
            if self.scan[i] if i < len(self.scan) else True:
                d[L.ZN_SCAN_OFF + i // 8] |= 1 << (i % 8)
        return bytes(d)


@dataclass
class Settings:
    radio_id: int = 0
    radio_name: str = ""
    startup_message: str = ""


# ───────────────────────────── codeplug ─────────────────────────────

class Codeplug:
    def __init__(self, image: bytes | None = None):
        if image is None:
            image = b"\xFF" * L.FILE_SIZE
        if len(image) == L.FILE_SIZE_LEGACY:
            image = bytes(image) + b"\xFF" * (L.FILE_SIZE - L.FILE_SIZE_LEGACY)
        if len(image) != L.FILE_SIZE:
            raise ValueError(f"Taille de fichier inattendue ({len(image)} octets, attendu {L.FILE_SIZE}). "
                             "Ce n'est pas un codeplug RT-4D au format .4rdmf.")
        self.image = bytearray(image)
        self.parse()

    # -- sections --
    def section(self, name: str) -> bytes:
        off, size, _, _ = L.SECTIONS[name]
        return bytes(self.image[off:off + size])

    def set_section(self, name: str, data: bytes):
        off, size, _, _ = L.SECTIONS[name]
        assert len(data) == size, (name, len(data), size)
        self.image[off:off + size] = data

    @property
    def is_refw(self) -> bool:
        return self.image[0xFFC:0x1000] == L.MAGIC

    @property
    def has_settings(self) -> bool:
        return not all(b == 0xFF for b in self.image[0:0x400])

    # -- lecture --
    def parse(self):
        img = self.image
        co = L.SECTIONS["channels"][0]
        self.channels: dict[int, Channel] = {}
        for i in range(L.CH_MAX):
            c = Channel.parse(img[co + i * L.CH_SIZE: co + (i + 1) * L.CH_SIZE])
            if c:
                self.channels[i] = c
        co = L.SECTIONS["contacts"][0]
        self.contacts: dict[int, Contact] = {}
        for i in range(L.CT_MAX):
            c = Contact.parse(img[co + i * L.CT_SIZE: co + (i + 1) * L.CT_SIZE])
            if c:
                self.contacts[i] = c
        go = L.SECTIONS["groups"][0]
        self.groups: dict[int, GroupList] = {}
        for i in range(L.GL_MAX):
            g = GroupList.parse(img[go + i * L.GL_SIZE: go + (i + 1) * L.GL_SIZE])
            if g:
                g.contacts = [c for c in g.contacts if c in self.contacts]
                self.groups[i] = g
        zo = L.SECTIONS["zones"][0]
        self.zones: dict[int, Zone] = {}
        for i in range(L.ZN_MAX):
            z = Zone.parse(img[zo + i * L.ZN_SIZE: zo + (i + 1) * L.ZN_SIZE])
            if z:
                keep = [(c, s) for c, s in zip(z.channels, z.scan) if c in self.channels]
                z.channels = [c for c, _ in keep]
                z.scan = [s for _, s in keep]
                self.zones[i] = z
        cfg = img[0:0x1000]
        self.settings = Settings(
            radio_id=bcd_decode(cfg[384:388]),
            radio_name=get_name(cfg, 76, 16),
            startup_message=get_name(cfg, 44, 32),
        )

    # -- écriture --
    def build(self) -> bytes:
        img = bytearray(self.image)
        co = L.SECTIONS["channels"][0]
        for i in range(L.CH_MAX):
            ch = self.channels.get(i)
            img[co + i * L.CH_SIZE: co + (i + 1) * L.CH_SIZE] = ch.encode() if ch else b"\xFF" * L.CH_SIZE
        co = L.SECTIONS["contacts"][0]
        for i in range(L.CT_MAX):
            c = self.contacts.get(i)
            img[co + i * L.CT_SIZE: co + (i + 1) * L.CT_SIZE] = c.encode() if c else b"\xFF" * L.CT_SIZE
        go = L.SECTIONS["groups"][0]
        for i in range(L.GL_MAX):
            g = self.groups.get(i)
            img[go + i * L.GL_SIZE: go + (i + 1) * L.GL_SIZE] = g.encode() if g else b"\xFF" * L.GL_SIZE
        zo = L.SECTIONS["zones"][0]
        for i in range(L.ZN_MAX):
            z = self.zones.get(i)
            img[zo + i * L.ZN_SIZE: zo + (i + 1) * L.ZN_SIZE] = z.encode() if z else b"\xFF" * L.ZN_SIZE
        if self.has_settings:
            s = self.settings
            img[384:388] = bcd_encode(s.radio_id)
            put_name(img, 76, s.radio_name, 16)
            put_name(img, 44, s.startup_message, 32)
        return bytes(img)

    def commit(self):
        """Applique le modèle dans l'image puis relit (normalise)."""
        self.image = bytearray(self.build())
        self.parse()

    # -- fichiers --
    @classmethod
    def load(cls, path: str) -> "Codeplug":
        with open(path, "rb") as f:
            return cls(f.read())

    def save(self, path: str):
        data = self.build()
        with open(path, "wb") as f:
            f.write(data)

    # -- gestion des références --
    @staticmethod
    def free_slot(d: dict, maxn: int) -> Optional[int]:
        for i in range(maxn):
            if i not in d:
                return i
        return None

    def delete_channel(self, slot: int):
        self.channels.pop(slot, None)
        for z in self.zones.values():
            keep = [(c, s) for c, s in zip(z.channels, z.scan) if c != slot]
            z.channels = [c for c, _ in keep]
            z.scan = [s for _, s in keep]

    def delete_contact(self, slot: int):
        self.contacts.pop(slot, None)
        for ch in self.channels.values():
            if ch.contact == slot:
                ch.contact = None
        for g in self.groups.values():
            g.contacts = [c for c in g.contacts if c != slot]

    def delete_group(self, slot: int):
        self.groups.pop(slot, None)
        for ch in self.channels.values():
            if ch.group == slot:
                ch.group = None

    def delete_zone(self, slot: int):
        self.zones.pop(slot, None)

    def validate(self) -> list[str]:
        """Contrôles avant écriture : liste de problèmes (vide = OK)."""
        pb = []
        for slot, ch in sorted(self.channels.items()):
            lab = f"Canal {slot + 1} « {ch.name} »"
            for nm, f in (("RX", ch.rx), ("TX", ch.tx)):
                if f and not (1_800_000 <= f <= 100_000_000):
                    pb.append(f"{lab} : fréquence {nm} {fmt_mhz(f)} MHz hors plage (18–1000 MHz).")
            if not ch.rx:
                pb.append(f"{lab} : fréquence RX manquante.")
            if not ch.analog:
                if not 0 <= ch.color_code <= 15:
                    pb.append(f"{lab} : code couleur invalide.")
                if ch.contact is not None and ch.contact not in self.contacts:
                    pb.append(f"{lab} : contact inexistant.")
        for slot, z in self.zones.items():
            if len(z.channels) > L.ZN_CH_MAX:
                pb.append(f"Zone « {z.name} » : plus de {L.ZN_CH_MAX} canaux.")
        for slot, c in self.contacts.items():
            if not 0 < c.dmr_id <= 16_777_215:
                pb.append(f"Contact « {c.name} » : ID DMR invalide ({c.dmr_id}).")
        return pb
