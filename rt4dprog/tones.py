"""Codage des sous-tons CTCSS / DCS du RT-4D.

Valeur 16 bits petit-boutiste :
  bits 12-15 : 0 = aucun, 1 = CTCSS, 2 = DCS normal, 3 = DCS inversé
  bits 0-11  : CTCSS = fréquence × 10 ; DCS = code octal converti en entier
Représentation texte : ""  /  "88.5"  /  "D023N"  /  "D023I"
"""

CTCSS_TONES = [
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4,
    100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3, 131.8, 136.5,
    141.3, 146.2, 150.0, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3,
    173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5,
    206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
]

DCS_CODES = [
    23, 25, 26, 31, 32, 36, 43, 47, 51, 53, 54, 65, 71, 72, 73, 74, 114, 115,
    116, 122, 125, 131, 132, 134, 143, 145, 152, 155, 156, 162, 165, 172, 174,
    205, 212, 223, 225, 226, 243, 244, 245, 246, 251, 252, 255, 261, 263, 265,
    266, 271, 274, 306, 311, 315, 325, 331, 332, 343, 346, 351, 356, 364, 365,
    371, 411, 412, 413, 423, 431, 432, 445, 446, 452, 454, 455, 462, 464, 465,
    466, 503, 506, 516, 523, 526, 532, 546, 565, 606, 612, 624, 627, 631, 632,
    654, 662, 664, 703, 712, 723, 731, 732, 734, 743, 754,
]


def tone_choices() -> list[str]:
    out = [""]
    out += [f"{t:.1f}" for t in CTCSS_TONES]
    out += [f"D{c:03d}N" for c in DCS_CODES]
    out += [f"D{c:03d}I" for c in DCS_CODES]
    return out


def decode(raw: bytes) -> str:
    v = raw[0] | (raw[1] << 8)
    if v in (0x0000, 0xFFFF):
        return ""
    kind, val = v >> 12, v & 0x0FFF
    if kind == 1:
        return f"{val / 10:.1f}"
    if kind in (2, 3):
        return f"D{val:03o}{'N' if kind == 2 else 'I'}"
    return ""


def normalize(text: str) -> str:
    """Accepte « 88.5 », « 88,5 », « D23N », « d023i », « aucun »… ; ValueError sinon."""
    t = (text or "").strip().upper().replace(",", ".")
    if t in ("", "AUCUN", "OFF", "NONE", "-"):
        return ""
    if t.startswith("D"):
        pol = "N"
        body = t[1:]
        if body.endswith(("N", "I", "R")):
            pol = "N" if body[-1] == "N" else "I"
            body = body[:-1]
        if not body.isdigit() or any(c in "89" for c in body) or int(body, 8) > 0o777:
            raise ValueError(f"Code DCS invalide : {text}")
        return f"D{int(body):03d}{pol}"
    f = float(t)
    if not 60.0 <= f <= 260.0:
        raise ValueError(f"Fréquence CTCSS hors plage : {text}")
    return f"{f:.1f}"


def encode(text: str) -> bytes:
    t = normalize(text)
    if not t:
        v = 0
    elif t.startswith("D"):
        v = (0x2000 if t.endswith("N") else 0x3000) | int(t[1:4], 8)
    else:
        v = 0x1000 | (round(float(t) * 10) & 0x0FFF)
    return bytes([v & 0xFF, v >> 8])
