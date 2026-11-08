"""Opérations de haut niveau avec la radio (lecture, écriture, sauvegarde, restauration)."""

from __future__ import annotations

import datetime
import os
from typing import Callable, Optional

from . import layout as L
from . import legacy as O
from . import stock as S
from .codeplug import Codeplug
from .protocol import (BLOCK, SPI_SIZE_KB, CancelCheck, Cancelled, NormalLink, Progress,
                       RadioError)

Log = Callable[[str], None]

LEGACY_WRITE_MSG = (
    "Ce codeplug vient d'un ANCIEN firmware Radtel (V3.17 ou plus ancien) : l'écriture "
    "directe n'est pas disponible.\n\nPour modifier cette radio :\n"
    "1. Enregistrez ce codeplug (Fichier > Enregistrer sous…).\n"
    "2. Mettez à jour le firmware de la radio (menu Firmware).\n"
    "3. Lisez la radio (Radio > Lire la radio).\n"
    "4. Fichier > Importer un codeplug d'ancien firmware… et choisissez le fichier enregistré.\n"
    "5. Vérifiez puis écrivez dans la radio.")


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
    return link


def _detect_format(rd: "BlockCache") -> tuple[str, int]:
    """('refw', banque) / ('stock', 0x2000) / ('inconnu', 0x2000)."""
    bank, refw = _settings_bank(rd)
    if refw:
        return "refw", bank
    if S.looks_like_stock(rd.read(S.A_CONTACT, 8)):
        return "stock", S.A_CFG
    if O.looks_like_legacy(rd.read(O.A_CONTACT, 8)):
        return "legacy", O.A_CFG
    return "inconnu", bank


def _connect(link: NormalLink, fmt: str, log: Log):
    if fmt == "stock":
        for _ in range(3):
            link.ser.write(S.CONNECT)
            r = link._read_exact(1)
            if r and r[0] == 0x06:
                return
        raise RadioError("La radio n'a pas accepté l'ouverture de la session de programmation.")
    if not link.notify():
        log("Avertissement : la radio n'a pas accusé réception de l'ouverture de session.")


def _read_stock(rd: "BlockCache", progress: Progress, log: Log) -> "S.StockCodeplug":
    image = bytearray(b"\xFF" * S.IMAGE_SIZE)
    n = len(S.READ_BLOCKS)
    log("Lecture du codeplug (firmware officiel Radtel)…")
    for i, kb in enumerate(S.READ_BLOCKS):
        image[kb * BLOCK:(kb + 1) * BLOCK] = rd.read(kb * BLOCK, BLOCK)
        if progress:
            progress(i + 1, n, "Lecture")
    return S.StockCodeplug(bytes(image))


