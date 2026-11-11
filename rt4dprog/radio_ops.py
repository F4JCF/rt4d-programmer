"""Opérations de haut niveau avec la radio (lecture, écriture, sauvegarde, restauration)."""

from __future__ import annotations

import datetime
import os
from typing import Callable, Optional

from . import layout as L
from . import legacy as O
from . import stock as S
from .codeplug import Codeplug
from .protocol import (BLOCK, SPI_SIZE_KB, SPI_FULL_KB, CancelCheck, Cancelled, NormalLink, Progress,
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
    if fmt in ("stock", "legacy"):
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
    log("Lecture du codeplug (ancien firmware officiel Radtel)…")
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
         "legacy": "Ancien firmware officiel Radtel (V3.17 ou plus ancien) détecté.",
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
                    "plus ancien). Lecture et écriture disponibles.")
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
        return _write_legacy(port, cp, backup_dir, progress, cancel, log)
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
               log: Log = print, size_kb: int = SPI_SIZE_KB):
    """Sauvegarde de la flash SPI, en lecture seule : 4 Mo (environ 6 minutes) par défaut,
    ou 16 Mo, toute la mémoire (size_kb=SPI_FULL_KB, environ 25 minutes)."""
    link = _open_normal(port, log)
    tmp = path + ".partiel"
    try:
        with open(tmp, "wb") as f:
            for kb in range(size_kb):
                if cancel and cancel():
                    raise Cancelled("Sauvegarde annulée.")
                data = link.read_block(kb)
                if data is None:
                    raise RadioError(f"Lecture refusée à 0x{kb * BLOCK:06X}.")
                f.write(data)
                if progress:
                    progress(kb + 1, size_kb, "Sauvegarde")
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


def dump_4mb(dump: bytes) -> bytes:
    """Une sauvegarde de 16 Mo commence par la sauvegarde de 4 Mo : on ne garde que celle-ci."""
    return dump[:SPI_SIZE_KB * BLOCK] if len(dump) == SPI_FULL_KB * BLOCK else dump


def dump_format(dump: bytes) -> str:
    """Firmware d'origine d'une sauvegarde complète : 'refw', 'stock', 'legacy' ou 'inconnu'."""
    if any(dump[b + 0xFFC:b + 0x1000] == L.MAGIC for b in L.SETTINGS_BANKS):
        return "refw"
    if S.looks_like_stock(dump[S.A_CONTACT:S.A_CONTACT + 8]):
        return "stock"
    if O.looks_like_legacy(dump[O.A_CONTACT:O.A_CONTACT + 8]):
        return "legacy"
    return "inconnu"


def restore_regions_from_dump(dump: bytes, include_calibration: bool,
                              fmt: Optional[str] = None) -> list[tuple[int, str, bytes]]:
    """Prépare les régions à réécrire à partir d'une sauvegarde 4 Mo.
    fmt : firmware de la radio ('refw' ou 'stock'). Chaque firmware a sa propre carte des régions."""
    dump = dump_4mb(dump)
    if len(dump) != SPI_SIZE_KB * BLOCK:
        raise RadioError(f"Le fichier ne fait ni 4 ni 16 Mo ({len(dump)} octets) : "
                         "ce n'est pas une sauvegarde complète du RT-4D.")
    src = dump_format(dump)
    fmt = fmt or src
    if fmt not in ("refw", "stock"):
        raise RadioError("La restauration complète n'est possible qu'avec le firmware REFW ou le "
                         "firmware officiel Radtel V3.18 ou plus récent.")
    if src != fmt:
        names = {"refw": "REFW", "stock": "officiel Radtel V3.18+", "legacy": "officiel ancien (V3.17 ou avant)",
                 "inconnu": "inconnu"}
        raise RadioError(f"Cette sauvegarde vient d'un firmware {names[src]}, mais la radio contient "
                         f"le firmware {names[fmt]}. Les deux n'utilisent pas la même organisation de "
                         "la mémoire : restauration refusée. Remettez d'abord le même firmware.")
    if fmt == "stock":
        return [(region, name, dump[addr:addr + size]) for region, addr, size, name in S.RESTORE_REGIONS
                if include_calibration or region != 0x40]
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
    link = _open_normal(port, log)
    try:
        fmt, _ = _detect_format(BlockCache(link, cancel))
        regions = restore_regions_from_dump(dump, include_calibration, fmt)
        _connect(link, fmt, log)
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


