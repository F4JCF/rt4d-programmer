"""Calibration du RT-4D (firmware officiel V3.25) : lecture, affichage et modification encadrée.

Zone SPI 0x000000, 4 Ko (région d'écriture 0x40). Le firmware en garde une copie à 0x001000 :
- au démarrage (0x0801247C), si l'octet magique 0x9A est absent à 0x0000, il utilise la copie 0x1000,
  et à défaut des valeurs par défaut internes ;
- à la fin d'une session de programmation (commande 0xEE) au cours de laquelle la calibration a été
  écrite, il recopie 0x0000 vers 0x1000 (fonction 0x08004CB0) : la copie de secours prend alors
  la nouvelle calibration.

Organisation (analyse du firmware, fonctions 0x08011140-0x08011214) : en-tête de 32 octets, puis
11 blocs de bandes de 112 octets à 0x20 + bande × 0x70. Dans chaque bloc, des lignes de 16 points
(un point tous les 5 MHz à partir du début de la bande, le 16e servant au-delà) :
  +0x10 polarisation de l'ampli d'émission, puissance haute (registre REG_36 du BK4819, 0-63)
  +0x20 polarisation de l'ampli d'émission, puissance basse
  +0x30 squelch bande large, base du registre REG_4F (bruit)
  +0x40 squelch bande large, base du registre REG_78 (niveau RSSI)
  +0x50 squelch bande étroite, base REG_4F
  +0x60 squelch bande étroite, base REG_78
Les 16 premiers octets de chaque bloc et l'en-tête ne sont pas compris : ils ne sont jamais modifiés.
"""

from __future__ import annotations

import datetime
import hashlib
import os

CAL_REGION = 0x40
CAL_ADDR, CAL_SIZE = 0x000000, 0x1000
CAL_COPY_ADDR = 0x001000
MAGIC = 0x9A
BAND_BASE, BAND_SIZE = 0x20, 0x70
NPOINTS = 16
STEP_MHZ = 5

# (numéro, libellé, MHz de base, MHz de fin) — ordre des blocs dans la zone de calibration
BANDS = [
    (0, "VHF 136-174 MHz", 135, 174),
    (1, "UHF 400-480 MHz", 400, 480),
    (2, "18-64 MHz", 18, 64),
    (3, "64-136 MHz", 64, 136),
    (4, "174-240 MHz", 174, 240),
    (5, "240-320 MHz", 240, 320),
    (6, "320-400 MHz", 320, 400),
    (7, "480-560 MHz", 480, 560),
    (8, "560-660 MHz", 560, 660),
    (9, "840-920 MHz", 840, 920),
    (10, "920-1000 MHz", 920, 1000),
]
# Bandes où le poste émet (radioamateur 144-146 et 430-440 comprises) : seules modifiables
EDITABLE_BANDS = (0, 1)

# (clé, décalage dans le bloc, libellé, min, max, écart maximal autorisé par rapport à l'origine)
ROWS = [
    ("pa_high", 0x10, "Ampli émission — puissance haute (REG_36)", 0, 63, 8),
    ("pa_low", 0x20, "Ampli émission — puissance basse (REG_36)", 0, 63, 8),
    ("sq_w_noise", 0x30, "Squelch large — bruit (REG_4F)", 8, 240, 12),
    ("sq_w_rssi", 0x40, "Squelch large — RSSI (REG_78)", 8, 240, 12),
    ("sq_n_noise", 0x50, "Squelch étroit — bruit (REG_4F)", 8, 240, 12),
    ("sq_n_rssi", 0x60, "Squelch étroit — RSSI (REG_78)", 8, 240, 12),
]
ROW_BY_KEY = {r[0]: r for r in ROWS}

CONFIRM_WORD = "CALIBRATION"


class CalibrationError(ValueError):
    pass


