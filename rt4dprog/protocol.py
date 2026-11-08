"""Protocole série du Radtel RT-4D.

Mode normal (radio allumée normalement) : lecture/écriture de la flash SPI
(codeplug, réglages, sauvegarde complète).
Mode bootloader (radio allumée en maintenant le PTT) : flash du firmware MCU.

Ce module est une réécriture en Python du protocole publié par Dual Tachyon
dans RT-4D-SPIFlash-CLI et radtel-rt-4d-flasher-cli :

    Copyright 2025 Dual Tachyon — https://github.com/DualTachyon
    Licensed under the Apache License, Version 2.0
    http://www.apache.org/licenses/LICENSE-2.0
"""

from __future__ import annotations

import time
from typing import Callable, Optional

ACK = 0x06
BLOCK = 1024
SPI_SIZE_KB = 4096            # la flash SPI lue par l'outil fait 4 Mo
FIRMWARE_MAX_SIZE = 251904    # taille maximale d'une image firmware MCU (246 Ko)

Progress = Optional[Callable[[int, int, str], None]]   # (fait, total, message)
CancelCheck = Optional[Callable[[], bool]]


class RadioError(Exception):
    """Erreur de communication avec la radio (message en français)."""


class Cancelled(RadioError):
    pass


def _sum(data, base: int = 0) -> int:
    s = base
    for b in data:
        s = (s + b) & 0xFF
    return s


def list_ports() -> list[tuple[str, str]]:
    """Liste des ports série : [(nom, description)]."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    out = []
    for p in sorted(lp.comports(), key=lambda p: p.device):
        desc = p.description or ""
        if desc == "n/a":
            desc = ""
        out.append((p.device, desc))
    return out


def _open_serial(port: str, timeout: float, bootloader: bool):
    import serial
    try:
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = 115200
        ser.bytesize = 8
        ser.parity = serial.PARITY_NONE
        ser.stopbits = serial.STOPBITS_ONE
        ser.timeout = timeout
        ser.write_timeout = 2.0
        ser.rtscts = False
        ser.xonxoff = False
        if bootloader:
            ser.dtr = False
        ser.open()
        return ser
    except Exception as e:  # SerialException, PermissionError, ...
        hint = ""
        if "ermission" in str(e):
            hint = ("\n\nSous Linux, ajoutez votre utilisateur au groupe « dialout » "
                    "(sudo usermod -aG dialout $USER) puis fermez/rouvrez la session.")
        raise RadioError(f"Impossible d'ouvrir le port {port} : {e}{hint}") from e


# ─────────────────────────────── Mode normal ───────────────────────────────

class NormalLink:
    """Liaison avec la radio en fonctionnement normal."""

    def __init__(self, ser):
        self.ser = ser

    @classmethod
    def open(cls, port: str) -> "NormalLink":
        return cls(_open_serial(port, timeout=3.0, bootloader=False))

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- bas niveau --
    def _read_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.ser.read(n - len(buf))
            if not chunk:
                break
            buf.extend(chunk)
        return bytes(buf)

    def read_block(self, kb: int) -> Optional[bytes]:
        """Lit 1 Ko de flash SPI à l'adresse kb*1024. None si refus/erreur."""
        cmd = bytearray([0x52, (kb >> 8) & 0xFF, kb & 0xFF, 0])
        cmd[3] = _sum(cmd[:3])
        self.ser.write(bytes(cmd))
        first = self._read_exact(1)
        if not first:
            raise RadioError("La radio ne répond pas. Vérifiez qu'elle est allumée "
                             "normalement, que le câble est bien enfoncé et le bon port choisi.")
        if first[0] == 0xFF:
            return None
        rest = self._read_exact(BLOCK + 3)
        block = first + rest
        if len(block) != BLOCK + 4:
            raise RadioError(f"Réponse incomplète de la radio (bloc 0x{kb:04X}).")
        if _sum(block[:-1]) != block[-1]:
            raise RadioError(f"Somme de contrôle incorrecte (bloc 0x{kb:04X}).")
        return bytes(block[3:3 + BLOCK])

    def detect_mode(self) -> str:
        """'normal' ou 'bootloader'. Lève RadioError si aucune réponse."""
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        data = self.read_block(0)
        if data is None:
            time.sleep(0.05)
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
            return "bootloader"
        return "normal"

    def notify(self) -> bool:
        cmd = bytearray([0x34, 0x00, 0x00, 0x10, 0])
        cmd[4] = _sum(cmd[:4])
        self.ser.write(bytes(cmd))
        r = self._read_exact(1)
        return bool(r) and r[0] == ACK

    def close_session(self):
        """Termine la session (la radio redémarre/rafraîchit ses réglages)."""
        try:
            self.ser.write(bytes([0x34, 0x52, 0x05, 0xEE, 0x79]))
            self.ser.flush()
        except Exception:
            pass

    # -- haut niveau --
    def read(self, address: int, size: int, progress: Progress = None,
             cancel: CancelCheck = None, label: str = "") -> bytes:
        """Lit `size` octets à `address` (multiples de 1 Ko non requis)."""
        first_kb = address // BLOCK
        skip = address % BLOCK
        nblocks = (skip + size + BLOCK - 1) // BLOCK
        out = bytearray()
        for i in range(nblocks):
            if cancel and cancel():
                raise Cancelled("Opération annulée.")
            data = self.read_block(first_kb + i)
            if data is None:
                raise RadioError(f"La radio a refusé la lecture à 0x{(first_kb + i) * BLOCK:06X}.")
            out.extend(data)
            if progress:
                progress(i + 1, nblocks, label)
        return bytes(out[skip:skip + size])

    def write_region(self, region_id: int, data: bytes, progress: Progress = None,
                     cancel: CancelCheck = None, label: str = ""):
        """Écrit une région SPI identifiée par son numéro (0x90, 0x91...)."""
        if len(data) % BLOCK:
            data = bytes(data) + b"\xFF" * (BLOCK - len(data) % BLOCK)
        n = len(data) // BLOCK
        for i in range(n):
            if cancel and cancel():
                raise Cancelled("Opération annulée (la région en cours est partiellement écrite).")
            cmd = bytearray(BLOCK + 4)
            cmd[0] = region_id
            cmd[1] = (i >> 8) & 0xFF
            cmd[2] = i & 0xFF
            cmd[3:3 + BLOCK] = data[i * BLOCK:(i + 1) * BLOCK]
            cmd[-1] = _sum(cmd[:-1])
            self.ser.write(bytes(cmd))
            r = self._read_exact(1)
            if not r:
                raise RadioError(f"Pas d'accusé de réception de la radio ({label}, bloc {i}).")
            if r[0] != ACK:
                raise RadioError(f"Réponse inattendue 0x{r[0]:02X} de la radio ({label}, bloc {i}).")
            if progress:
                progress(i + 1, n, label)


