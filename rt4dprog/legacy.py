"""Firmware OFFICIEL Radtel ANCIEN (V3.17 et antérieurs, testé sur V3.14 du 2025-03-21).

Lecture seule. Carte mémoire déterminée à partir d'une sauvegarde complète d'un
RT-4D en V3.14 (mêmes adresses que la flash SPI) :

  0x002000  réglages (copie à 0x003000)
  0x004000  canaux        1024 × 48 octets  (copie à 0x010000)
  0x01C000  zones          256 × 512 octets (copie à 0x03C000)
  0x05C000  contacts      2048 × 32 octets  (copie à 0x06C000)
  0x07C000  groupes RX     256 × 48 octets  (copie à 0x07F000)

L'écriture directe n'est pas proposée tant que la séquence du CPS Radtel de
cette génération n'a pas été vérifiée. Le contenu peut en revanche être
converti vers le format V3.18+ (après mise à jour du firmware de la radio).
"""

from __future__ import annotations

import struct
from typing import Optional

from . import tones
from .codeplug import Channel, Contact, GroupList, Settings, Zone, bcd_decode, get_name

IMAGE_SIZE = 0x100000

A_CFG = 0x002000
A_CH = 0x004000
A_ZONE = 0x01C000
A_CONTACT = 0x05C000
A_TGLIST = 0x07C000

CH_SIZE, CH_MAX = 48, 1024
ZN_SIZE, ZN_MAX, ZN_CH_MAX = 512, 256, 246
CT_SIZE, CT_MAX = 32, 2048
GL_SIZE, GL_MAX, GL_IDS = 48, 256, 16

ALL_CALL = bytes([0x02, 0xAA, 0xAA, 0xAA, 0xAA])

# Blocs de 1 Ko lus dans la radio
READ_RANGES = [(A_CFG, 1), (A_CH, 48), (A_ZONE, 128), (A_CONTACT, 64), (A_TGLIST, 12)]


def looks_like_legacy(contact_head: bytes) -> bool:
    """Signature : contact n°1 « All Call » (01 02 AA AA AA AA) à 0x5C000."""
    return contact_head[0] == 0x01 and contact_head[1:6] == ALL_CALL


def parse_channel(b: bytes) -> Optional[Channel]:
    """Enregistrement canal de l'ancien firmware → modèle commun."""
    if b[2] > 1:
        return None
    rx = struct.unpack_from("<I", b, 0x06)[0]
    tx = struct.unpack_from("<I", b, 0x0A)[0]
    rx = 0 if rx == 0xFFFFFFFF else rx
    tx = 0 if tx == 0xFFFFFFFF else tx
    name = get_name(b, 0x20, 16)
    if not name and not rx and not tx:
        return None
    analog = b[2] == 1
    ch = Channel(name=name, analog=analog, rx=rx, tx=tx,
                 power_high=b[0x10] == 1, scan=not (b[0x13] & 0x80), raw=None)
    if analog:
        ch.modulation = b[0] if b[0] <= 2 else 0
        ch.narrow = b[3] == 1
        ch.rx_tone = tones.decode(b[0x04:0x06])
        ch.tx_tone = tones.decode(b[0x0E:0x10])
    else:
        ch.timeslot = b[3] & 1
        ch.color_code = b[4] & 0x0F
        gl = struct.unpack_from("<H", b, 0x16)[0]
        ch.group = None if gl in (0, 0xFFFF) else gl - 1
        ct = struct.unpack_from("<H", b, 0x18)[0]
        ch.contact = None if ct == 0xFFFF else ct
        ch.encrypt = 0
        ch.use_radio_id = True
    return ch


