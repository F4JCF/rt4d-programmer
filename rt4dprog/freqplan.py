"""Import d'un plan de fréquences (idée n°32).

Formats reconnus automatiquement :
  - CSV de CHIRP (colonnes Location, Name, Frequency, Duplex, Offset, Tone…) ;
  - CSV de RT4D Programmer (Fichier → Exporter les canaux) ;
  - CSV « libre » : une colonne de fréquence et, si présentes, nom, décalage, sens, sous-ton…
    (noms de colonnes en français ou en anglais, séparateur ; , ou tabulation) ;
  - texte simple : une fréquence par ligne, suivie éventuellement d'un nom
    (« 145.500 S20 », « 430,350 438,950 Relais Mougins »).

Le plan est d'abord lu en liste d'entrées (aperçu), puis ajouté au codeplug : les canaux vont dans
les emplacements libres à partir du numéro choisi, et peuvent être regroupés dans une zone.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Optional

from . import tones
from .codeplug import Channel, Zone, fmt_mhz, mhz_to_raw

# Bandes où l'émission radioamateur est autorisée (MHz), utilisées pour l'option « réception seule »
HAM_TX_BANDS = [(144.0, 146.0), (430.0, 440.0)]


@dataclass
class PlanEntry:
    name: str
    rx: int                       # unités de 10 Hz
    tx: int
    analog: bool = True
    modulation: int = 0           # 0 FM, 1 AM, 2 SSB
    narrow: bool = False
    rx_tone: str = ""
    tx_tone: str = ""
    power_high: Optional[bool] = None     # None = valeur choisie à l'import
    scan: bool = True
    rx_only: bool = False         # TX interdite dans le plan (CHIRP « off »)
    color_code: int = 1
    timeslot: int = 0
    note: str = ""

    @property
    def ham_tx(self) -> bool:
        f = self.tx / 100000
        return any(a <= f <= b for a, b in HAM_TX_BANDS)


@dataclass
class Plan:
    fmt: str
    entries: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


# ───────────────────────────── lecture du fichier ─────────────────────────────

def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def _num(text: str) -> float:
    t = (text or "").strip().replace(" ", "").replace(" ", "")
    t = re.sub(r"(?i)\s*(mhz|khz|w)$", "", t)
    return float(t.replace(",", "."))


def _mhz(text: str) -> float:
    """Fréquence en MHz ; accepte aussi des kHz (145500) ou des Hz (145500000)."""
    f = _num(text)
    if f > 1e6:
        f /= 1e6
    elif f > 2000:
        f /= 1e3
    return f


def _rows(text: str) -> list[dict]:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        delim = dialect.delimiter
    except csv.Error:
        delim = ";" if sample.count(";") >= sample.count(",") else ","
    rd = csv.DictReader(io.StringIO(text), delimiter=delim)
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items() if k is not None}
            for r in rd]


def _get(row: dict, *names: str) -> str:
    for n in names:
        v = row.get(n)
        if v:
            return v
    return ""


def _check_freq(f: float, what: str) -> int:
    if not 18.0 <= f <= 1000.0:
        raise ValueError(f"fréquence {what} {f:g} MHz hors de la plage du poste (18-1000 MHz)")
    return mhz_to_raw(f)


def _tone(text: str) -> str:
    try:
        return tones.normalize(text)
    except ValueError:
        return ""


# CHIRP ------------------------------------------------------------------------

def _chirp_entry(r: dict) -> Optional[PlanEntry]:
    f = _get(r, "frequency")
    if not f:
        return None
    rx = _check_freq(_mhz(f), "RX")
    duplex = _get(r, "duplex").lower()
    off = _get(r, "offset")
    offv = _mhz(off) if off else 0.0
    rx_only = False
    if duplex == "+":
        tx = rx + mhz_to_raw(offv)
    elif duplex == "-":
        tx = rx - mhz_to_raw(offv)
    elif duplex == "split":
        tx = _check_freq(offv, "TX")
    elif duplex == "off":
        tx, rx_only = rx, True
    else:
        tx = rx
    mode = _get(r, "mode").upper()
    e = PlanEntry(name=_get(r, "name"), rx=rx, tx=tx, rx_only=rx_only)
    if mode in ("DMR", "DIG"):
        e.analog = False
    elif mode == "AM" or mode == "NAM":
        e.modulation = 1
    elif mode in ("USB", "LSB", "SSB"):
        e.modulation = 2
    e.narrow = mode in ("NFM", "NAM")
    # sous-tons (sémantique CHIRP)
    t = _get(r, "tone")
    rt = _get(r, "rtonefreq") or "88.5"
    ct = _get(r, "ctonefreq") or "88.5"
    code = _get(r, "dtcscode") or "023"
    rcode = _get(r, "rxdtcscode") or code
    pol = (_get(r, "dtcspolarity") or "NN").upper().ljust(2, "N")
    dcs_tx = f"D{int(code):03d}{'N' if pol[0] == 'N' else 'I'}"
    dcs_rx = f"D{int(rcode):03d}{'N' if pol[1] == 'N' else 'I'}"
    if t == "Tone":
        e.tx_tone = _tone(rt)
    elif t == "TSQL":
        e.tx_tone = e.rx_tone = _tone(ct)
    elif t == "DTCS":
        e.tx_tone, e.rx_tone = _tone(dcs_tx), _tone(f"D{int(code):03d}{'N' if pol[1] == 'N' else 'I'}")
    elif t == "Cross":
        cm = _get(r, "crossmode") or "Tone->Tone"
        txm, _, rxm = cm.partition("->")
        e.tx_tone = _tone(rt) if txm == "Tone" else _tone(dcs_tx) if txm == "DTCS" else ""
        e.rx_tone = _tone(ct) if rxm == "Tone" else _tone(dcs_rx) if rxm == "DTCS" else ""
    p = _get(r, "power").lower()
    if p:
        try:
            e.power_high = _num(p) >= 2.0
        except ValueError:
            e.power_high = not p.startswith("l")
    e.scan = _get(r, "skip").upper() != "S"
    e.note = _get(r, "comment")
    return e


# CSV libre / RT4D Programmer ----------------------------------------------------

_F_RX = ("rx (mhz)", "rx", "fréquence rx", "frequence rx", "freq rx", "réception", "reception",
         "fréquence", "frequence", "freq", "freq.", "fréquence (mhz)", "frequence (mhz)",
         "frequency", "rx frequency", "receive frequency", "sortie", "output")
_F_TX = ("tx (mhz)", "tx", "fréquence tx", "frequence tx", "freq tx", "émission", "emission",
         "tx frequency", "transmit frequency", "entrée", "entree", "input")
_F_NAME = ("nom", "name", "indicatif", "callsign", "canal", "channel name", "libellé", "libelle",
           "désignation", "designation")
_F_SHIFT = ("décalage", "decalage", "shift", "offset", "décalage (mhz)", "decalage (mhz)")
_F_DIR = ("sens", "duplex", "direction")
_F_TONE = ("sous-ton", "sous ton", "ctcss", "tone", "ton", "ctcss/dcs", "subtone")


def _free_entry(r: dict) -> Optional[PlanEntry]:
    f = _get(r, *_F_RX)
    if not f:
        return None
    rx = _check_freq(_mhz(f), "RX")
    txt = _get(r, *_F_TX)
    tx = _check_freq(_mhz(txt), "TX") if txt else rx
    sh = _get(r, *_F_SHIFT)
    if not txt and sh:
        s = sh.strip()
        sign = -1 if s.startswith("-") else 1
        d = _get(r, *_F_DIR).strip()
        if d.startswith("-"):
            sign = -1
        v = abs(_num(s.lstrip("+-")))
        if v > 100:                               # décalage donné en kHz (600, 7600)
            v /= 1000
        tx = rx + sign * mhz_to_raw(v)
    name = _get(r, *_F_NAME)
    ville = _get(r, "ville", "city", "lieu", "location")
    if ville and _get(r, "indicatif", "callsign") and not _get(r, "nom", "name"):
        name = f"{name} {ville}"
    e = PlanEntry(name=name, rx=rx, tx=tx)
    mode = _get(r, "mode", "channel type").lower()
    if mode.startswith(("num", "dig", "dmr")):
        e.analog = False
    m = _get(r, "modulation").upper()
    if m == "AM" or mode == "am":
        e.modulation = 1
    elif m in ("SSB", "BLU", "USB", "LSB"):
        e.modulation = 2
    bw = _get(r, "largeur", "bandwidth", "bande").replace(",", ".").lower()
    e.narrow = bw.startswith(("12", "n", "é", "e")) or mode == "nfm"
    tone = _get(r, *_F_TONE)
    e.rx_tone = _tone(_get(r, "ton rx", "rx tone", "ctcss/dcs decode") or "")
    e.tx_tone = _tone(_get(r, "ton tx", "tx tone", "ctcss/dcs encode") or tone)
    p = _get(r, "puissance", "power").lower()
    if p:
        e.power_high = not p.startswith(("b", "l"))
    sc = _get(r, "scan", "balayage").lower()
    if sc:
        e.scan = not (sc.startswith("n") or sc in ("0", "false"))
    cc = _get(r, "code couleur", "color code", "cc")
    if cc.isdigit():
        e.color_code = min(int(cc), 15)
    ts = _get(r, "slot", "time slot", "timeslot")
    e.timeslot = 1 if ts.strip().endswith("2") else 0
    e.note = _get(r, "commentaire", "comment", "remarque", "notes")
    return e


# Texte simple --------------------------------------------------------------------

_FREQ = r"\d{2,4}(?:[.,]\d{1,6})?"
_LINE = re.compile(rf"^\s*({_FREQ})(?:\s+({_FREQ}))?\s*[;,\t ]?\s*(.*)$")


def _text_entries(text: str, plan: Plan):
    for i, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith(("#", "//", ";")):
            continue
        m = _LINE.match(s)
        if not m:
            plan.warnings.append(f"Ligne {i} ignorée : pas de fréquence au début (« {s[:30]} »).")
            continue
        try:
            rx = _check_freq(_mhz(m.group(1)), "RX")
            tx = rx
            if m.group(2):
                f2 = _mhz(m.group(2))
                if 18.0 <= f2 <= 1000.0:
                    tx = mhz_to_raw(f2)
            plan.entries.append(PlanEntry(name=m.group(3).strip(), rx=rx, tx=tx))
        except ValueError as e:
            plan.warnings.append(f"Ligne {i} ignorée : {e}.")


# Point d'entrée ------------------------------------------------------------------

def read_plan(path: str) -> Plan:
    with open(path, "rb") as f:
        text = _decode(f.read())
    return parse_plan(text)


def parse_plan(text: str) -> Plan:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "//"))]
    first = lines[0].strip().lower() if lines else ""
    if not first:
        return Plan("vide", warnings=["Le fichier est vide."])
    if "frequency" in first and "duplex" in first and "location" in first:
        plan, fn = Plan("CHIRP"), _chirp_entry
    elif re.match(rf"^\s*{_FREQ}\b", first) and not any(h in first for h in ("mhz", "fréq", "freq")):
        plan = Plan("texte")
        _text_entries(text, plan)
        return plan
    else:
        plan = Plan("RT4D Programmer" if first.startswith("n°;nom;mode") else "CSV")
        fn = _free_entry
    rows = _rows(text)
    if rows and not any(_get(r, *_F_RX, "frequency") for r in rows[:20]):
        plan.warnings.append("Aucune colonne de fréquence reconnue (Fréquence, RX, Frequency…).")
    for i, r in enumerate(rows, start=2):
        try:
            e = fn(r)
        except (ValueError, IndexError) as ex:
            plan.warnings.append(f"Ligne {i} ignorée : {ex}.")
            continue
        if e:
            plan.entries.append(e)
    return plan


# ───────────────────────────── ajout au codeplug ─────────────────────────────

@dataclass
class ImportOptions:
    start: int = 1                    # premier numéro de canal (1-1024)
    zone_name: str = ""               # vide = pas de zone
    skip_duplicates: bool = True      # même RX et même TX déjà présents
    power_high: bool = False          # puissance quand le plan ne la donne pas
    rx_only_outside_ham: bool = True  # réception seule hors 144-146 / 430-440 MHz
    name_prefix: str = ""


def _default_name(e: PlanEntry) -> str:
    return fmt_mhz(e.rx)[:9].rstrip("0").rstrip(".")


def apply_plan(cp, entries: list, opt: ImportOptions) -> tuple[list[int], list[str]]:
    """Ajoute les entrées au codeplug. Retourne (emplacements remplis, remarques)."""
    notes = []
    maxch = cp.LIMITS["channels"]
    existing = {(c.rx, c.tx) for c in cp.channels.values()}
    slots = []
    slot = max(0, min(opt.start - 1, maxch - 1))
    rx_only_n = 0
    for e in entries:
        if opt.skip_duplicates and (e.rx, e.tx) in existing:
            notes.append(f"{fmt_mhz(e.rx)} MHz « {e.name} » déjà présent : non ajouté.")
            continue
        while slot < maxch and slot in cp.channels:
            slot += 1
        if slot >= maxch:
            notes.append(f"Plus d'emplacement libre après le canal {opt.start} : import arrêté.")
            break
        ch = Channel(name=(opt.name_prefix + (e.name or _default_name(e)))[:16], analog=e.analog,
                     rx=e.rx, tx=e.tx)
        ch.modulation = e.modulation
        ch.narrow = e.narrow
        ch.rx_tone, ch.tx_tone = e.rx_tone, e.tx_tone
        ch.power_high = opt.power_high if e.power_high is None else e.power_high
        ch.scan = e.scan
        ch.color_code, ch.timeslot = e.color_code, e.timeslot
        if not e.analog:
            ch.contact = 0 if 0 in cp.contacts else None
        if e.rx_only or (opt.rx_only_outside_ham and not e.ham_tx):
            ch.rx_tx = 1                  # « RX seul »
            ch.power_high = False
            rx_only_n += 1
        cp.channels[slot] = ch
        existing.add((e.rx, e.tx))
        slots.append(slot)
        slot += 1
    if rx_only_n:
        notes.append(f"{rx_only_n} canal(aux) hors des bandes radioamateur 144-146 / 430-440 MHz "
                     "réglé(s) en « RX seul ».")
    if opt.zone_name and slots:
        zmax = cp.LIMITS["zone_channels"]
        zslot = cp.free_slot(cp.zones, cp.LIMITS["zones"])
        if zslot is None:
            notes.append("Aucune zone libre : canaux ajoutés sans zone.")
        else:
            for k in range(0, len(slots), zmax):
                part = slots[k:k + zmax]
                name = opt.zone_name if k == 0 else f"{opt.zone_name[:13]} {k // zmax + 1}"
                zs = cp.free_slot(cp.zones, cp.LIMITS["zones"])
                if zs is None:
                    notes.append("Plus de zone libre : la fin du plan n'est dans aucune zone.")
                    break
                cp.zones[zs] = Zone(name=name[:16], channels=list(part), scan=[True] * len(part))
                notes.append(f"Zone « {name[:16]} » créée ({len(part)} canaux).")
    return slots, notes