# ─────────────────────────────── Bootloader ───────────────────────────────

class BootLink:
    """Liaison avec le bootloader (radio allumée en maintenant le PTT)."""

    def __init__(self, ser):
        self.ser = ser

    @classmethod
    def open(cls, port: str) -> "BootLink":
        t0 = time.monotonic()
        ser = _open_serial(port, timeout=0.1, bootloader=True)
        if time.monotonic() - t0 > 1.0:
            ser.close()
            raise RadioError("Le port série semble bloqué : débranchez et rebranchez le câble USB.")
        return cls(ser)

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read_byte(self) -> int:
        r = self.ser.read(1)
        return r[0] if r else -1

    def _wait_ack(self, tries: int) -> bool:
        for _ in range(tries):
            b = self._read_byte()
            if b == -1:
                continue
            return b == ACK
        return False

    def probe(self, timeout_s: float = 30.0, cancel: CancelCheck = None) -> bool:
        """Attend que le bootloader réponde 0xFF à nos 0xFF."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if cancel and cancel():
                raise Cancelled("Opération annulée.")
            b = self._read_byte()
            if b == -1:
                try:
                    self.ser.write(b"\xFF")
                except Exception:
                    pass
            elif b == 0xFF:
                return True
        return False

    def _cmd5(self, b1: int, b2: int, b3: int) -> bytes:
        cmd = bytearray([0x39, b1, b2, b3, 0])
        cmd[4] = _sum(cmd[:4], 0x48)
        return bytes(cmd)

    def _send_wait(self, cmd: bytes, tries: int) -> bool:
        self.ser.reset_input_buffer()
        self.ser.write(cmd)
        return self._wait_ack(tries)

    def erase(self, log: Callable[[str], None] = print) -> bool:
        """Efface la flash MCU.

        1) Séquence de l'outil officiel Radtel : 3 × « 39 33 05 10 » puis « 39 33 05 55 ».
        2) Repli : commande « 39 00 00 55 » des outils de Dual Tachyon.
        """
        ok = True
        for step in range(3):
            if not any(self._send_wait(self._cmd5(0x33, 0x05, 0x10), 5) for _ in range(10)):
                ok = False
                break
        if ok:
            log("Liaison établie (procédure officielle). Effacement…")
            if self._send_wait(self._cmd5(0x33, 0x05, 0x55), 400):      # jusqu'à ~40 s
                return True
        log("Procédure officielle sans réponse, essai de la méthode alternative…")
        return self._send_wait(self._cmd5(0x00, 0x00, 0x55), 400)

    def write_chunk(self, offset: int, firmware: bytes) -> bool:
        cmd = bytearray(BLOCK + 4)
        cmd[0] = 0x57
        cmd[1] = (offset >> 8) & 0xFF
        cmd[2] = offset & 0xFF
        chunk = firmware[offset:offset + BLOCK]
        cmd[3:3 + len(chunk)] = chunk
        cmd[-1] = _sum(cmd[:-1], 0x48)
        self.ser.write(bytes(cmd))
        return self._wait_ack(100)          # jusqu'à ~10 s


def check_firmware_file(data: bytes) -> Optional[str]:
    """Retourne un message d'erreur, ou None si le fichier semble acceptable."""
    if not data:
        return "Le fichier est vide."
    if len(data) > FIRMWARE_MAX_SIZE:
        return (f"Fichier trop gros ({len(data)} octets) : un firmware MCU du RT-4D fait "
                f"au plus {FIRMWARE_MAX_SIZE} octets. Ce n'est probablement pas le bon fichier "
                "(un .exe de mise à jour Radtel ne peut pas être utilisé directement).")
    if data[:2] == b"MZ":
        return "C'est un programme Windows (.exe), pas une image firmware .bin."
    if data[:4] in (b"PK\x03\x04", b"Rar!"):
        return "C'est une archive (zip/rar) : décompressez-la et choisissez le fichier .bin."
    # Image ARM Cortex-M : premier mot = pointeur de pile en RAM (0x2000xxxx)
    sp = int.from_bytes(data[0:4], "little")
    if len(data) >= 8 and (sp >> 24) != 0x20:
        return ("Ce fichier ne ressemble pas à une image firmware ARM (vecteur de pile "
                f"0x{sp:08X} inattendu).")
    return None