def _write_legacy(port: str, cp: "O.LegacyCodeplug", backup_dir: str, progress: Progress,
                  cancel: CancelCheck, log: Log) -> str:
    """Écriture à l'ancien firmware officiel : même séquence que le CPS Radtel V1.19."""
    problems = cp.validate()
    if problems:
        raise RadioError("Le codeplug contient des erreurs :\n• " + "\n• ".join(problems[:15]))
    link = _open_normal(port, log)
    try:
        rd = BlockCache(link, cancel)
        fmt, _ = _detect_format(rd)
        if fmt != "legacy":
            raise RadioError("La radio ne contient pas l'ancien firmware officiel Radtel attendu "
                             "pour ce codeplug : écriture annulée.")
        _connect(link, "legacy", log)
        log("Sauvegarde du contenu actuel de la radio avant écriture…")
        before = _read_legacy(rd, progress, log)
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = os.path.join(backup_dir, f"RT4D-avant-ecriture-{stamp}.rt4d")
        with open(backup_path, "wb") as f:
            f.write(bytes(before.image))
        log(f"Sauvegarde enregistrée : {backup_path}")

        # les parties non gérées ici (chiffrement, SMS, FM) sont reprises de la radio
        data = bytearray(cp.build())
        for region, addr, nblocks, _ in O.WRITE_REGIONS:
            if region in (0x95, 0x97, 0x99):
                data[addr:addr + nblocks * BLOCK] = before.image[addr:addr + nblocks * BLOCK]

        # Comme le CPS : relire le bloc 8, garder le mot de passe de la radio
        live = link.read_block(8)
        if live is None:
            raise RadioError("La radio a refusé la lecture des réglages.")
        cfg = O.A_CFG
        data[cfg:cfg + 12] = live[0:12]
        data[cfg + 14:cfg + 16] = b"\xFF\xFF"

        total = sum(n for _, _, n, _ in O.WRITE_REGIONS)
        done = [0]

        def prog(i, n, label):
            if progress:
                progress(done[0] + i, total, label)

        for region, addr, nblocks, label in O.WRITE_REGIONS:
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
    if len(data) in (SPI_SIZE_KB * BLOCK, SPI_FULL_KB * BLOCK):
        raise RadioError("Cette sauvegarde complète ne vient pas d'un firmware officiel V3.18+ : "
                         "elle sert uniquement à restaurer la radio (menu Radio).")
    raise RadioError(f"Format de fichier non reconnu ({len(data)} octets).")


# ───────────────────── police ASCII (firmware officiel) ─────────────────────

def read_font(port: str, progress: Progress = None, cancel: CancelCheck = None,
              log: Log = print) -> bytes:
    """Lit la police ASCII (8 Ko) d'une radio au firmware officiel V3.18+."""
    from . import fontpatch as P
    link = _open_normal(port, log)
    try:
        rd = BlockCache(link, cancel)
        fmt, _ = _detect_format(rd)
        if fmt != "stock":
            raise RadioError("La police avec accents n'est prévue que pour le firmware officiel "
                             "Radtel V3.18 ou plus récent.")
        font = rd.read(S.FONT_ADDR, S.FONT_SIZE)
        link.close_session()
    finally:
        link.close()
    err = P.check_font(font)
    if err:
        raise RadioError("Police de la radio non reconnue : " + err)
    return font


