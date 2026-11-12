"""Flashage depuis RT4D Programmer (idée n°31) : bibliothèque de firmwares, identification,
contrôles avant le flash et journal.

- La bibliothèque est un dossier de l'ordinateur (par défaut « firmwares » dans le dossier des
  sauvegardes) où l'on range les firmwares officiels Radtel et les versions F4JCF.
  Rien n'est téléchargé ni publié : les firmwares F4JCF contiennent le code Radtel.
- Chaque fichier est identifié par son empreinte SHA-256 (liste des versions connues ci-dessous)
  ou, à défaut, par son contenu.
- Chaque flash est noté dans « journal-firmware.txt » (dossier des sauvegardes).
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import shutil
from dataclasses import dataclass, field

from . import firmware_pkg as FW
from .protocol import FIRMWARE_MAX_SIZE, RadioError

FLASH_BASE = 0x08002800
FLASH_END = 0x08040000
FW_EXT = (".bin", ".zip", ".exe")

# Versions connues : empreinte SHA-256 de l'image flashée (246 Ko) → (nom, contenu)
KNOWN = {
    "b6e13eaa0e8de5472b04d399cf3498315cf7b9ce007df88aa8405dc334bcb151": (
        "F4JCF16 (V3.25, menus FR04)",
        "Écran d'appel DMR : l'appelant (Talker Alias, sinon indicatif et nom du carnet) en 1re ligne, "
        "le nom du groupe en 2e ligne, ensemble pendant tout l'appel ; l'ID passe dans la ligne qui "
        "alterne. Menu Version : « F4JCF16 11/2026 ». Contient F4JCF15."),
    "b029f531289cc8dd2f9330e35117a6bd0e14cb49db568fdf970da0638c2ca6cb": (
        "F4JCF15 (V3.25, menus FR04)",
        "Réglages des modes depuis RT4D Programmer (Radio → Réglages des modes F4JCF) : canaux "
        "SATER, valeurs de départ et indicatif de la balise, période du rappel d'indicatif. "
        "Menu Version : « F4JCF15 11/2026 ». Contient F4JCF14."),
    "a4c0c02904cf7449ebaced9d06419466c26af621dce41d2cd97c59624f10cd56": (
        "F4JCF14 (V3.25, menus FR04)",
        "Talker Alias et niveau du signal DMR sur l'écran d'appel, écran « Trames DMR » "
        "(Menu F4JCF 8). Contient aussi F4JCF10 à F4JCF13."),
}


@dataclass
class FwInfo:
    path: str
    fw: FW.Firmware
    image: bytes                  # image réellement envoyée (complétée à 246 Ko si officielle)
    sha256: str
    kind: str                     # "officiel", "f4jcf", "autre"
    label: str
    details: str = ""
    warnings: list = field(default_factory=list)
    blocking: str = ""            # raison d'un refus (rien ne sera flashé)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _f4jcf_tag(image: bytes) -> str:
    m = re.search(rb"F4JCF ?(\d{2})", image)
    return f"F4JCF{m.group(1).decode()}" if m else ""


def inspect(path: str) -> FwInfo:
    """Charge un firmware et vérifie qu'il peut être flashé. Lève RadioError si illisible."""
    fw = FW.load_firmware(path)
    image = fw.image
    if fw.official and len(image) < FIRMWARE_MAX_SIZE:
        image = bytes(image) + b"\x00" * (FIRMWARE_MAX_SIZE - len(image))
    h = sha256(image)
    warn = []
    blocking = ""
    sp = int.from_bytes(image[0:4], "little")
    reset = int.from_bytes(image[4:8], "little")
    if not (0x20000000 <= sp <= 0x20020000):
        blocking = f"Pointeur de pile 0x{sp:08X} inattendu : ce n'est pas un firmware du RT-4D."
    elif not (FLASH_BASE <= reset < FLASH_END) or not reset & 1:
        blocking = (f"Adresse de démarrage 0x{reset:08X} hors de la zone du firmware "
                    "(0x08002800-0x0803FFFF) : le poste ne démarrerait pas.")
    if h in KNOWN:
        name, details = KNOWN[h]
        kind = "f4jcf" if name.startswith("F4JCF") else "officiel"
        label = f"Firmware {name}"
    elif fw.official:
        kind, label, details = "officiel", fw.label, "Firmware d'origine Radtel, extrait du paquet officiel."
    else:
        tag = _f4jcf_tag(image)
        if tag or b"F4JCF" in image:
            kind = "f4jcf"
            label = f"Firmware {tag or 'F4JCF'} (version non répertoriée)"
            details = "Version F4JCF dont l'empreinte n'est pas dans la liste du logiciel."
            warn.append("Vérifiez que c'est bien la version prévue (nom du fichier, empreinte).")
        else:
            kind, label, details = "autre", fw.label, "Firmware non identifié (REFW ou autre)."
    if kind == "f4jcf" and len(image) != FIRMWARE_MAX_SIZE and not blocking:
        blocking = (f"Une image F4JCF fait exactement {FIRMWARE_MAX_SIZE} octets (246 Ko) ; "
                    f"celle-ci en fait {len(image)} : fichier incomplet ou abîmé.")
    if kind == "autre" and len(image) < FIRMWARE_MAX_SIZE:
        warn.append(f"Image de {len(image)} octets : seule cette partie sera écrite "
                    "(normal pour REFW).")
    return FwInfo(path, fw, image, h, kind, label, details, warn, blocking)


# ───────────────────────────── bibliothèque ─────────────────────────────

def default_dir(backup_dir: str) -> str:
    return os.path.join(backup_dir, "firmwares")


def list_library(folder: str) -> list[str]:
    if not os.path.isdir(folder):
        return []
    out = [os.path.join(folder, n) for n in os.listdir(folder)
           if n.lower().endswith(FW_EXT) and os.path.isfile(os.path.join(folder, n))]
    return sorted(out, key=lambda p: os.path.getmtime(p), reverse=True)


def add_to_library(path: str, folder: str) -> str:
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, os.path.basename(path))
    if os.path.abspath(dest) != os.path.abspath(path):
        if os.path.exists(dest):
            with open(dest, "rb") as a, open(path, "rb") as b:
                if a.read() != b.read():
                    raise RadioError(f"Un autre fichier « {os.path.basename(path)} » existe déjà "
                                     "dans la bibliothèque.")
        else:
            shutil.copy2(path, dest)
    return dest


# ───────────────────────────── journal ─────────────────────────────

JOURNAL = "journal-firmware.txt"


def journal(folder: str, info: FwInfo, port: str, result: str) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, JOURNAL)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {result}\n"
                f"  fichier : {info.path}\n  firmware : {info.label}\n"
                f"  SHA-256 : {info.sha256}\n  port : {port}\n\n")
    return path


def last_full_backup(folder: str):
    """(chemin, âge en jours) de la dernière sauvegarde complète de la flash, ou None."""
    if not os.path.isdir(folder):
        return None
    files = [os.path.join(folder, n) for n in os.listdir(folder)
             if n.startswith(("RT4D-flash-complete-", "RT4D-flash-16Mo-")) and n.endswith(".bin")]
    if not files:
        return None
    p = max(files, key=os.path.getmtime)
    age = (datetime.datetime.now().timestamp() - os.path.getmtime(p)) / 86400
    return p, age
