"""Carnet d'adresses DMR (base des indicatifs) — format et envoi identiques au CPS Radtel V2.07.

Données envoyées : un en-tête de 4 octets (longueur totale, gros-boutiste, en-tête
compris) puis des lignes texte GBK « ID,Indicatif,Nom,Ville,Région,Pays\\n ».
Envoi par blocs de 1 Ko avec la commande 0xA4 ; la radio répond 0x06 à chaque bloc,
0xA4 si sa mémoire flash ne convient pas, 0x4A si la capacité est atteinte.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from .protocol import BLOCK, CancelCheck, Cancelled, Progress, RadioError

RADIOID_URL = "https://radioid.net/static/user.csv"
MAX_BYTES = 29_360_124          # limite du CPS officiel (28 Mo - 4)
SECONDS_PER_BLOCK = 0.095       # ≈ 1028 octets à 115200 bauds + accusé


@dataclass
class Entry:
    dmr_id: int
    callsign: str
    name: str
    city: str
    state: str
    country: str


def _clean(text: str) -> str:
    """ASCII sans accents, sans virgule ni retour à la ligne."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = "".join(c if 32 <= ord(c) < 127 else " " for c in t)
    return " ".join(t.replace(",", " ").split())


def _col(header: list[str], *names: str) -> Optional[int]:
    norm = [h.strip().lower().replace("_", " ").replace(".", "") for h in header]
    for n in names:
        if n in norm:
            return norm.index(n)
    return None


def parse_csv_text(text: str) -> list[Entry]:
    """Lit un CSV RadioID.net (RADIO_ID,CALLSIGN,FIRST_NAME,LAST_NAME,CITY,STATE,COUNTRY)
    ou au format Radtel (No,Radio ID,Callsign,Name,City,State,Country,Remarks)."""
    sample = text[:4096]
    delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    header = next(reader, None)
    if not header:
        raise RadioError("Le fichier CSV est vide.")
    c_id = _col(header, "radio id", "radioid", "dmr id", "id", "dmrid")
    c_call = _col(header, "callsign", "call", "indicatif")
    c_first = _col(header, "first name", "firstname", "prenom", "prénom")
    c_last = _col(header, "last name", "lastname", "nom de famille")
    c_name = _col(header, "name", "nom")
    c_city = _col(header, "city", "ville")
    c_state = _col(header, "state", "region", "région")
    c_country = _col(header, "country", "pays")
    if c_id is None or c_call is None:
        raise RadioError("Colonnes « Radio ID » et « Callsign » introuvables dans ce CSV.")

    def g(row, i):
        return row[i] if i is not None and i < len(row) else ""

    out = []
    for row in reader:
        try:
            dmr_id = int(g(row, c_id).strip())
        except ValueError:
            continue
        if not 1 <= dmr_id <= 16_777_215:
            continue
        if c_first is not None or c_last is not None:
            name = " ".join(x for x in (g(row, c_first), g(row, c_last)) if x.strip())
        else:
            name = g(row, c_name)
        out.append(Entry(dmr_id, _clean(g(row, c_call)).upper(), _clean(name),
                         _clean(g(row, c_city)), _clean(g(row, c_state)),
                         _clean(g(row, c_country))))
    if not out:
        raise RadioError("Aucune entrée valide trouvée dans ce CSV.")
    return out


def load_csv(path: str) -> list[Entry]:
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return parse_csv_text(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    raise RadioError("Encodage du fichier non reconnu.")


def cache_path() -> str:
    """Copie locale de la base (gardée entre deux lancements du logiciel)."""
    import os
    import sys
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "RT4D-Programmer")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "carnet-dmr.csv")


def load_cache() -> Optional[tuple[list[Entry], float]]:
    """(entrées, date de mise à jour) de la base gardée sur le PC, ou None."""
    import os
    p = cache_path()
    if not os.path.exists(p):
        return None
    try:
        return load_csv(p), os.path.getmtime(p)
    except Exception:
        return None


def save_cache(raw: bytes):
    import os
    p = cache_path()
    tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
    os.replace(tmp, p)


