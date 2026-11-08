"""Opérations de haut niveau avec la radio (lecture, écriture, sauvegarde, restauration)."""

from __future__ import annotations

import datetime
import os
from typing import Callable, Optional

from . import layout as L
from .codeplug import Codeplug
from .protocol import (BLOCK, SPI_SIZE_KB, CancelCheck, Cancelled, NormalLink, Progress,
                       RadioError)

Log = Callable[[str], None]


class BlockCache:
    """Lecture SPI avec cache par bloc de 1 Ko."""

    def __init__(self, link: NormalLink, cancel: CancelCheck = None):
        self.link = link
        self.cache: dict[int, bytes] = {}
        self.cancel = cancel

    def read(self, address: int, size: int, tick: Optional[Callable[[], None]] = None) -> bytes:
        out = bytearray()
        first = address // BLOCK
        last = (address + size - 1) // BLOCK
        for kb in range(first, last + 1):
            if self.cancel and self.cancel():
                raise Cancelled("Opération annulée.")
            if kb not in self.cache:
                data = self.link.read_block(kb)
                if data is None:
                    raise RadioError(f"La radio a refusé la lecture à 0x{kb * BLOCK:06X}.")
                self.cache[kb] = data
                if tick:
                    tick()
            out.extend(self.cache[kb])
        skip = address - first * BLOCK
        return bytes(out[skip:skip + size])


def _open_normal(port: str, log: Log) -> NormalLink:
    link = NormalLink.open(port)
    try:
        mode = link.detect_mode()
    except Exception:
        link.close()
        raise
    if mode == "bootloader":
        link.close()
        raise RadioError("La radio est en mode bootloader (allumée avec le PTT enfoncé). "
                         "Rallumez-la normalement pour lire ou écrire le codeplug.")
    if not link.notify():
        log("Avertissement : la radio n'a pas accusé réception de l'ouverture de session.")
    return link


def _settings_bank(rd: BlockCache) -> tuple[int, bool]:
    for base in L.SETTINGS_BANKS:
        if rd.read(base + 0xFFC, 4) == L.MAGIC:
            return base, True
    return L.SETTINGS_BANKS[0], False


def _zone_page_source(marker_a: bytes, marker_b: bytes) -> str:
    a_pristine = marker_a == L.ZONE_MARKER_PRISTINE
    b_dtcn = marker_b[4:] == L.MAGIC
    if b_dtcn and not (a_pristine and marker_b != L.ZONE_MARKER_PRISTINE):
        return "B"
    return "A"


def _read_zones(rd: BlockCache, tick) -> bytes:
    base = L.SECTIONS["zones"][2]
    size = L.SECTIONS["zones"][1]
    pages = []
    for addr in range(base, base + size, L.ZONE_PAGE):
        ma = rd.read(addr + L.ZONE_MARKER_OFF, 8, tick)
        mb = rd.read(addr + L.ZONE_BANK_B + L.ZONE_MARKER_OFF, 8, tick)
        src = addr + (L.ZONE_BANK_B if _zone_page_source(ma, mb) == "B" else 0)
        pages.append(rd.read(src, L.ZONE_PAGE, tick))
    return b"".join(pages)


