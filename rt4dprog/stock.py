"""Firmware OFFICIEL Radtel (V3.18 et suivantes, testé sur V3.22 / CPS V2.07).

Carte mémoire et séquence d'écriture déterminées par analyse du logiciel
officiel « RT-4D CPS V2.07 » (interopérabilité). L'image est un miroir des
premiers 1 Mo de la flash SPI (mêmes adresses que la radio).

Régions écrites par le CPS officiel, dans cet ordre :
  0x90 réglages     0x002000   1 Ko
  0x91 canaux       0x004000  48 Ko   (1024 × 48 octets)
  0x92 VFO          0x01C000   1 Ko
  0x93 zones        0x01E000 128 Ko   (250 × 520 octets)
  0x94 contacts     0x05E000 208 Ko   (10000 × 21 octets)
  0x95 groupes RX   0x0C6000  20 Ko   (250 × 80 octets)
  0x96 chiffrement  0x0D0000  12 Ko
  0x97 SMS prédéf.  0x0D6000   4 Ko
  0x98 radio FM     0x0F0000   4 Ko
"""

from __future__ import annotations

import struct
from typing import Optional

from .codeplug import (Channel, Contact, GroupList, Settings, Zone, bcd_decode, bcd_encode,
                       fmt_mhz, get_name, put_name)

IMAGE_SIZE = 0x100000

A_CFG = 0x002000
A_CH = 0x004000
A_VFO = 0x01C000
A_ZONE = 0x01E000
A_CONTACT = 0x05E000
A_TGLIST = 0x0C6000
A_ENCRYPT = 0x0D0000
A_SMS = 0x0D6000
A_FM = 0x0F0000

# (région, adresse, nombre de blocs de 1 Ko, libellé) — ordre du CPS officiel
WRITE_REGIONS = [
    (0x90, A_CFG, 1, "Réglages"),
    (0x91, A_CH, 48, "Canaux"),
    (0x92, A_VFO, 1, "VFO"),
    (0x93, A_ZONE, 128, "Zones"),
    (0x94, A_CONTACT, 208, "Contacts"),
    (0x95, A_TGLIST, 20, "Groupes RX"),
    (0x96, A_ENCRYPT, 12, "Chiffrement"),
    (0x97, A_SMS, 4, "SMS prédéfinis"),
    (0x98, A_FM, 4, "Radio FM"),
]

# Blocs lus par le CPS officiel (numéros de Ko) : 522 blocs
READ_BLOCKS = ([8] + list(range(16, 64)) + [112] + list(range(120, 248))
               + list(range(376, 584)) + list(range(792, 812)) + list(range(832, 844))
               + list(range(856, 956)) + list(range(960, 964)))

CH_SIZE, CH_MAX = 48, 1024
ZN_SIZE, ZN_MAX, ZN_CH_MAX = 520, 250, 250
CT_SIZE, CT_MAX = 21, 10000
GL_SIZE, GL_MAX, GL_IDS = 80, 250, 32

# Touches programmables (bloc VFO, réglages n°2 à 0x1C060) — d'après le CPS officiel
A_CFG2 = A_VFO + 0x60
KEY_SLOTS = [                       # (décalage dans A_CFG2, libellé)
    (31, "Touche latérale 1 — appui court"),
    (32, "Touche latérale 1 — appui long"),
    (33, "Touche latérale 2 — appui court"),
    (34, "Touche latérale 2 — appui long"),
    (35, "Touche alarme — appui court"),
    (36, "Touche alarme — appui long"),
] + [(37 + i, f"Touche {i} — appui long") for i in range(10)]