class LegacyCodeplug:
    kind = "legacy"
    is_refw = False
    read_only = True
    LIMITS = dict(channels=CH_MAX, contacts=CT_MAX, groups=GL_MAX, group_ids=GL_IDS,
                  zones=ZN_MAX, zone_channels=ZN_CH_MAX, zone_scan=False, contact0_fixed=True,
                  keys=False, startup=False)

    def __init__(self, image: bytes):
        image = bytes(image[:IMAGE_SIZE])
        if len(image) != IMAGE_SIZE or not looks_like_legacy(image[A_CONTACT:A_CONTACT + 6]):
            raise ValueError("Ce fichier n'est pas une image de l'ancien firmware officiel Radtel.")
        self.image = bytearray(image)
        self.parse()

    @property
    def has_settings(self) -> bool:
        return self.image[A_CFG + 12:A_CFG + 14] == b"\xCD\xAB"

    def parse(self):
        img = self.image
        self.contacts: dict[int, Contact] = {}
        for i in range(CT_MAX):
            r = img[A_CONTACT + i * CT_SIZE:A_CONTACT + (i + 1) * CT_SIZE]
            if r[1] > 2:
                continue
            dmr_id = 16777215 if i == 0 else bcd_decode(r[2:6])
            self.contacts[i] = Contact(name=get_name(r, 0x10, 16), ctype=r[1], dmr_id=dmr_id)
        self.channels: dict[int, Channel] = {}
        for i in range(CH_MAX):
            c = parse_channel(img[A_CH + i * CH_SIZE:A_CH + (i + 1) * CH_SIZE])
            if c:
                if c.contact is not None and c.contact not in self.contacts:
                    c.contact = None
                self.channels[i] = c
        self.groups: dict[int, GroupList] = {}
        for i in range(GL_MAX):
            r = img[A_TGLIST + i * GL_SIZE:A_TGLIST + (i + 1) * GL_SIZE]
            if r[1] != 0x01:
                continue
            ids = []
            for k in range(GL_IDS):
                v = struct.unpack_from("<H", r, 0x10 + 2 * k)[0]
                if v != 0xFFFF and v in self.contacts:
                    ids.append(v)
            self.groups[i] = GroupList(name=get_name(r, 2, 14) or f"Groupe {i + 1}", contacts=ids)
        for ch in self.channels.values():
            if ch.group is not None and ch.group not in self.groups:
                ch.group = None
        self.zones: dict[int, Zone] = {}
        for i in range(ZN_MAX):
            r = img[A_ZONE + i * ZN_SIZE:A_ZONE + (i + 1) * ZN_SIZE]
            if r[4] == 0xFF:
                continue
            chans = []
            for k in range(ZN_CH_MAX):
                v = struct.unpack_from("<H", r, 0x14 + 2 * k)[0]
                if v == 0xFFFF:
                    break
                if v in self.channels:
                    chans.append(v)
            name = get_name(r, 4, 16)
            if chans or (name and name != f"Zone-{i + 1:03d}"):
                self.zones[i] = Zone(name=name or f"Zone-{i + 1:03d}", channels=chans,
                                     scan=[True] * len(chans), cur=(0, 0))
        cfg = img[A_CFG:A_CFG + 0x400]
        self.settings = Settings(radio_id=bcd_decode(cfg[384:388]),
                                 radio_name=get_name(cfg, 76, 16),
                                 startup_message=get_name(cfg, 44, 32))
        self.keys = []
        self.startup = {}

    # ── fichier : on conserve l'image lue, telle quelle ──
    def build(self) -> bytes:
        return bytes(self.image)

    def commit(self):
        pass

    def save(self, path: str):
        with open(path, "wb") as f:
            f.write(bytes(self.image))

    def validate(self) -> list[str]:
        return ["Ancien firmware Radtel : écriture non disponible (lecture seule)."]

    @staticmethod
    def free_slot(d: dict, maxn: int) -> Optional[int]:
        return None

    def delete_channel(self, slot):
        pass

    delete_contact = delete_group = delete_zone = delete_channel


def convert_into(old: LegacyCodeplug, new) -> list[str]:
    """Copie canaux, contacts, groupes RX, zones et ID DMR d'un ancien codeplug
    dans un codeplug V3.18+ (StockCodeplug lu dans la radio mise à jour).

    Retourne la liste des remarques (éléments tronqués ou ignorés).
    """
    notes = []
    L = new.LIMITS
    # contacts : le n°0 (All Call) existe des deux côtés
    contacts = {0: new.contacts.get(0) or Contact(name="All Call", ctype=2, dmr_id=16777215)}
    for i, c in sorted(old.contacts.items()):
        if i == 0:
            continue
        if i >= L["contacts"]:
            notes.append(f"Contact « {c.name} » ignoré (au-delà de {L['contacts']}).")
            continue
        contacts[i] = Contact(name=c.name, ctype=c.ctype, dmr_id=c.dmr_id)
    new.contacts = contacts

    groups = {}
    for i, g in sorted(old.groups.items()):
        if i >= L["groups"]:
            notes.append(f"Groupe RX « {g.name} » ignoré (au-delà de {L['groups']}).")
            continue
        groups[i] = GroupList(name=g.name, contacts=[c for c in g.contacts if c in contacts])
    new.groups = groups

    channels = {}
    for i, ch in sorted(old.channels.items()):
        c = Channel(**{k: getattr(ch, k) for k in ch.__dataclass_fields__ if k != "raw"})
        if c.contact is not None and c.contact not in contacts:
            c.contact = 0
        if c.group is not None and c.group not in groups:
            c.group = None
        if not c.analog and c.contact is None:
            c.contact = 0
        channels[i] = c
    new.channels = channels

    zones = {}
    late = []
    for i, z in sorted(old.zones.items()):
        chans = [c for c in z.channels if c in channels][:L["zone_channels"]]
        nz = Zone(name=z.name, channels=chans, scan=[True] * len(chans), cur=(0, 0))
        if i < L["zones"]:
            zones[i] = nz
        else:
            late.append((i, nz))
    for i, nz in late:          # zones au-delà de la limite : déplacées sur un emplacement libre
        slot = next((k for k in range(L["zones"]) if k not in zones), None)
        if slot is None:
            notes.append(f"Zone « {nz.name} » ignorée (plus de place).")
            continue
        zones[slot] = nz
        notes.append(f"Zone « {nz.name} » déplacée de la position {i + 1} à {slot + 1}.")
    new.zones = zones

    if old.settings.radio_id:
        new.settings.radio_id = old.settings.radio_id
    if old.settings.radio_name:
        new.settings.radio_name = old.settings.radio_name
    if old.settings.startup_message:
        new.settings.startup_message = old.settings.startup_message
    return notes
