"""Stations surveillées (firmware F4JCF22 ou plus récent, idée n°34) : liste d'ID DMR qui déclenchent le
double bip de la radio comme un contact privé, sans créer de contact.

Emplacement : secteur du logo (0x100000), décalage 0xC00 :
  « F4W1 » (0x31573446 petit-boutiste), nombre d'ID (u8, 64 au plus), 3 octets à 0, puis les ID (u32).
Le reste du secteur (logo, réglages des modes, journal) n'est pas touché.
"""
from __future__ import annotations

import struct

OFFSET = 0xC00
MAGIC = 0x31573446
MAX = 64
SIZE = 8 + 4 * MAX


class WatchError(ValueError):
    pass


def decode(sector: bytes) -> list[int]:
    """Liste des ID du secteur (4 Ko) ; [] s'il n'y en a pas."""
    if len(sector) < OFFSET + 8:
        raise WatchError("secteur trop court")
    magic, n = struct.unpack_from("<IB", sector, OFFSET)
    if magic != MAGIC:
        return []
    n = min(n, MAX)
    return list(struct.unpack_from("<%dI" % n, sector, OFFSET + 8))


def encode(ids: list[int]) -> bytes:
    ids = clean(ids)
    if len(ids) > MAX:
        raise WatchError("%d stations au plus" % MAX)
    return (struct.pack("<IB3x", MAGIC, len(ids)) + b"".join(struct.pack("<I", i) for i in ids)).ljust(SIZE, b"\0")


def clean(ids) -> list[int]:
    """ID valides (1 à 16 777 214), sans doublon, dans l'ordre."""
    out = []
    for i in ids:
        i = int(i)
        if not 1 <= i <= 0xFFFFFE:
            raise WatchError("ID DMR invalide : %d" % i)
        if i not in out:
            out.append(i)
    return out


def build_sector(cur: bytes, ids: list[int]) -> bytes:
    """Nouveau secteur : la liste remplace l'ancienne, tout le reste est gardé."""
    if len(cur) != 4096:
        raise WatchError("secteur de 4 Ko attendu")
    data = encode(ids)
    return cur[:OFFSET] + data + cur[OFFSET + SIZE:]