def flash_firmware(port: str, firmware: bytes, progress: Progress = None,
                   cancel: CancelCheck = None, log: Callable[[str], None] = print,
                   full_image: bool = False):
    """Flashe le firmware MCU. full_image=True : image complète de 246 Ko (firmware officiel)."""
    err = check_firmware_file(firmware)
    if err:
        raise RadioError(err)
    if full_image:
        firmware = bytes(firmware) + b"\x00" * (FIRMWARE_MAX_SIZE - len(firmware))
    with BootLink.open(port) as link:
        log("Recherche du bootloader… (allumez la radio en maintenant le PTT)")
        if not link.probe(cancel=cancel):
            raise RadioError("Le bootloader ne répond pas. Éteignez la radio, maintenez le PTT "
                             "enfoncé et rallumez-la, câble branché, puis réessayez.")
        log("Bootloader détecté. Effacement de la mémoire du firmware… (jusqu'à 40 s)")
        if not link.erase(log):
            raise RadioError("L'effacement a échoué. Rien n'a été écrit ; recommencez la procédure.")
        n = (len(firmware) + BLOCK - 1) // BLOCK
        log(f"Écriture de {len(firmware)} octets ({n} blocs)…")
        for i in range(n):
            if not link.write_chunk(i * BLOCK, firmware):
                raise RadioError(f"Échec d'écriture au bloc {i}/{n}. NE DÉBRANCHEZ PAS : "
                                 "relancez le flash en mode bootloader, il reste disponible.")
            if progress:
                progress(i + 1, n, "Firmware")
        log("Firmware écrit. Éteignez puis rallumez la radio normalement.")