def points(band: int) -> list[tuple[int, int]]:
    """Points utiles d'une bande : (indice, MHz de début de la tranche de 5 MHz)."""
    _, _, base, end = BANDS[band]
    n = min(NPOINTS, (end - base + STEP_MHZ - 1) // STEP_MHZ)
    return [(i, base + i * STEP_MHZ) for i in range(n)]


def point_for(freq_mhz: float, band: int) -> int:
    """Indice du point utilisé par le firmware pour une fréquence (comme 0x08011140)."""
    base = BANDS[band][2]
    return max(0, min(NPOINTS - 1, int((freq_mhz - base) // STEP_MHZ)))


def check(data: bytes) -> str | None:
    """None si la zone ressemble à une calibration RT-4D, sinon la raison."""
    if len(data) != CAL_SIZE:
        return f"taille {len(data)} octets au lieu de {CAL_SIZE}"
    if data[0] != MAGIC:
        return f"octet magique 0x{data[0]:02X} au lieu de 0x{MAGIC:02X} (calibration absente ?)"
    for band in EDITABLE_BANDS:
        for key, off, label, lo, hi, _ in ROWS:
            a = BAND_BASE + band * BAND_SIZE + off
            row = data[a:a + NPOINTS]
            if key.startswith("pa") and max(row) > 63:
                return f"{BANDS[band][1]} : {label} hors plage"
    return None


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Calibration:
    def __init__(self, data: bytes):
        err = check(bytes(data))
        if err:
            raise CalibrationError("Calibration non reconnue : " + err)
        self.original = bytes(data)
        self.data = bytearray(data)

    def _addr(self, band: int, key: str, i: int) -> int:
        if not 0 <= i < NPOINTS:
            raise CalibrationError("point hors plage")
        return BAND_BASE + band * BAND_SIZE + ROW_BY_KEY[key][1] + i

    def get(self, band: int, key: str, i: int) -> int:
        return self.data[self._addr(band, key, i)]

    def get_original(self, band: int, key: str, i: int) -> int:
        return self.original[self._addr(band, key, i)]

    def row(self, band: int, key: str) -> list[int]:
        return [self.get(band, key, i) for i in range(NPOINTS)]

    def set(self, band: int, key: str, i: int, value: int):
        """Modifie une valeur, avec contrôle de plage et d'écart par rapport à l'origine."""
        if band not in EDITABLE_BANDS:
            raise CalibrationError(f"{BANDS[band][1]} : bande non modifiable.")
        _, _, label, lo, hi, dmax = ROW_BY_KEY[key]
        value = int(value)
        if not lo <= value <= hi:
            raise CalibrationError(f"{label} : {value} hors de la plage {lo}-{hi}.")
        orig = self.get_original(band, key, i)
        if abs(value - orig) > dmax:
            raise CalibrationError(f"{label}, point {i} : écart de {value - orig:+d} par rapport à "
                                   f"l'origine ({orig}) ; maximum autorisé ±{dmax}.")
        self.data[self._addr(band, key, i)] = value

    def reset(self):
        self.data = bytearray(self.original)

    def changes(self) -> list[tuple[int, str, int, int, int]]:
        """[(bande, clé, point, ancienne valeur, nouvelle valeur)]"""
        out = []
        for band in EDITABLE_BANDS:
            for key, *_ in ROWS:
                for i in range(NPOINTS):
                    a, b = self.get_original(band, key, i), self.get(band, key, i)
                    if a != b:
                        out.append((band, key, i, a, b))
        return out

    def build(self) -> bytes:
        """Image à écrire. Vérifie que seuls les octets des lignes modifiables ont changé."""
        allowed = {self._addr(b, k, i) for b in EDITABLE_BANDS for k, *_ in ROWS for i in range(NPOINTS)}
        for a in range(CAL_SIZE):
            if self.data[a] != self.original[a] and a not in allowed:
                raise CalibrationError(f"octet 0x{a:03X} modifié hors des tables autorisées")
        out = bytes(self.data)
        err = check(out)
        if err:
            raise CalibrationError(err)
        return out


def confirm_phrase(operator: str) -> str:
    """Phrase que l'opérateur doit recopier pour autoriser l'écriture."""
    return f"{operator.strip().upper()} {CONFIRM_WORD}"


def check_operator(operator: str) -> str | None:
    op = operator.strip()
    if len(op) < 3 or not all(c.isalnum() or c in "-/" for c in op):
        return "indiquez l'indicatif (ou le nom) de l'opérateur : au moins 3 lettres ou chiffres"
    return None


def save_backup(folder: str, data: bytes, tag: str, copy: bytes | None = None) -> str:
    """Enregistre une calibration (4 Ko, et la copie de secours 0x1000 si fournie : 8 Ko)."""
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(folder, f"RT4D-calibration-{tag}-{stamp}.bin")
    with open(path, "wb") as f:
        f.write(bytes(data) + (bytes(copy) if copy is not None else b""))
    return path


def load_file(path: str) -> bytes:
    """Calibration depuis un fichier de sauvegarde (4 Ko, 8 Ko) ou une sauvegarde complète (4 Mo)."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) not in (CAL_SIZE, 2 * CAL_SIZE) and len(data) < 0x2000:
        raise CalibrationError("fichier trop petit pour contenir une calibration")
    data = data[:CAL_SIZE]
    err = check(data)
    if err:
        raise CalibrationError("Ce fichier ne contient pas une calibration du RT-4D : " + err)
    return data


def journal(folder: str, operator: str, changes, before: bytes, after: bytes) -> str:
    """Ajoute l'opération au journal des calibrations (texte)."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "journal-calibration.txt")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [f"{now}  opérateur {operator.strip().upper()}",
             f"  avant {sha(before)[:16]}  après {sha(after)[:16]}"]
    for band, key, i, a, b in changes:
        lines.append(f"  {BANDS[band][1]}  {ROW_BY_KEY[key][2]}  point {i} "
                     f"({points(band)[i][1] if i < len(points(band)) else '-'} MHz) : {a} -> {b}")
    if not changes:
        lines.append("  restauration complète d'une calibration enregistrée")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path