KEY_FUNCTIONS = [
    "Aucune fonction", "Écoute analogique (monitor)", "Puissance haute/basse",
    "Double veille", "Priorité émission", "Balayage (scan)", "Rétroéclairage marche/arrêt",
    "Bip de fin d'émission (analogique)", "Radio FM", "Talkaround (sans relais)",
    "Alarme d'urgence", "Mesure de fréquence", "Décodage sous-ton à distance",
    "Envoi d'un ton (tone)", "Interrogation d'état", "Écoute à distance",
    "DMR : neutralisation à distance (stun)", "DMR : destruction à distance (kill)",
    "DMR : réactivation à distance", "Détection en ligne", "Affichage de l'appelé",
    "Bascule AM/FM (réception)", "Spectre analogique", "Niveau de squelch",
    "Pas de fréquence", "Bascule analogique/DMR", "Canal météo NOAA",
    "Enregistrer le canal", "Nouveau SMS", "Menu SMS", "Luminosité de l'écran",
    "VOX analogique", "Choix de la zone", "Mode promiscuité (DMR)",
    "Double slot marche/arrêt", "Changement de slot", "Changement de code couleur",
    "Chiffrement DMR marche/arrêt", "Choix du groupe RX", "Aller aux contacts",
    "Choix DTMF",
]

# Réglages « Menu démarrage » : (clé, zone, décalage, taille, choix ou (min, max), libellé, groupe)
_TIMER = ["Arrêt"] + [f"{(i * 5) if i <= 3 else (i - 2) * 15} s" for i in range(1, 43)]
_ONOFF = ["Non", "Oui"]
STARTUP_FIELDS = [
    ("logo", "cfg", 16, 1, _ONOFF, "Logo au démarrage", "Allumage"),
    ("tone", "cfg", 19, 1, _ONOFF, "Bip au démarrage", "Allumage"),
    ("text", "cfg", 20, 1, _ONOFF, "Afficher le message d'accueil", "Allumage"),
    ("line", "cfg", 23, 2, (0, 7), "Ligne du message (0 = en haut)", "Allumage"),
    ("col", "cfg", 25, 2, (0, 127), "Colonne du message (en pixels)", "Allumage"),
    ("work_a", "cfg2", 20, 1, ["Fréquence (VFO)", "Canal", "Zone"], "Mode de travail voie A", "Au démarrage"),
    ("work_b", "cfg2", 21, 1, ["Fréquence (VFO)", "Canal", "Zone"], "Mode de travail voie B", "Au démarrage"),
    ("zone_a", "cfg2", 24, 1, "zones", "Zone de la voie A", "Au démarrage"),
    ("zone_b", "cfg2", 25, 1, "zones", "Zone de la voie B", "Au démarrage"),
    ("disp_a", "cfg2", 22, 1, ["Numéro de canal", "Fréquence", "Nom"], "Affichage voie A", "Affichage"),
    ("disp_b", "cfg2", 23, 1, ["Numéro de canal", "Fréquence", "Nom"], "Affichage voie B", "Affichage"),
    ("digits", "cfg", 234, 1, ["6 chiffres", "8 chiffres"], "Chiffres de la fréquence", "Affichage"),
    ("light", "cfg", 97, 1, ["0", "1", "2", "3", "4"], "Luminosité de l'écran", "Affichage"),
    ("light_t", "cfg", 98, 1, _TIMER, "Extinction du rétroéclairage", "Affichage"),
    ("contrast", "cfg", 233, 1, (5, 25), "Contraste", "Affichage"),
    ("menu_t", "cfg", 101, 1, _TIMER, "Sortie automatique des menus", "Affichage"),
    ("grp_disp", "cfg", 404, 1, ["Ville de l'appelant", "ID de l'appelant"], "Appel de groupe : afficher", "Affichage"),
    ("voice", "cfg", 92, 1, _ONOFF, "Annonces vocales", "Sons et voyants"),
    ("beep", "cfg", 93, 1, _ONOFF, "Bip des touches", "Sons et voyants"),
    ("carrier", "cfg", 852, 1, _ONOFF, "Voyant de porteuse", "Sons et voyants"),
]

