"""Firmwares du RT-4D : fichiers .bin et outils de mise à jour officiels Radtel.

Les mises à jour Radtel (« RT-4D Upgrade Vx_yy_aammjj.exe ») contiennent le
firmware en clair, au format Intel HEX (adresse de base 0x08002800). On l'extrait
sans jamais exécuter le programme Radtel.
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from dataclasses import dataclass

from .protocol import FIRMWARE_MAX_SIZE, RadioError, check_firmware_file

RADTEL_DOWNLOAD_PAGE = "https://www.radtels.com/pages/software-download"
FLASH_BASE = 0x08002800
HEX_MARKER = ":020000040800".encode("utf-16-le")
HEX_CHARS = set("0123456789ABCDEFabcdef:")


@dataclass
class Firmware:
    image: bytes            # image à flasher
    label: str              # description lisible
    official: bool          # True = firmware officiel Radtel (image complète 246 Ko)
    used_bytes: int


def _version_from_name(name: str) -> str:
    m = re.search(r"V(\d+)_(\d+)(?:_SLOW)?_(\d{6})", name, re.I)
    if m:
        return f"V{m.group(1)}.{m.group(2)} ({m.group(3)})"
    return ""


def extract_hex_from_exe(data: bytes) -> str:
    i = data.find(HEX_MARKER)
    if i < 0:
        raise RadioError("Aucun firmware trouvé dans ce programme : ce n'est pas un outil de "
                         "mise à jour « RT-4D Upgrade » de Radtel.")
    chars = []
    while i + 1 < len(data):
        c = chr(data[i] | (data[i + 1] << 8))
        if c not in HEX_CHARS:
            break
        chars.append(c)
        i += 2
    return "".join(chars)


def hex_to_image(text: str) -> tuple[bytes, int]:
    """Convertit l'Intel HEX en image binaire (comme l'outil officiel, remplissage par 0)."""
    img = bytearray(FIRMWARE_MAX_SIZE)
    upper = 0
    used = 0
    eof = False
    for rec in text.split(":"):
        if not rec:
            continue
        try:
            b = bytes.fromhex(rec)
        except ValueError as e:
            raise RadioError("Firmware illisible (enregistrement HEX invalide).") from e
        if len(b) < 5 or len(b) != b[0] + 5 or sum(b) & 0xFF:
            raise RadioError("Firmware endommagé (somme de contrôle HEX incorrecte).")
        n, addr, typ = b[0], (b[1] << 8) | b[2], b[3]
        if typ == 0:
            a = (upper << 16) + addr - FLASH_BASE
            if a < 0 or a + n > FIRMWARE_MAX_SIZE:
                raise RadioError(f"Adresse 0x{(upper << 16) + addr:08X} hors de la zone du firmware.")
            img[a:a + n] = b[4:4 + n]
            used = max(used, a + n)
        elif typ == 4:
            upper = (b[4] << 8) | b[5]
        elif typ == 1:
            eof = True
            break
    if not eof or not used:
        raise RadioError("Firmware incomplet (fin du fichier HEX absente).")
    return bytes(img), used


def _from_exe(data: bytes, name: str) -> Firmware:
    image, used = hex_to_image(extract_hex_from_exe(data))
    err = check_firmware_file(image)
    if err:
        raise RadioError(err)
    reset = int.from_bytes(image[4:8], "little")
    if not (FLASH_BASE <= reset < FLASH_BASE + FIRMWARE_MAX_SIZE):
        raise RadioError("Vecteur de démarrage inattendu : firmware refusé par précaution.")
    ver = _version_from_name(name)
    return Firmware(image, f"Firmware officiel Radtel {ver}".strip(), True, used)


def load_firmware(path: str) -> Firmware:
    name = os.path.basename(path)
    low = name.lower()
    if low.endswith(".rar"):
        raise RadioError("Les fichiers .rar ne peuvent pas être lus directement. Décompressez-le "
                         "(clic droit → « Extraire tout »), puis choisissez le fichier "
                         "« RT-4D Upgrade Vx_xx_xxxxxx.zip » ou le .exe qu'il contient.")
    with open(path, "rb") as f:
        data = f.read()
    if low.endswith(".zip") or data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            exes = [n for n in z.namelist() if n.lower().endswith(".exe")
                    and "upgrade" in os.path.basename(n).lower()
                    and "dmr" not in os.path.basename(n).lower()]
            if not exes:
                raise RadioError("Cette archive ne contient pas d'outil « RT-4D Upgrade ». "
                                 "Choisissez le zip « RT-4D Upgrade Vx_xx_xxxxxx.zip ».")
            exes.sort(key=lambda n: "slow" in n.lower())     # version normale en priorité
            inner = exes[0]
            return _from_exe(z.read(inner), os.path.basename(inner))
    if data[:2] == b"MZ":
        return _from_exe(data, name)
    err = check_firmware_file(data)
    if err:
        raise RadioError(err)
    return Firmware(data, f"Fichier firmware {name}", False, len(data))