def _read_legacy(rd: "BlockCache", progress: Progress, log: Log) -> "O.LegacyCodeplug":
    image = bytearray(b"\xFF" * O.IMAGE_SIZE)
    blocks = [a // BLOCK + k for a, n in O.READ_RANGES for k in range(n)]
    log("Lecture du codeplug (ancien firmware officiel Radtel, lecture seule)…")
    for i, kb in enumerate(blocks):
        image[kb * BLOCK:(kb + 1) * BLOCK] = rd.read(kb * BLOCK, BLOCK)
        if progress:
            progress(i + 1, len(blocks), "Lecture")
    return O.LegacyCodeplug(bytes(image))


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


def _read_all(link: NormalLink, progress: Progress, cancel: CancelCheck, log: Log,
              connected: bool = False):
    rd = BlockCache(link, cancel)
    fmt, bank = _detect_format(rd)
    log({"refw": "Firmware REFW (beta 41 ou plus) détecté.",
         "stock": "Firmware officiel Radtel (V3.18 ou plus récent) détecté.",
         "legacy": "Ancien firmware officiel Radtel (V3.17 ou plus ancien) détecté : "
                   "lecture seule.",
         "inconnu": "Format NON reconnu (firmware officiel ancien ou REFW ancien) : "
                    "lecture seule."}[fmt])
    if not connected:
        _connect(link, fmt, log)
    if fmt == "stock":
        return _read_stock(rd, progress, log)
    if fmt == "legacy":
        return _read_legacy(rd, progress, log)
    refw = fmt == "refw"
    total = _estimate_blocks()
    done = [0]

    def tick():
        done[0] += 1
        if progress:
            progress(min(done[0], total), total, "Lecture")

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
        rd = BlockCache(link)
        fmt, bank = _detect_format(rd)
        if fmt == "refw":
            return f"Radio détectée en mode normal — firmware REFW (réglages en banque 0x{bank:06X})."
        if fmt == "stock":
            return ("Radio détectée en mode normal — firmware OFFICIEL Radtel (V3.18 ou plus "
                    "récent). Lecture et écriture disponibles.")
        if fmt == "legacy":
            return ("Radio détectée en mode normal — ANCIEN firmware officiel Radtel (V3.17 ou "
                    "plus ancien). Lecture disponible ; pour modifier la radio, mettez à jour "
                    "son firmware puis importez l'ancien codeplug (menu Fichier).")
        return ("Radio détectée en mode normal — format non reconnu (firmware ancien ?). "
                "Lecture et sauvegarde complète possibles, écriture du codeplug bloquée.")
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
    if getattr(cp, "kind", "refw") == "legacy":
        raise RadioError(LEGACY_WRITE_MSG)
    if getattr(cp, "kind", "refw") == "stock":
        return _write_stock(port, cp, backup_dir, progress, cancel, log)
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
        if getattr(before, "kind", "") != "refw" or not before.is_refw:
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


def _write_stock(port: str, cp: "S.StockCodeplug", backup_dir: str, progress: Progress,
                 cancel: CancelCheck, log: Log) -> str:
    """Écriture au firmware officiel : même séquence que le CPS Radtel V2.07."""
    problems = cp.validate()
    if problems:
        raise RadioError("Le codeplug contient des erreurs :\n• " + "\n• ".join(problems[:15]))
    data = bytearray(cp.build())
    link = _open_normal(port, log)
    try:
        rd = BlockCache(link, cancel)
        fmt, _ = _detect_format(rd)
        if fmt != "stock":
            raise RadioError("La radio ne contient pas le firmware officiel Radtel V3.18+ "
                             "attendu pour ce codeplug : écriture annulée.")
        _connect(link, "stock", log)
        log("Sauvegarde du contenu actuel de la radio avant écriture…")
        before = _read_stock(rd, progress, log)
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = os.path.join(backup_dir, f"RT4D-avant-ecriture-{stamp}.rt4d")
        with open(backup_path, "wb") as f:
            f.write(bytes(before.image))
        log(f"Sauvegarde enregistrée : {backup_path}")

        # Comme le CPS : relire les blocs 0 et 8, garder mot de passe et fin des réglages
        if link.read_block(0) is None:
            raise RadioError("La radio a refusé la lecture du bloc 0.")
        live = link.read_block(8)
        if live is None:
            raise RadioError("La radio a refusé la lecture des réglages.")
        cfg = S.A_CFG
        data[cfg:cfg + 12] = live[0:12]
        data[cfg + 14:cfg + 16] = b"\xFF\xFF"
        data[cfg + 960:cfg + 1024] = live[960:1024]

        total = sum(n for _, _, n, _ in S.WRITE_REGIONS)
        done = [0]

        def prog(i, n, label):
            if progress:
                progress(done[0] + i, total, label)

        for region, addr, nblocks, label in S.WRITE_REGIONS:
            log(f"Écriture : {label}…")
            link.write_region(region, bytes(data[addr:addr + nblocks * BLOCK]), prog, cancel, label)
            done[0] += nblocks
        link.ser.write(S.END)
        log("Écriture terminée. La radio redémarre.")
        return backup_path
    finally:
        link.close()


def load_any(path: str):
    """Ouvre un .4rdmf (REFW), une image .rt4d (officiel) ou une sauvegarde complète .bin."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) in (L.FILE_SIZE, L.FILE_SIZE_LEGACY):
        return Codeplug(data)
    if len(data) >= S.IMAGE_SIZE and S.looks_like_stock(data[S.A_CONTACT:S.A_CONTACT + 5]):
        return S.StockCodeplug(data[:S.IMAGE_SIZE])
    if len(data) >= O.IMAGE_SIZE and O.looks_like_legacy(data[O.A_CONTACT:O.A_CONTACT + 6]):
        return O.LegacyCodeplug(data[:O.IMAGE_SIZE])
    if len(data) == SPI_SIZE_KB * BLOCK:
        raise RadioError("Cette sauvegarde complète ne vient pas d'un firmware officiel V3.18+ : "
                         "elle sert uniquement à restaurer la radio (menu Radio).")
    raise RadioError(f"Format de fichier non reconnu ({len(data)} octets).")