# Limites de fréquences (réglages 0x2000, octets 142-161 ; lues par le firmware V3.25 en RAM 0x20002A49).
# 4 plages de 5 octets : type, début (u16, MHz), fin (u16, MHz), bornes incluses.
# Type 1 : émission interdite dans la plage. Type 2 : réception autorisée seulement dans les plages
# de type 2. En plus, le firmware refuse toujours l'émission de 108 à 136 MHz (bande aviation).
LIMIT_OFF = 142
LIMIT_N = 4
LIMIT_TYPES = ["Inactive", "Émission interdite", "Réception seulement dans cette plage"]
LIMIT_MHZ = (18, 1000)                 # plage de réception du poste (18-620 et 840-1000 MHz)
LIMIT_UNUSED = (0, 18, 1000)           # valeur d'usine d'une plage inactive
# Émission limitée aux bandes radioamateur 144-146 et 430-440 MHz
HAM_PRESET = [(1, 18, 144), (1, 146, 430), (1, 440, 1000), LIMIT_UNUSED]


def tx_allowed(limits, f: int) -> bool:
    """Reproduit le contrôle d'émission du firmware V3.25 (fonction 0x08005F8C).
    f : fréquence d'émission en unités de 10 Hz (comme dans les canaux)."""
    if 10_800_000 <= f <= 13_600_000:
        return False
    return not any(t == 1 and a * 100_000 <= f <= b * 100_000 for t, a, b in limits)


CONNECT = bytes([0x34, 0x52, 0x05, 0x10, 0x9B])
END = bytes([0x34, 0x52, 0x05, 0xEE, 0x79])
ALL_CALL = bytes([0x02, 0xAA, 0xAA, 0xAA, 0xAA])


def looks_like_stock(contact_head: bytes) -> bool:
    """Signature : le contact n°0 « All Call » (02 AA AA AA AA) à 0x5E000."""
    return contact_head[:5] == ALL_CALL


def zone_default_name(i: int) -> str:
    return f"Zone-{i + 1:03d}"


def group_default_name(i: int) -> str:
    return f"TG List-{i + 1:03d}"