def write_font(port: str, font: bytes, backup_dir: str, progress: Progress = None,
               cancel: CancelCheck = None, log: Log = print) -> str:
    """Écrit une police ASCII de 8 Ko (région 0xA0). L'ancienne police est d'abord enregistrée
    dans backup_dir. La police écrite est relue et comparée. Retourne le chemin de la sauvegarde."""
    from . import fontpatch as P
    err = P.check_font(font)
    if err:
        raise RadioError("Police refusée : " + err)
    link = _open_normal(port, log)
    try:
        rd = BlockCache(link, cancel)
        fmt, _ = _detect_format(rd)
        if fmt != "stock":
            raise RadioError("La radio ne contient pas le firmware officiel Radtel V3.18+ : "
                             "écriture de la police annulée.")
        _connect(link, fmt, log)
        log("Lecture de la police actuelle…")
        old = rd.read(S.FONT_ADDR, S.FONT_SIZE)
        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bk = os.path.join(backup_dir, f"RT4D-police-avant-{stamp}.bin")
        with open(bk, "wb") as f:
            f.write(old)
        log(f"Police actuelle enregistrée : {bk}")
        if old == font:
            log("La radio contient déjà cette police : rien à écrire.")
            link.close_session()
            return bk
        log("Écriture de la police (8 blocs)…")
        link.write_region(S.FONT_REGION, font, progress, cancel, "Police")
        log("Relecture de contrôle…")
        back = BlockCache(link).read(S.FONT_ADDR, S.FONT_SIZE)
        if back != font:
            raise RadioError("La police relue ne correspond pas à la police envoyée. "
                             f"Recommencez, ou remettez la police d'origine ({bk}).")
        link.close_session()
        log("Police écrite et vérifiée.")
        return bk
    finally:
        link.close()


