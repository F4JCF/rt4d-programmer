"""Firmware OFFICIEL Radtel ANCIEN (V3.17 et antérieurs, testé sur V3.14 du 2025-03-21).

Carte mémoire et séquence d'écriture déterminées par analyse du logiciel officiel
« RT-4D CPS V1.19 (250322) » (interopérabilité), confirmées sur une sauvegarde
réelle d'un RT-4D en V3.14. L'image est un miroir du premier Mo de la flash SPI.

Régions écrites par le CPS V1.19, dans cet ordre (le firmware tient lui-même
les copies de secours à 0x003000, 0x010000, 0x03C000, 0x06C000 et 0x07F000) :
  0x90 réglages      0x002000   4 Ko
  0x91 canaux        0x004000  48 Ko   (1024 × 48 octets)
  0x92 zones         0x01C000 128 Ko   (256 × 512 octets, 200 canaux par zone)
  0x93 contacts      0x05C000  64 Ko   (2000 × 32 octets, liste contiguë)
  0x94 groupes RX    0x07C000  12 Ko   (256 × 48 octets, 16 contacts par groupe)
  0x95 chiffrement   0x082000  12 Ko
  0x97 SMS prédéf.   0x094000   4 Ko
  0x99 radio FM      0x0D6000   1 Ko
Ouverture et fin de session identiques au firmware V3.18+.
"""

from __future__ import annotations

import struct
from typing import Optional

from . import tones
from .codeplug import (Channel, Contact, GroupList, Settings, Zone, bcd_decode, bcd_encode,
                       fmt_mhz, get_name, put_name)

IMAGE_SIZE = 0x100000

A_CFG = 0x002000
A_CH = 0x004000
A_ZONE = 0x01C000
A_CONTACT = 0x05C000
A_TGLIST = 0x07C000
A_ENCRYPT = 0x082000
A_SMS = 0x094000
A_FM = 0x0D6000

CH_SIZE, CH_MAX = 48, 1024
ZN_SIZE, ZN_MAX, ZN_CH_MAX = 512, 256, 200
CT_SIZE, CT_MAX, CT_AREA = 32, 2000, 2048
GL_SIZE, GL_MAX, GL_IDS = 48, 256, 16

ALL_CALL = bytes([0x02, 0xAA, 0xAA, 0xAA, 0xAA])

# (région, adresse, nombre de blocs de 1 Ko, libellé) — ordre du CPS V1.19
WRITE_REGIONS = [
    (0x90, A_CFG, 4, "Réglages"),
    (0x91, A_CH, 48, "Canaux"),
    (0x92, A_ZONE, 128, "Zones"),
    (0x93, A_CONTACT, 64, "Contacts"),
    (0x94, A_TGLIST, 12, "Groupes RX"),
    (0x95, A_ENCRYPT, 12, "Chiffrement"),
    (0x97, A_SMS, 4, "SMS prédéfinis"),
    (0x99, A_FM, 1, "Radio FM"),
]
READ_RANGES = [(a, n) for _, a, n, _ in WRITE_REGIONS]


def looks_like_legacy(contact_head: bytes) -> bool:
    """Signature : contact n°1 « All Call » (01 02 AA AA AA AA) à 0x5C000."""
    return contact_head[0] == 0x01 and contact_head[1:6] == ALL_CALL


def zone_default_name(i: int) -> str:
    return f"Zone-{i + 1:03d}"


def parse_channel(b: bytes) -> Optional[Channel]:
    """Enregistrement canal de l'ancien firmware → modèle commun (champs du CPS V1.19)."""
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
                 power_high=b[0x10] == 1, scan=not (b[0x13] & 0x80), raw=bytes(b))
    if analog:
        ch.modulation = b[0] if b[0] <= 2 else 0
        ch.narrow = b[3] == 1
        ch.rx_tone = tones.decode(b[0x04:0x06])
        ch.tx_tone = tones.decode(b[0x0E:0x10])
        ch.tot = b[0x12] & 0x1F
        ch.ctdcs_select = (b[0x12] >> 5) & 7
        ch.tail_tone = (b[0x13] >> 4) & 7
        ch.scramble = b[0x13] & 0x0F
        ch.ana_busy = b[0x11] if b[0x11] <= 3 else 0
    else:
        ch.timeslot = b[3] & 1
        ch.color_code = b[4] & 0x0F
        gl = struct.unpack_from("<H", b, 0x16)[0]
        ch.group = None if gl in (0, 0xFFFF) else gl - 1
        ct = struct.unpack_from("<H", b, 0x18)[0]
        ch.contact = None if ct == 0xFFFF else ct
        ch.encrypt = struct.unpack_from("<H", b, 0x1A)[0]
        ch.use_radio_id = b[0] == 0
        ch.dmr_id = bcd_decode(b[0x1C:0x20])
        ch.monitor = b[0x0E] & 1
        ch.dmr_busy = b[0x11] if b[0x11] <= 3 else 0
    return ch