class StockCodeplug:
    kind = "stock"
    is_refw = False
    LIMITS = dict(channels=CH_MAX, contacts=CT_MAX, groups=GL_MAX, group_ids=GL_IDS,
                  zones=ZN_MAX, zone_channels=ZN_CH_MAX, zone_scan=False, contact0_fixed=True,
                  keys=True, startup=True, freq_limits=True)

    def __init__(self, image: bytes):
        if len(image) >= IMAGE_SIZE:
            image = bytes(image[:IMAGE_SIZE])
        if len(image) != IMAGE_SIZE or not looks_like_stock(image[A_CONTACT:A_CONTACT + 5]):
            raise ValueError("Ce fichier n'est pas une image du firmware officiel Radtel V3.18+.")
        self.image = bytearray(image)
        self.parse()

    @property
    def has_settings(self) -> bool:
        return self.image[A_CFG + 12:A_CFG + 14] == b"\xCD\xAB"

    # ── lecture ──
    def parse(self):
        img = self.image
        self.channels: dict[int, Channel] = {}
        for i in range(CH_MAX):
            c = Channel.parse(img[A_CH + i * CH_SIZE:A_CH + (i + 1) * CH_SIZE])
            if c:
                if c.contact is not None and c.contact >= CT_MAX:
                    c.contact = None
                self.channels[i] = c
        self.contacts: dict[int, Contact] = {}
        for i in range(CT_MAX):
            r = img[A_CONTACT + i * CT_SIZE:A_CONTACT + (i + 1) * CT_SIZE]
            if r[0] > 2:
                break                      # liste contiguë : fin au premier vide
            dmr_id = 16777215 if i == 0 else bcd_decode(r[1:5])
            self.contacts[i] = Contact(name=get_name(r, 5, 16), ctype=r[0], dmr_id=dmr_id,
                                       raw=bytes(r))
        self.groups: dict[int, GroupList] = {}
        for i in range(GL_MAX):
            r = img[A_TGLIST + i * GL_SIZE:A_TGLIST + (i + 1) * GL_SIZE]
            name = get_name(r, 0, 16)
            ids = []
            for k in range(GL_IDS):
                v = struct.unpack_from("<H", r, 16 + 2 * k)[0]
                if v != 0xFFFF and v in self.contacts:
                    ids.append(v)
            if ids or (name and name != group_default_name(i)):
                self.groups[i] = GroupList(name=name or group_default_name(i), contacts=ids,
                                           raw=bytes(r))
        self.zones: dict[int, Zone] = {}
        for i in range(ZN_MAX):
            r = img[A_ZONE + i * ZN_SIZE:A_ZONE + (i + 1) * ZN_SIZE]
            name = get_name(r, 4, 16)
            chans = []
            for k in range(ZN_CH_MAX):
                v = struct.unpack_from("<H", r, 20 + 2 * k)[0]
                if v == 0xFFFF:
                    break
                if v in self.channels:
                    chans.append(v)
            if chans or (name and name != zone_default_name(i)):
                cur = (struct.unpack_from("<H", r, 0)[0], struct.unpack_from("<H", r, 2)[0])
                self.zones[i] = Zone(name=name or zone_default_name(i), channels=chans,
                                     scan=[True] * len(chans), cur=cur)
        self.keys = [img[A_CFG2 + off] for off, _ in KEY_SLOTS]
        self.startup = {}
        for key, area, off, size, *_ in STARTUP_FIELDS:
            base = A_CFG if area == "cfg" else A_CFG2
            self.startup[key] = int.from_bytes(img[base + off:base + off + size], "little")
        cfg = img[A_CFG:A_CFG + 0x400]
        self.freq_limits = [list(struct.unpack_from("<BHH", cfg, LIMIT_OFF + 5 * i))
                            for i in range(LIMIT_N)]
        self.settings = Settings(radio_id=bcd_decode(cfg[384:388]),
                                 radio_name=get_name(cfg, 76, 16),
                                 startup_message=get_name(cfg, 44, 32))

    # ── écriture ──
    def _contact_order(self) -> list[int]:
        """Ordre des contacts après compactage ; le n°0 (All Call) reste en tête."""
        return sorted(self.contacts)

    def build(self) -> bytes:
        img = bytearray(self.image)
        order = self._contact_order()
        remap = {old: new for new, old in enumerate(order)}

        # contacts (liste contiguë)
        img[A_CONTACT:A_CONTACT + 208 * 1024] = b"\xFF" * (208 * 1024)
        for new, old in enumerate(order):
            c = self.contacts[old]
            r = bytearray(c.raw) if c.raw else bytearray(b"\xFF" * CT_SIZE)
            if new == 0:
                r[0:5] = ALL_CALL
            else:
                r[0] = c.ctype & 3
                r[1:5] = bcd_encode(c.dmr_id)
            put_name(r, 5, c.name, 16)
            img[A_CONTACT + new * CT_SIZE:A_CONTACT + (new + 1) * CT_SIZE] = r

        # canaux
        for i in range(CH_MAX):
            ch = self.channels.get(i)
            if ch is None:
                rec = b"\xFF" * CH_SIZE
            else:
                b = bytearray(ch.encode())
                struct.pack_into("<H", b, 0x11, remap.get(ch.contact, 0) if ch.contact is not None else 0)
                rec = bytes(b)
            img[A_CH + i * CH_SIZE:A_CH + (i + 1) * CH_SIZE] = rec

        # groupes RX
        for i in range(GL_MAX):
            g = self.groups.get(i)
            r = bytearray(g.raw) if g and g.raw else bytearray(b"\xFF" * GL_SIZE)
            r[16:GL_SIZE] = b"\xFF" * (GL_SIZE - 16)
            put_name(r, 0, g.name if g else group_default_name(i), 16)
            if g:
                ids = [remap[c] for c in g.contacts if c in remap][:GL_IDS]
                for k, v in enumerate(ids):
                    struct.pack_into("<H", r, 16 + 2 * k, v)
            img[A_TGLIST + i * GL_SIZE:A_TGLIST + (i + 1) * GL_SIZE] = r

        # zones
        for i in range(ZN_MAX):
            z = self.zones.get(i)
            r = bytearray(b"\xFF" * ZN_SIZE)
            if z and z.channels:
                chans = z.channels[:ZN_CH_MAX]
                ca = z.cur[0] if z.cur[0] in chans else chans[0]
                cb = z.cur[1] if z.cur[1] in chans else chans[0]
            else:
                chans, ca, cb = [], 0, 0
            struct.pack_into("<HH", r, 0, ca, cb)
            put_name(r, 4, z.name if z else zone_default_name(i), 16)
            for k, v in enumerate(chans):
                struct.pack_into("<H", r, 20 + 2 * k, v)
            img[A_ZONE + i * ZN_SIZE:A_ZONE + (i + 1) * ZN_SIZE] = r

        # menu démarrage / affichage
        for key, area, off, size, *_ in STARTUP_FIELDS:
            base = A_CFG if area == "cfg" else A_CFG2
            img[base + off:base + off + size] = int(self.startup[key]).to_bytes(size, "little")

        # touches programmables
        for (off, _), val in zip(KEY_SLOTS, self.keys):
            img[A_CFG2 + off] = val & 0xFF

        # réglages (seuls les champs gérés sont modifiés)
        s = self.settings
        cfg = bytearray(img[A_CFG:A_CFG + 0x400])
        cfg[384:388] = bcd_encode(s.radio_id)
        put_name(cfg, 76, s.radio_name, 16)
        put_name(cfg, 44, s.startup_message, 32)
        for i, (t, a, b) in enumerate(self.freq_limits):
            struct.pack_into("<BHH", cfg, LIMIT_OFF + 5 * i, t & 0xFF, a & 0xFFFF, b & 0xFFFF)
        img[A_CFG:A_CFG + 0x400] = cfg
        return bytes(img)

    def commit(self):
        self.image = bytearray(self.build())
        self.parse()

    def save(self, path: str):
        with open(path, "wb") as f:
            f.write(self.build())

    @classmethod
    def load(cls, path: str) -> "StockCodeplug":
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
        for slot, ch in sorted(self.channels.items()):
            lab = f"Canal {slot + 1} « {ch.name} »"
            for nm, f in (("RX", ch.rx), ("TX", ch.tx)):
                if f and not (1_800_000 <= f <= 100_000_000):
                    pb.append(f"{lab} : fréquence {nm} {fmt_mhz(f)} MHz hors plage.")
            if not ch.rx:
                pb.append(f"{lab} : fréquence RX manquante.")
            if ch.group is not None and ch.group >= GL_MAX:
                pb.append(f"{lab} : groupe RX invalide.")
        for slot, c in self.contacts.items():
            if slot and not 0 < c.dmr_id <= 16_777_215:
                pb.append(f"Contact « {c.name} » : ID DMR invalide ({c.dmr_id}).")
        for z in self.zones.values():
            if len(z.channels) > ZN_CH_MAX:
                pb.append(f"Zone « {z.name} » : plus de {ZN_CH_MAX} canaux.")
        for g in self.groups.values():
            if len(g.contacts) > GL_IDS:
                pb.append(f"Groupe « {g.name} » : plus de {GL_IDS} contacts.")
        for i, (t, a, b) in enumerate(self.freq_limits):
            if t in (1, 2) and not (LIMIT_MHZ[0] <= a <= b <= LIMIT_MHZ[1]):
                pb.append(f"Limite de fréquences n°{i + 1} : plage {a}-{b} MHz invalide "
                          f"(début ≤ fin, entre {LIMIT_MHZ[0]} et {LIMIT_MHZ[1]} MHz).")
        return pb
