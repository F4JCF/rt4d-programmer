"""Réglages des modes du firmware F4JCF (idée n°30), à partir de F4JCF15.

Le bloc de 48 octets est rangé dans la flash SPI du poste en 0x100400 : c'est le 2e Ko du secteur
du logo de démarrage (région 0x9A, 4 Ko). Le firmware Radtel ne lit que le 1er Ko de ce secteur
(le logo) et ne l'écrit jamais lui-même. Le firmware F4JCF15 lit le bloc à la mise sous tension ;
s'il est absent ou abîmé, il garde les valeurs d'origine. Un poste sans F4JCF15 l'ignore.

Écrire la région 0x9A efface tout le secteur : le logo est donc relu et réécrit à l'identique.
Format : voir f4ram.h (f4set_t) dans les sources du firmware F4JCF.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

LOGO_REGION = 0x9A
SECTOR_ADDR = 0x100000
SECTOR_SIZE = 0x1000
LOGO_SIZE = 0x400
SET_OFF = 0x400                    # dans le secteur
SET_ADDR = SECTOR_ADDR + SET_OFF
MAGIC = 0x31533446                 # « F4S1 »
VERSION = 1
SIZE = 48
_FMT = "<IBBH4HHBBHH8s16s"
assert struct.calcsize(_FMT) == SIZE

SATER_DEFAULT = [467, 1024, 310, 309]
BAL_CYCLES = [30, 60, 120, 300]
BAL_LONGS = [3, 5, 10, 0]
BAL_TONES = [800, 1000, 600]


@dataclass
class F4Settings:
    sater_ch: list = field(default_factory=lambda: list(SATER_DEFAULT))
    bal_cycle: int = 60
    bal_long: int = 3
    bal_num: int = 0
    bal_tone: int = 800
    rappel_min: int = 10
    bal_call: str = ""

    def check(self) -> list[str]:
        """Liste des erreurs (vide si tout est bon)."""
        err = []
        if len(self.sater_ch) != 4:
            err.append("SATER : il faut 4 canaux")
        else:
            for i, c in enumerate(self.sater_ch):
                lo = 1 if i == 0 else 0
                if not lo <= c <= 1024:
                    err.append(f"SATER, touche {i + 1} : canal {c} hors de {lo}-1024")
        if not 10 <= self.bal_cycle <= 999:
            err.append("Balise : intervalle entre 10 et 999 s")
        if not 0 <= self.bal_long <= 20:
            err.append("Balise : ton long entre 0 et 20 s")
        if not 0 <= self.bal_num <= 5:
            err.append("Balise : numéro entre 0 et 5")
        if not 300 <= self.bal_tone <= 2500:
            err.append("Balise : hauteur du ton entre 300 et 2500 Hz")
        if not 0 <= self.rappel_min <= 60:
            err.append("Rappel d'indicatif : entre 0 (arrêt) et 60 min")
        c = self.bal_call
        if len(c) > 8 or any(not (ch.isascii() and (ch.isupper() or ch.isdigit())) for ch in c):
            err.append("Balise : indicatif de 8 lettres majuscules ou chiffres au plus")
        return err

    def encode(self) -> bytes:
        e = self.check()
        if e:
            raise ValueError(" ; ".join(e))
        body = struct.pack(_FMT, MAGIC, VERSION, SIZE, 0, *self.sater_ch, self.bal_cycle,
                           self.bal_long, self.bal_num, self.bal_tone, self.rappel_min,
                           self.bal_call.encode("ascii").ljust(8, b"\0"), bytes(16))
        s = sum(body[8:]) & 0xFFFF
        return body[:6] + struct.pack("<H", s) + body[8:]


def decode(block: bytes):
    """(F4Settings, état) ; état = 'ok', 'vide' (jamais écrit) ou 'abîmé'."""
    b = bytes(block[:SIZE])
    if len(b) < SIZE:
        return F4Settings(), "vide"
    v = struct.unpack(_FMT, b)
    if v[0] != MAGIC:
        return F4Settings(), "vide"
    if v[1] != VERSION or v[2] != SIZE or v[3] != (sum(b[8:]) & 0xFFFF):
        return F4Settings(), "abîmé"
    s = F4Settings(sater_ch=list(v[4:8]), bal_cycle=v[8], bal_long=v[9], bal_num=v[10],
                   bal_tone=v[11], rappel_min=v[12], bal_call=v[13].rstrip(b"\0").decode("ascii", "replace"))
    if s.check():
        return F4Settings(), "abîmé"
    return s, "ok"


def build_sector(sector: bytes, settings: F4Settings) -> bytes:
    """Nouveau contenu du secteur du logo : logo inchangé, réglages au 2e Ko, reste inchangé."""
    if len(sector) != SECTOR_SIZE:
        raise ValueError("secteur du logo : 4 Ko attendus")
    out = bytearray(sector)
    blk = settings.encode()
    out[SET_OFF:SET_OFF + 1024] = blk + b"\0" * (1024 - len(blk))
    return bytes(out)


def describe_default_channels() -> str:
    return "121,500 (467), 121,375 (1024), 243 (310), 406 (309)"