def download_radioid(progress: Progress = None, cancel: CancelCheck = None,
                     log: Callable[[str], None] = print) -> list[Entry]:
    """Télécharge la base mondiale sur RadioID.net (environ 15 à 20 Mo) et la garde sur le PC."""
    import ssl
    import urllib.request
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    log("Téléchargement de la base RadioID.net…")
    req = urllib.request.Request(RADIOID_URL, headers={"User-Agent": "RT4D-Programmer"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            total = int(r.headers.get("Content-Length") or 0)
            buf = bytearray()
            while True:
                if cancel and cancel():
                    raise Cancelled("Téléchargement annulé.")
                chunk = r.read(65536)
                if not chunk:
                    break
                buf.extend(chunk)
                if progress:
                    progress(len(buf) // 1024, max(total // 1024, len(buf) // 1024), "Téléchargement (Ko)")
    except Cancelled:
        raise
    except Exception as e:
        raise RadioError(f"Téléchargement impossible : {e}\n\nVous pouvez télécharger le fichier "
                         f"vous-même ({RADIOID_URL}) puis l'ouvrir avec « Ouvrir un CSV ».") from e
    log(f"{len(buf) // 1024} Ko reçus, analyse…")
    entries = parse_csv_text(buf.decode("utf-8-sig", "replace"))
    save_cache(bytes(buf))
    return entries


def countries(entries: Iterable[Entry]) -> list[tuple[str, int]]:
    c = Counter(e.country or "(inconnu)" for e in entries)
    return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))


def build_payload(entries: Iterable[Entry]) -> bytes:
    lines = []
    seen = set()
    for e in sorted(entries, key=lambda e: e.dmr_id):
        if e.dmr_id in seen:
            continue
        seen.add(e.dmr_id)
        lines.append(f"{e.dmr_id},{e.callsign},{e.name},{e.city},{e.state},{e.country}\n")
    body = "".join(lines).encode("gbk", "replace")
    if len(body) > MAX_BYTES:
        raise RadioError(f"Carnet trop volumineux ({len(body) // 1024} Ko) : limitez les pays.")
    total = len(body) + 4
    return total.to_bytes(4, "big") + body


def estimate(payload_len: int) -> tuple[int, float]:
    """(nombre de blocs, durée estimée en minutes)."""
    n = (payload_len + BLOCK - 1) // BLOCK
    return n, n * SECONDS_PER_BLOCK / 60


def write_addressbook(port: str, payload: bytes, progress: Progress = None,
                      cancel: CancelCheck = None, log: Callable[[str], None] = print):
    from . import stock as S
    from .radio_ops import BlockCache, _detect_format, _open_normal
    link = _open_normal(port, log)
    try:
        fmt, _ = _detect_format(BlockCache(link))
        # Ouverture de session : commande du CPS officiel (firmware officiel), sinon REFW
        link.ser.write(S.CONNECT if fmt == "stock" else bytes([0x34, 0x00, 0x00, 0x10, 0x44]))
        r = link._read_exact(1)
        if not r or r[0] != 0x06:
            raise RadioError("La radio n'a pas accepté l'ouverture de la session.")
        n = (len(payload) + BLOCK - 1) // BLOCK
        log(f"Envoi de {len(payload) // 1024} Ko ({n} blocs)…")
        for i in range(n):
            if cancel and cancel():
                link.ser.write(S.END)
                raise Cancelled("Envoi annulé : le carnet de la radio est incomplet, "
                                "relancez l'envoi pour le terminer.")
            pkt = bytearray(BLOCK + 4)
            pkt[0] = 0xA4
            pkt[1] = (i >> 8) & 0xFF
            pkt[2] = i & 0xFF
            chunk = payload[i * BLOCK:(i + 1) * BLOCK]
            pkt[3:3 + len(chunk)] = chunk
            pkt[3 + len(chunk):3 + BLOCK] = b"\xFF" * (BLOCK - len(chunk))
            s = 0
            for b in pkt[:-1]:
                s = (s + b) & 0xFF
            pkt[-1] = s
            link.ser.write(bytes(pkt))
            r = link._read_exact(1)
            if not r:
                raise RadioError(f"Pas de réponse de la radio (bloc {i + 1}/{n}).")
            if r[0] == 0xA4:
                raise RadioError("La radio indique que sa mémoire flash ne convient pas "
                                 "pour le carnet d'adresses.")
            if r[0] == 0x4A:
                raise RadioError("La mémoire du carnet d'adresses de la radio est pleine : "
                                 "limitez les pays.")
            if r[0] != 0x06:
                raise RadioError(f"Erreur de communication (réponse 0x{r[0]:02X}, bloc {i + 1}).")
            if progress:
                progress(i + 1, n, "Carnet d'adresses")
        link.ser.write(S.END)
        log("Carnet d'adresses envoyé.")
    finally:
        link.close()