def encode_channel(ch: Channel, contact: int, group: Optional[int]) -> bytes:
    """Modèle commun → enregistrement 48 octets, comme « SaveSingle » du CPS V1.19.

    Les octets non gérés ici sont conservés quand le type du canal ne change pas.
    """
    same = ch.raw is not None and ch.raw[2] == (1 if ch.analog else 0)
    d = bytearray(ch.raw) if same else bytearray(b"\xFF" * CH_SIZE)
    if not same:
        if ch.analog:
            d[0x10:0x20] = b"\x00" * 16
        else:
            d[0] = 0                      # ID de la radio
            d[5] = 0                      # mode DMR
            d[0x0E] = 0
            d[0x11] = 0
            d[0x14] = d[0x15] = 0
            d[0x1A:0x1C] = b"\x00\x00"
            d[0x1C:0x20] = bcd_encode(ch.dmr_id or 0)
    d[1] = 1
    d[2] = 1 if ch.analog else 0
    struct.pack_into("<I", d, 0x06, ch.rx)
    struct.pack_into("<I", d, 0x0A, ch.tx)
    d[0x10] = 1 if ch.power_high else 0
    if ch.analog:
        d[0] = ch.modulation if ch.modulation in (0, 1, 2) else 0
        d[3] = 1 if ch.narrow else 0
        d[0x04:0x06] = tones.encode(ch.rx_tone)
        d[0x0E:0x10] = tones.encode(ch.tx_tone)
        d[0x11] = ch.ana_busy & 3
        d[0x12] = (ch.tot & 0x1F) | ((ch.ctdcs_select & 7) << 5)
        d[0x13] = (ch.scramble & 0x0F) | ((ch.tail_tone & 7) << 4) | (0 if ch.scan else 0x80)
    else:
        d[0] = 0 if ch.use_radio_id else 1
        d[3] = ch.timeslot & 1
        d[4] = ch.color_code & 0x0F
        d[0x0E] = ch.monitor & 1
        d[0x11] = ch.dmr_busy & 3
        if bcd_decode(d[0x1C:0x20]) != ch.dmr_id:
            d[0x1C:0x20] = bcd_encode(ch.dmr_id)
        struct.pack_into("<H", d, 0x1A, ch.encrypt & 0xFFFF)
        struct.pack_into("<H", d, 0x16, 0 if group is None else group + 1)
        struct.pack_into("<H", d, 0x18, contact)
        d[0x13] = 0 if ch.scan else 0x80
    put_name(d, 0x20, ch.name, 16)
    return bytes(d)


