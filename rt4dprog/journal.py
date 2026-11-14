"""Journal F4JCF (firmware F4JCF19 ou plus récent) : carnet de relevés du mode SATER et liste des
derniers DMR entendus, gardés par la radio dans la mémoire SPI en 0x100800 (secteur du logo).

Format (voir claude/firmware-f4jcf-19.md) :
  en-tête 16 octets : « F4J1 » (0x314A3446 petit-boutiste), version (1), nombre de relevés, nombre de
  stations, 0, somme 16 bits des octets 16 à 655, 6 octets réservés ;
  20 relevés de 16 octets : niveau max (RSSI brut, u16), chrono (s, u16), touche SATER (u8), ouest (u8),
  azimut (u16, 0xFFFF = aucun), latitude et longitude en 1/10 000 de degré (u32, 0 = aucune) ;
  16 stations de 20 octets : ID (u32), heure interne (u32), passages (u16), groupe (u16, 0 = privé,
  tronqué à 16 bits), indicatif (8 caractères).
"""
from __future__ import annotations

import csv
import struct
from dataclasses import dataclass, field

SECTOR_ADDR = 0x100000
OFFSET = 0x800                       # dans le secteur de 4 Ko
MAGIC = 0x314A3446
SIZE = 656
NREL, REL_SIZE = 20, 16
NHEARD, HEARD_SIZE = 16, 20


class JournalError(ValueError):
    pass


@dataclass
class Releve:
    num: int
    dbm: int
    chrono_s: int
    touche: int
    azimut: int | None
    lat: float | None
    lon: float | None

    @property
    def position(self) -> str:
        """« 43.6012 E07.0034 », comme l'écran de la radio (lu par la fenêtre Triangulation)."""
        if self.lat is None:
            return ""
        ew = "O" if self.lon < 0 else "E"
        return "%07.4f %s%07.4f" % (self.lat, ew, abs(self.lon))

    @property
    def chrono(self) -> str:
        return "%d:%02d" % divmod(self.chrono_s, 60)


@dataclass
class Station:
    id: int
    passages: int
    groupe: int
    indicatif: str

    @property
    def appel(self) -> str:
        return "privé" if self.groupe == 0 else "TG %d" % self.groupe


@dataclass
class Journal:
    releves: list[Releve] = field(default_factory=list)
    stations: list[Station] = field(default_factory=list)


def _sector_of(data: bytes) -> bytes:
    """Accepte le secteur de 4 Ko, le journal seul (656 octets) ou une sauvegarde complète (≥ 1,1 Mo)."""
    if len(data) >= SECTOR_ADDR + OFFSET + SIZE:
        return data[SECTOR_ADDR:SECTOR_ADDR + 4096]
    if len(data) == 4096:
        return data
    if len(data) == SIZE:
        return bytes(OFFSET) + data
    raise JournalError("Fichier de %d octets : il faut une sauvegarde complète de la mémoire, "
                       "le secteur du logo (4 Ko) ou le journal (656 octets)." % len(data))


def parse(data: bytes) -> Journal:
    s = _sector_of(data)
    j = s[OFFSET:OFFSET + SIZE]
    magic, ver, nrel, nheard, _pad, cks = struct.unpack_from("<IBBBBH", j, 0)
    if magic != MAGIC:
        raise JournalError("Pas de journal F4JCF dans cette mémoire (firmware F4JCF19 ou plus récent, "
                           "écrit en quittant le Menu F4JCF).")
    if ver != 1 or nrel > NREL or nheard > NHEARD:
        raise JournalError("Journal F4JCF de version inconnue (%d)." % ver)
    rel = j[16:16 + NREL * REL_SIZE]
    hd = j[16 + NREL * REL_SIZE:SIZE]
    total = sum(rel[:nrel * REL_SIZE]) + sum(hd[:nheard * HEARD_SIZE])
    if total & 0xFFFF != cks:
        raise JournalError("Journal F4JCF abîmé (somme de contrôle fausse).")
    out = Journal()
    for i in range(nrel):
        peak, sec, slot, ouest, az, lat, lon = struct.unpack_from("<HHBBHII", rel, i * REL_SIZE)
        pos = lat != 0 or lon != 0
        out.releves.append(Releve(
            num=i + 1, dbm=peak // 2 - 160, chrono_s=sec, touche=slot,
            azimut=None if az > 359 else az,
            lat=lat / 10000 if pos else None,
            lon=(-lon if ouest else lon) / 10000 if pos else None))
    for i in range(nheard):
        sid, _t, count, tg, call = struct.unpack_from("<IIHH8s", hd, i * HEARD_SIZE)
        c = call.split(b"\0")[0].decode("ascii", "replace").strip()
        out.stations.append(Station(sid, count, tg, c))
    return out


def save_csv(jr: Journal, path: str) -> None:
    """Deux tableaux dans un même fichier CSV (séparateur « ; », pour Excel / LibreOffice)."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Carnet SATER"])
        w.writerow(["N°", "Niveau max (dBm)", "Chrono", "Touche", "Azimut", "Position"])
        for r in jr.releves:
            w.writerow([r.num, r.dbm, r.chrono, r.touche,
                        "" if r.azimut is None else r.azimut, r.position])
        w.writerow([])
        w.writerow(["Derniers DMR (le plus récent en premier)"])
        w.writerow(["Indicatif", "ID", "Groupe", "Passages"])
        for s in jr.stations:
            w.writerow([s.indicatif, s.id, s.appel, s.passages])


def build(releves: list[tuple], stations: list[tuple]) -> bytes:
    """Journal brut de 656 octets (pour les essais) : relevés (peak, sec, slot, ouest, az, lat, lon),
    stations (id, t, count, tg, call)."""
    rel = b"".join(struct.pack("<HHBBHII", *r) for r in releves).ljust(NREL * REL_SIZE, b"\0")
    hd = b"".join(struct.pack("<IIHH8s", *s) for s in stations).ljust(NHEARD * HEARD_SIZE, b"\0")
    cks = (sum(rel[:len(releves) * REL_SIZE]) + sum(hd[:len(stations) * HEARD_SIZE])) & 0xFFFF
    head = struct.pack("<IBBBBH6x", MAGIC, 1, len(releves), len(stations), 0, cks)
    return head + rel + hd