def font_from_file(path: str) -> bytes:
    """Police de 8 Ko depuis une sauvegarde de police (8 Ko) ou une sauvegarde complète (4 Mo)."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) in (SPI_SIZE_KB * BLOCK, SPI_FULL_KB * BLOCK):
        data = data[S.FONT_ADDR:S.FONT_ADDR + S.FONT_SIZE]
    from . import fontpatch as P
    err = P.check_font(data)
    if err:
        raise RadioError(f"Ce fichier ne contient pas une police du RT-4D : {err}")
    return data


# ─────────────────────────────── Calibration ───────────────────────────────

def read_calibration(port: str, progress: Progress = None, cancel: CancelCheck = None,
                     log: Log = print) -> tuple[bytes, bytes]:
    """Lit la calibration (0x0000, 4 Ko) et sa copie de secours (0x1000). Lecture seule."""
    from . import calibration as C
    link = _open_normal(port, log)
    try:
        rd = BlockCache(link, cancel)
        fmt, _ = _detect_format(rd)
        if fmt != "stock":
            raise RadioError("La calibration n'est prise en charge qu'avec le firmware officiel "
                             "Radtel V3.18+ (ou le firmware F4JCF qui en dérive).")
        cal = rd.read(C.CAL_ADDR, C.CAL_SIZE, lambda: progress and progress(len(rd.cache), 8, "Calibration"))
        copy = rd.read(C.CAL_COPY_ADDR, C.CAL_SIZE, lambda: progress and progress(len(rd.cache), 8, "Calibration"))
        link.close_session()
    finally:
        link.close()
    return cal, copy


def write_calibration(port: str, new: bytes, expected: bytes, operator: str, backup_dir: str,
                      progress: Progress = None, cancel: CancelCheck = None, log: Log = print,
                      changes=None) -> str:
    """Écrit une calibration (région 0x40) après validation de l'opérateur (faite par l'appelant).
    Garde-fous : la calibration présente dans la radio doit être celle à partir de laquelle les
    modifications ont été faites (`expected`) ; la calibration et la copie de secours de la radio
    sont enregistrées sur l'ordinateur avant toute écriture ; relecture et comparaison après
    écriture ; journal des opérations. Retourne le chemin de la sauvegarde."""
    from . import calibration as C
    err = C.check(new) or C.check_operator(operator)
    if err:
        raise RadioError("Écriture de la calibration refusée : " + err)
    link = _open_normal(port, log)
    try:
        rd = BlockCache(link, cancel)
        fmt, _ = _detect_format(rd)
        if fmt != "stock":
            raise RadioError("La radio ne contient pas le firmware officiel Radtel V3.18+ : "
                             "écriture de la calibration annulée.")
        _connect(link, fmt, log)
        log("Lecture de la calibration actuelle de la radio…")
        cur = rd.read(C.CAL_ADDR, C.CAL_SIZE)
        copy = rd.read(C.CAL_COPY_ADDR, C.CAL_SIZE)
        bk = C.save_backup(backup_dir, cur, "avant", copy)
        log(f"Calibration actuelle enregistrée : {bk}")
        if cur != expected:
            raise RadioError("La calibration de la radio n'est plus celle qui a été lue et modifiée "
                             "(autre radio, ou modifiée entre-temps). Relisez la calibration. "
                             "Rien n'a été écrit.")
        if cur == new:
            log("La radio contient déjà cette calibration : rien à écrire.")
            link.close_session()
            return bk
        log("Écriture de la calibration (4 blocs)…")
        link.write_region(C.CAL_REGION, new, progress, cancel, "Calibration")
        log("Relecture de contrôle…")
        back = BlockCache(link).read(C.CAL_ADDR, C.CAL_SIZE)
        if back != new:
            raise RadioError("La calibration relue ne correspond pas à celle envoyée. NE PAS "
                             "éteindre la radio : recommencez l'écriture, ou restaurez la "
                             f"sauvegarde {bk}.")
        link.close_session()
        C.journal(backup_dir, operator, changes if changes is not None else [], cur, new)
        log("Calibration écrite et vérifiée. La radio l'utilise après redémarrage.")
        return bk
    finally:
        link.close()


# ─────────────────────── Réglages des modes F4JCF (F4JCF15) ───────────────────────

def read_f4set(port: str, progress: Progress = None, cancel: CancelCheck = None,
               log: Log = print) -> bytes:
    """Lit le secteur du logo (0x100000, 4 Ko) qui contient les réglages des modes F4JCF."""
    from . import f4set as FS
    link = _open_normal(port, log)
    try:
        rd = BlockCache(link, cancel)
        fmt, _ = _detect_format(rd)
        if fmt != "stock":
            raise RadioError("Les réglages des modes F4JCF demandent le firmware F4JCF "
                             "(dérivé du firmware officiel Radtel V3.25).")
        sector = rd.read(FS.SECTOR_ADDR, FS.SECTOR_SIZE,
                         lambda: progress and progress(len(rd.cache), 6, "Réglages F4JCF"))
        link.close_session()
    finally:
        link.close()
    return sector


def write_f4set(port: str, settings, expected: bytes, backup_dir: str, progress: Progress = None,
                cancel: CancelCheck = None, log: Log = print) -> str:
    """Écrit les réglages des modes F4JCF (région 0x9A). Le secteur actuel de la radio doit être
    celui qui a été lu (`expected`, logo compris) ; il est enregistré sur l'ordinateur avant
    l'écriture, puis relu et comparé. Retourne le chemin de la sauvegarde."""
    import datetime
    from . import f4set as FS
    link = _open_normal(port, log)
    try:
        rd = BlockCache(link, cancel)
        fmt, _ = _detect_format(rd)
        if fmt != "stock":
            raise RadioError("La radio ne contient pas un firmware dérivé du firmware officiel "
                             "Radtel V3.25 : rien n'a été écrit.")
        _connect(link, fmt, log)
        cur = rd.read(FS.SECTOR_ADDR, FS.SECTOR_SIZE)
        os.makedirs(backup_dir, exist_ok=True)
        bk = os.path.join(backup_dir, f"RT4D-logo-et-reglages-F4JCF-avant-"
                          f"{datetime.datetime.now():%Y%m%d-%H%M%S}.bin")
        with open(bk, "wb") as f:
            f.write(cur)
        log(f"Secteur du logo enregistré : {bk}")
        if cur != expected:
            raise RadioError("Le logo ou les réglages de la radio ont changé depuis la lecture "
                             "(autre radio ?). Relisez les réglages. Rien n'a été écrit.")
        new = FS.build_sector(cur, settings)
        if new == cur:
            log("La radio contient déjà ces réglages : rien à écrire.")
            link.close_session()
            return bk
        log("Écriture des réglages des modes F4JCF (secteur du logo, 4 blocs)…")
        link.write_region(FS.LOGO_REGION, new, progress, cancel, "Réglages F4JCF")
        back = BlockCache(link).read(FS.SECTOR_ADDR, FS.SECTOR_SIZE)
        if back != new:
            raise RadioError("Le secteur relu ne correspond pas à celui envoyé. Recommencez "
                             f"l'écriture ; le logo d'origine est dans {bk}.")
        link.close_session()
        log("Réglages écrits et vérifiés. Le firmware F4JCF15 les prend en compte au prochain "
            "allumage du poste.")
        return bk
    finally:
        link.close()