class LegacyCodeplug:
    kind = "legacy"
    is_refw = False
    read_only = False
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

    # ── lecture ──
    def parse(self):
        img = self.image
        self.contacts: dict[int, Contact] = {}
        for i in range(CT_MAX):
            r = img[A_CONTACT + i * CT_SIZE:A_CONTACT + (i + 1) * CT_SIZE]
            if r[1] > 2:
                continue
            dmr_id = 16777215 if i == 0 else bcd_decode(r[2:6])
            self.contacts[i] = Contact(name=get_name(r, 0x10, 16), ctype=r[1], dmr_id=dmr_id,
                                       raw=bytes(r))
        self.channels: dict[int, Channel] = {}
        for i in range(CH_MAX):
            c = parse_channel(img[A_CH + i * CH_SIZE:A_CH + (i + 1) * CH_SIZE])
            if c:
                if c.contact is not None and c.contact not in self.contacts:
                    c.contact = 0
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
            self.groups[i] = GroupList(name=get_name(r, 2, 14) or f"Groupe {i + 1}", contacts=ids,
                                       raw=bytes(r))
        for ch in self.channels.values():
            if ch.group is not None and ch.group not in self.groups:
                ch.group = None
        self.zones: dict[int, Zone] = {}
        for i in range(ZN_MAX):
            r = img[A_ZONE + i * ZN_SIZE:A_ZONE + (i + 1) * ZN_SIZE]
            if r[0] == 0xFF and r[4] == 0xFF:
                continue
            chans = []
            for k in range(ZN_CH_MAX):
                v = struct.unpack_from("<H", r, 0x14 + 2 * k)[0]
                if v == 0xFFFF:
                    break
                if v in self.channels:
                    chans.append(v)
            name = get_name(r, 4, 16)
            if chans or (name and name != zone_default_name(i)):
                self.zones[i] = Zone(name=name or zone_default_name(i), channels=chans,
                                     scan=[True] * len(chans), cur=(r[2], r[3]))
        cfg = img[A_CFG:A_CFG + 0x400]
        self.settings = Settings(radio_id=bcd_decode(cfg[384:388]),
                                 radio_name=get_name(cfg, 76, 16),
                                 startup_message=get_name(cfg, 44, 32))
        self.keys = []
        self.startup = {}

    # ── écriture (même construction que « SaveData » du CPS V1.19) ──
    def build(self) -> bytes:
        img = bytearray(self.image)

        # contacts : liste contiguë, le n°0 (All Call) reste en tête
        order = sorted(self.contacts)
        if 0 in order:
            order.remove(0)
        order = [0] + order
        cremap = {old: new for new, old in enumerate(order)}
        area = bytearray(b"\xFF" * (CT_AREA * CT_SIZE))
        for i in range(CT_MAX):
            area[i * CT_SIZE] = (i + 1) & 0xFF
        for new, old in enumerate(order[:CT_MAX]):
            c = self.contacts.get(old) or Contact(name="All Call", ctype=2, dmr_id=16777215)
            r = bytearray(b"\xFF" * CT_SIZE)
            r[0] = (new + 1) & 0xFF
            if new == 0:
                r[1:6] = ALL_CALL
            else:
                r[1] = c.ctype & 3
                r[2:6] = bcd_encode(c.dmr_id)
            if c.raw and c.raw[0x10:0x20] and get_name(c.raw, 0x10, 16) == (c.name or "").strip():
                r[0x10:0x20] = c.raw[0x10:0x20]
            else:
                put_name(r, 0x10, c.name or ("All Call" if new == 0 else ""), 16)
            area[new * CT_SIZE:(new + 1) * CT_SIZE] = r
        img[A_CONTACT:A_CONTACT + len(area)] = area

        # groupes RX : contigus (le canal référence la position + 1)
        gorder = sorted(self.groups)
        gremap = {old: new for new, old in enumerate(gorder)}
        garea = bytearray(b"\xFF" * (GL_MAX * GL_SIZE))
        for new, old in enumerate(gorder[:GL_MAX]):
            g = self.groups[old]
            r = bytearray(b"\xFF" * GL_SIZE)
            r[0], r[1] = new, 1
            if g.raw:
                r[2:16] = g.raw[2:16]
            put_name(r, 2, g.name, 14)
            ids = [cremap[c] for c in g.contacts if c in cremap][:GL_IDS]
            for k, v in enumerate(ids):
                struct.pack_into("<H", r, 0x10 + 2 * k, v)
            garea[new * GL_SIZE:(new + 1) * GL_SIZE] = r
        img[A_TGLIST:A_TGLIST + len(garea)] = garea

        # canaux
        for i in range(CH_MAX):
            ch = self.channels.get(i)
            if ch is None:
                rec = b"\xFF" * CH_SIZE
            else:
                ct = cremap.get(ch.contact, 0) if ch.contact is not None else 0
                gl = gremap.get(ch.group) if ch.group is not None else None
                rec = encode_channel(ch, ct, gl)
            img[A_CH + i * CH_SIZE:A_CH + (i + 1) * CH_SIZE] = rec

        # zones : toutes écrites, les vides avec leur nom par défaut
        for i in range(ZN_MAX):
            z = self.zones.get(i)
            old = self.image[A_ZONE + i * ZN_SIZE:A_ZONE + (i + 1) * ZN_SIZE]
            r = bytearray(b"\xFF" * ZN_SIZE)
            chans = [c for c in (z.channels if z else []) if c in self.channels][:ZN_CH_MAX]
            r[0] = r[1] = len(chans)
            r[2], r[3] = (old[2], old[3]) if z and chans else (0xFF, 0xFF)
            if z and chans:
                if r[2] != 0xFF and r[2] >= len(chans):
                    r[2] = 0
                if r[3] != 0xFF and r[3] >= len(chans):
                    r[3] = 0
            r[4:20] = old[4:20] if z and old[4] != 0xFF else b"\xFF" * 16
            if not (z and old[4] == 0xFF and z.name == zone_default_name(i)):
                put_name(r, 4, z.name if z else zone_default_name(i), 16)
            for k, v in enumerate(chans):
                struct.pack_into("<H", r, 0x14 + 2 * k, v)
            img[A_ZONE + i * ZN_SIZE:A_ZONE + (i + 1) * ZN_SIZE] = r

        # réglages : seuls l'ID DMR, le nom et le message d'accueil sont modifiés
        s = self.settings
        cfg = bytearray(img[A_CFG:A_CFG + 0x400])
        if bcd_decode(cfg[384:388]) != s.radio_id:
            cfg[384:388] = bcd_encode(s.radio_id)
        put_name(cfg, 76, s.radio_name, 16)
        put_name(cfg, 44, s.startup_message, 32)
        img[A_CFG:A_CFG + 0x400] = cfg
        return bytes(img)

    def commit(self):
        self.image = bytearray(self.build())
        self.parse()

    def save(self, path: str):
        with open(path, "wb") as f:
            f.write(self.build())

    @classmethod
    def load(cls, path: str) -> "LegacyCodeplug":
        with open(path, "rb") as f:
            return cls(f.read())

    # ── gestion ──
    @staticmethod
    def free_slot(d: dict, maxn: int) -> Optional[int]:
        for i in range(maxn):
            if i not in d:
                return i
        return None

    def delete_channel(self, slot: int):
        self.channels.pop(slot, None)
        for z in self.zones.values():
            z.channels = [c for c in z.channels if c != slot]
            z.scan = [True] * len(z.channels)

    def delete_contact(self, slot: int):
        if slot == 0:
            return                      # « All Call » obligatoire
        self.contacts.pop(slot, None)
        for ch in self.channels.values():
            if ch.contact == slot:
                ch.contact = 0
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
        pb = []
        if 0 not in self.contacts:
            pb.append("Le contact n°1 « All Call » est obligatoire.")
        if len(self.contacts) > CT_MAX:
            pb.append(f"Plus de {CT_MAX} contacts.")
        for slot, ch in sorted(self.channels.items()):
            lab = f"Canal {slot + 1} « {ch.name} »"
            for nm, f in (("RX", ch.rx), ("TX", ch.tx)):
                if f and not (1_800_000 <= f <= 100_000_000):
                    pb.append(f"{lab} : fréquence {nm} {fmt_mhz(f)} MHz hors plage.")
            if not ch.rx:
                pb.append(f"{lab} : fréquence RX manquante.")
        for slot, c in self.contacts.items():
            if slot and not 0 < c.dmr_id <= 16_777_215:
                pb.append(f"Contact « {c.name} » : ID DMR invalide ({c.dmr_id}).")
        for z in self.zones.values():
            if len(z.channels) > ZN_CH_MAX:
                pb.append(f"Zone « {z.name} » : plus de {ZN_CH_MAX} canaux.")
        for g in self.groups.values():
            if len(g.contacts) > GL_IDS:
                pb.append(f"Groupe « {g.name} » : plus de {GL_IDS} contacts.")
        return pb


def convert_into(old: LegacyCodeplug, new) -> list[str]:
    """Copie canaux, contacts, groupes RX, zones et ID DMR d'un ancien codeplug
    dans un codeplug V3.18+ (StockCodeplug lu dans la radio mise à jour).

    Retourne la liste des remarques (éléments tronqués ou ignorés).
    """
    notes = []
    L = new.LIMITS
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
