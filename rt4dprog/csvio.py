"""Import / export CSV (séparateur « ; » compatible Excel/LibreOffice en français)."""

from __future__ import annotations

import csv
import io

from . import tones
from .codeplug import (ANALOG_MODS, CONTACT_TYPES, Channel, Codeplug, Contact, fmt_mhz,
                       mhz_to_raw)

CH_HEADERS = ["N°", "Nom", "Mode", "RX (MHz)", "TX (MHz)", "Puissance", "Modulation",
              "Largeur", "Ton RX", "Ton TX", "Code couleur", "Slot", "Contact", "Groupe RX",
              "Scan"]
CT_HEADERS = ["Nom", "Type", "ID DMR"]


def _read_rows(path: str) -> list[dict]:
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rows]


def _get(row: dict, *names: str) -> str:
    for n in names:
        v = row.get(n.lower())
        if v not in (None, ""):
            return v
    return ""


def _num(text: str) -> float:
    return float(text.replace(",", ".").replace(" ", ""))


def export_channels(cp: Codeplug, path: str):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(CH_HEADERS)
        for slot, ch in sorted(cp.channels.items()):
            ct = cp.contacts.get(ch.contact) if ch.contact is not None else None
            gl = cp.groups.get(ch.group) if ch.group is not None else None
            w.writerow([
                slot + 1, ch.name, "Analogique" if ch.analog else "Numérique",
                fmt_mhz(ch.rx).replace(".", ","), fmt_mhz(ch.tx).replace(".", ","),
                "Haute" if ch.power_high else "Basse", ANALOG_MODS[ch.modulation],
                "12,5" if ch.narrow else "25", ch.rx_tone.replace(".", ","),
                ch.tx_tone.replace(".", ","), ch.color_code, ch.timeslot + 1,
                ct.name if ct else "", gl.name if gl else "", "Oui" if ch.scan else "Non",
            ])


def import_channels(cp: Codeplug, path: str, replace: bool) -> tuple[int, list[str]]:
    """Retourne (nombre importé, avertissements)."""
    rows = _read_rows(path)
    warn = []
    if replace:
        for slot in list(cp.channels):
            cp.delete_channel(slot)
    ct_by_name = {c.name.lower(): s for s, c in cp.contacts.items()}
    gl_by_name = {g.name.lower(): s for s, g in cp.groups.items()}
    n = 0
    for i, r in enumerate(rows, start=2):
        try:
            name = _get(r, "nom", "name")
            rx_t = _get(r, "rx (mhz)", "rx", "rx frequency", "receive frequency")
            if not rx_t:
                continue
            rx = mhz_to_raw(_num(rx_t))
            tx_t = _get(r, "tx (mhz)", "tx", "tx frequency", "transmit frequency")
            tx = mhz_to_raw(_num(tx_t)) if tx_t else rx
            mode = _get(r, "mode", "channel type").lower()
            analog = not (mode.startswith("num") or mode.startswith("dig") or mode == "dmr")
            ch = Channel(name=name, analog=analog, rx=rx, tx=tx)
            p = _get(r, "puissance", "power").lower()
            ch.power_high = not (p.startswith("b") or p.startswith("l"))
            m = _get(r, "modulation").upper()
            ch.modulation = ANALOG_MODS.index(m) if m in ANALOG_MODS else 0
            bw = _get(r, "largeur", "bandwidth").replace(",", ".").lower()
            ch.narrow = bw.startswith("12") or bw.startswith("n") or bw.startswith("é")
            ch.rx_tone = tones.normalize(_get(r, "ton rx", "rx tone", "ctcss/dcs decode"))
            ch.tx_tone = tones.normalize(_get(r, "ton tx", "tx tone", "ctcss/dcs encode"))
            cc = _get(r, "code couleur", "color code", "cc")
            ch.color_code = int(cc) if cc else 1
            ts = _get(r, "slot", "time slot", "timeslot")
            ch.timeslot = 1 if ts.strip().endswith("2") else 0
            cname = _get(r, "contact", "contact name").lower()
            if cname:
                if cname in ct_by_name:
                    ch.contact = ct_by_name[cname]
                else:
                    warn.append(f"Ligne {i} : contact « {cname} » introuvable.")
            gname = _get(r, "groupe rx", "rx group list", "rx group").lower()
            if gname:
                if gname in gl_by_name:
                    ch.group = gl_by_name[gname]
                else:
                    warn.append(f"Ligne {i} : groupe RX « {gname} » introuvable.")
            sc = _get(r, "scan").lower()
            ch.scan = not (sc.startswith("n") or sc in ("0", "false"))
            num = _get(r, "n°", "no", "number", "position")
            slot = None
            if num.isdigit() and 1 <= int(num) <= 1024 and (int(num) - 1) not in cp.channels:
                slot = int(num) - 1
            if slot is None:
                slot = cp.free_slot(cp.channels, cp.LIMITS["channels"])
            if slot is None:
                warn.append("Mémoire pleine (1024 canaux) : import arrêté.")
                break
            cp.channels[slot] = ch
            n += 1
        except (ValueError, IndexError) as e:
            warn.append(f"Ligne {i} ignorée : {e}")
    return n, warn


def export_contacts(cp: Codeplug, path: str):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(CT_HEADERS)
        for _, c in sorted(cp.contacts.items()):
            w.writerow([c.name, CONTACT_TYPES[c.ctype], c.dmr_id])


def import_contacts(cp: Codeplug, path: str, replace: bool) -> tuple[int, list[str]]:
    rows = _read_rows(path)
    warn = []
    if replace:
        for slot in list(cp.contacts):
            cp.delete_contact(slot)
    n = 0
    for i, r in enumerate(rows, start=2):
        try:
            idt = _get(r, "id dmr", "id", "dmr id", "call id", "radio id")
            if not idt:
                continue
            dmr_id = int(_num(idt))
            name = _get(r, "nom", "name", "contact name", "callsign")
            t = _get(r, "type", "call type").lower()
            ctype = 0 if t.startswith(("pri", "pers", "ind")) else 2 if t.startswith(("tous", "all")) else 1
            slot = cp.free_slot(cp.contacts, cp.LIMITS["contacts"])
            if slot is None:
                warn.append("Mémoire pleine : import arrêté.")
                break
            cp.contacts[slot] = Contact(name=name, ctype=ctype, dmr_id=dmr_id)
            n += 1
        except ValueError as e:
            warn.append(f"Ligne {i} ignorée : {e}")
    return n, warn