def _estimate_blocks() -> int:
    n = 0
    for name in L.READ_SECTIONS:
        size = L.SECTIONS[name][1]
        n += (size + BLOCK - 1) // BLOCK
    n += 2 + 2 * (L.SECTIONS["zones"][1] // L.ZONE_PAGE)   # marqueurs
    return n


def _read_all(link: NormalLink, progress: Progress, cancel: CancelCheck, log: Log) -> Codeplug:
    rd = BlockCache(link, cancel)
    total = _estimate_blocks()
    done = [0]

    def tick():
        done[0] += 1
        if progress:
            progress(min(done[0], total), total, "Lecture")

    bank, refw = _settings_bank(rd)
    log(f"Réglages trouvés à 0x{bank:06X} — "
        + ("firmware REFW (beta 41 ou plus) détecté." if refw else
           "format REFW NON détecté (firmware officiel Radtel ou REFW ancien)."))
    image = bytearray(b"\xFF" * L.FILE_SIZE)
    for name in L.READ_SECTIONS:
        off, size, spi, _ = L.SECTIONS[name]
        log(f"Lecture : {L.SECTION_LABELS[name]}…")
        if name == "settings":
            data = rd.read(bank, size, tick)
        elif name == "zones" and refw:
            data = _read_zones(rd, tick)
        else:
            data = rd.read(spi, size, tick)
        image[off:off + size] = data
    if progress:
        progress(total, total, "Lecture")
    return Codeplug(bytes(image))


def identify(port: str, log: Log = print) -> str:
    """Teste la liaison et retourne un résumé lisible."""
    link = NormalLink.open(port)
    try:
        mode = link.detect_mode()
        if mode == "bootloader":
            return "Radio détectée en MODE BOOTLOADER (prête pour un flash de firmware)."
        link.notify()
        rd = BlockCache(link)
        bank, refw = _settings_bank(rd)
        link.close_session()
        if refw:
            return f"Radio détectée en mode normal — firmware REFW (réglages en banque 0x{bank:06X})."
        return ("Radio détectée en mode normal — firmware REFW non détecté "
                "(firmware officiel Radtel ?). Lecture et sauvegarde possibles, "
                "écriture du codeplug bloquée.")
    finally:
        link.close()


def read_codeplug(port: str, progress: Progress = None, cancel: CancelCheck = None,
                  log: Log = print) -> Codeplug:
    link = _open_normal(port, log)
    try:
        cp = _read_all(link, progress, cancel, log)
        link.close_session()
        return cp
    finally:
        link.close()


def write_codeplug(port: str, cp: Codeplug, sections: list[str], backup_dir: str,
                   progress: Progress = None, cancel: CancelCheck = None, log: Log = print) -> str:
    """Écrit les sections choisies. Sauvegarde d'abord le contenu actuel de la radio.

    Retourne le chemin du fichier de sauvegarde créé.
    """
    for s in sections:
        if s not in L.EDITABLE_SECTIONS:
            raise RadioError(f"Section non modifiable : {s}")
    if "settings" in sections and not (cp.has_settings and cp.is_refw):
        raise RadioError("Les réglages de ce codeplug ne sont pas au format REFW : "
                         "lisez d'abord la radio pour obtenir des réglages valides.")
    problems = cp.validate()
    if problems:
        raise RadioError("Le codeplug contient des erreurs :\n• " + "\n• ".join(problems[:15]))

    link = _open_normal(port, log)
    try:
        log("Sauvegarde du contenu actuel de la radio avant écriture…")
        before = _read_all(link, progress, cancel, log)
        if not before.is_refw:
            raise RadioError("Le firmware REFW n'est pas détecté sur la radio : écriture annulée "
                             "pour ne pas risquer de corrompre un format inconnu. Flashez d'abord "
                             "le firmware REFW (menu Firmware).")
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = os.path.join(backup_dir, f"RT4D-avant-ecriture-{stamp}.4rdmf")
        with open(backup_path, "wb") as f:
            f.write(bytes(before.image))
        log(f"Sauvegarde enregistrée : {backup_path}")

        data = cp.build()
        order = [s for s in L.EDITABLE_SECTIONS if s in sections]
        total = sum(L.SECTIONS[s][1] // BLOCK for s in order)
        done = [0]

        def prog(i, n, label):
            if progress:
                progress(done[0] + i, total, label)

        for s in order:
            off, size, _, region = L.SECTIONS[s]
            log(f"Écriture : {L.SECTION_LABELS[s]}…")
            link.write_region(region, data[off:off + size], prog, cancel, L.SECTION_LABELS[s])
            done[0] += size // BLOCK
        link.close_session()
        log("Écriture terminée. La radio redémarre ou recharge sa configuration.")
        return backup_path
    finally:
        link.close()


def backup_spi(port: str, path: str, progress: Progress = None, cancel: CancelCheck = None,
               log: Log = print):
    """Sauvegarde complète de la flash SPI (4 Mo, environ 6 minutes)."""
    link = _open_normal(port, log)
    tmp = path + ".partiel"
    try:
        with open(tmp, "wb") as f:
            for kb in range(SPI_SIZE_KB):
                if cancel and cancel():
                    raise Cancelled("Sauvegarde annulée.")
                data = link.read_block(kb)
                if data is None:
                    raise RadioError(f"Lecture refusée à 0x{kb * BLOCK:06X}.")
                f.write(data)
                if progress:
                    progress(kb + 1, SPI_SIZE_KB, "Sauvegarde")
        link.close_session()
        os.replace(tmp, path)
        log(f"Sauvegarde complète enregistrée : {path}")
    finally:
        link.close()
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def restore_regions_from_dump(dump: bytes, include_calibration: bool) -> list[tuple[int, str, bytes]]:
    """Prépare les régions à réécrire à partir d'une sauvegarde 4 Mo."""
    if len(dump) != SPI_SIZE_KB * BLOCK:
        raise RadioError(f"Le fichier ne fait pas 4 Mo ({len(dump)} octets) : "
                         "ce n'est pas une sauvegarde complète du RT-4D.")
    bank = L.SETTINGS_BANKS[0]
    for b in L.SETTINGS_BANKS:
        if dump[b + 0xFFC:b + 0x1000] == L.MAGIC:
            bank = b
            break
    out = []
    for region, addr, size, name in L.SPI_RESTORE_REGIONS:
        if region == 0x40 and not include_calibration:
            continue
        if region == 0x90:
            data = dump[bank:bank + size]
        elif region == 0x92 and dump[bank + 0xFFC:bank + 0x1000] == L.MAGIC:
            pages = []
            for a in range(addr, addr + size, L.ZONE_PAGE):
                ma = dump[a + L.ZONE_MARKER_OFF:a + L.ZONE_MARKER_OFF + 8]
                mb = dump[a + L.ZONE_BANK_B + L.ZONE_MARKER_OFF:a + L.ZONE_BANK_B + L.ZONE_MARKER_OFF + 8]
                src = a + (L.ZONE_BANK_B if _zone_page_source(ma, mb) == "B" else 0)
                pages.append(dump[src:src + L.ZONE_PAGE])
            data = b"".join(pages)
        else:
            data = dump[addr:addr + size]
        out.append((region, name, data))
    return out


def restore_spi(port: str, dump: bytes, include_calibration: bool, progress: Progress = None,
                cancel: CancelCheck = None, log: Log = print):
    regions = restore_regions_from_dump(dump, include_calibration)
    link = _open_normal(port, log)
    try:
        total = sum(len(d) // BLOCK for _, _, d in regions)
        done = [0]

        def prog(i, n, label):
            if progress:
                progress(done[0] + i, total, label)

        for region, name, data in regions:
            log(f"Restauration : {name}…")
            link.write_region(region, data, prog, cancel, name)
            done[0] += len(data) // BLOCK
        link.close_session()
        log("Restauration terminée.")
    finally:
        link.close()
