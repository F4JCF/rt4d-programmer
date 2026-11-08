"""Police ASCII du firmware officiel Radtel avec accents français.

La police 7 × 16 du poste (flash SPI 0x19C000, 8 Ko, 14 octets par caractère, index
(code − 32) × 14, colonnes de 2 octets, bit 0 en haut) n'a pas d'accents. Sept caractères
ASCII inutilisés par les menus sont redessinés :

    `  → é     {  → è     ^  → ê     }  → à     |  → ç     ~  → î     \\  → É

Le firmware traduit en français (FR03) utilise ces caractères. Un texte tapé avec l'un d'eux
(nom de canal, SMS) s'affichera donc avec l'accent correspondant.
"""

from __future__ import annotations

W, H = 7, 16
SIZE = 0x2000

# caractère remplacé -> (lettre affichée, lettre de base, accent)
MAP = {"`": ("é", "e", "aigu"), "{": ("è", "e", "grave"), "^": ("ê", "e", "circ"),
       "}": ("à", "a", "grave"), "|": ("ç", "c", "cedille"), "~": ("î", "i", "circ"),
       "\\": ("É", "E", "aigu_maj")}
ACCENT = {
    "aigu": (2, ["....##.", "...##.."]),
    "grave": (2, ["..##...", "...##.."]),
    "circ": (2, ["..###..", ".##.##."]),
    "cedille": (12, ["...##..", "..##..."]),
    "aigu_maj": (0, ["....##.", "...##.."]),
}
TO_RADIO = {v[0]: k for k, v in MAP.items()}


def glyph(font: bytes, ch: str) -> list[list[int]]:
    o = (ord(ch) - 32) * 14
    g = font[o:o + 14]
    return [[(g[2 * x + (y >> 3)] >> (y & 7)) & 1 for x in range(W)] for y in range(H)]


def encode(rows) -> bytes:
    out = bytearray(14)
    for x in range(W):
        for y in range(H):
            if rows[y][x]:
                out[2 * x + (y >> 3)] |= 1 << (y & 7)
    return bytes(out)


def _accented(font: bytes, base: str, acc: str):
    g = [row[:] for row in glyph(font, base)]
    if base == "i":                       # le point du i laisse la place à l'accent
        for y in range(5):
            g[y] = [0] * W
    y0, pat = ACCENT[acc]
    for dy, line in enumerate(pat):
        g[y0 + dy] = [1 if c == "#" else 0 for c in line]
    return g


def check_font(font: bytes) -> str | None:
    """Message d'erreur si les 8 Ko ne ressemblent pas à la police ASCII du RT-4D."""
    if len(font) != SIZE:
        return f"Taille inattendue ({len(font)} octets au lieu de {SIZE})."
    if font[:14] != b"\x00" * 14:
        return "Le caractère espace n'est pas vide : ce n'est pas la police attendue."
    if all(b == 0xFF for b in font[:95 * 14]) or all(b == 0 for b in font[14:95 * 14]):
        return "La police est vide."
    for ch in "AEae0":
        n = sum(map(sum, glyph(font, ch)))
        if not 10 <= n <= 60:
            return f"Le caractère « {ch} » n'a pas la forme attendue : ce n'est pas la police du RT-4D."
    return None


def build_accented(font: bytes) -> bytes:
    """Police d'origine → police avec accents. Seuls les 7 caractères de MAP changent."""
    err = check_font(font)
    if err:
        raise ValueError(err)
    out = bytearray(font)
    for ch, (_, base, acc) in MAP.items():
        o = (ord(ch) - 32) * 14
        out[o:o + 14] = encode(_accented(font, base, acc))
    return bytes(out)


def is_accented(font: bytes) -> bool:
    """Vrai si la police contient déjà les accents (é à la place de `)."""
    o = (ord("`") - 32) * 14
    return font[o:o + 14] == encode(_accented(font, "e", "aigu"))


def to_radio(text: str) -> str:
    """é è ê à ç î É → caractères affichés avec accent par la police modifiée."""
    return "".join(TO_RADIO.get(c, c) for c in text)
