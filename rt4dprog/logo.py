"""Logo de démarrage du RT-4D (idée n°24).

Le logo est une image noir et blanc de 128 × 64 points rangée dans la flash SPI en 0x100000 (1 Ko,
1er Ko du secteur 0x9A). Le firmware V3.25 le lit (0x08013B20) et l'envoie à l'écran par bandes de
8 lignes : la bande du haut de l'écran est la dernière du fichier.
  point (x, y) allumé  <=>  octet[(7 - y // 8) * 128 + x], bit (y % 8)
Vérifié dans l'émulateur avec le logo du poste de F4JCF : 0 point différent.
L'affichage du logo au démarrage se règle dans l'onglet Menu démarrage (« Logo au démarrage »).
"""

from __future__ import annotations

W, H = 128, 64
SIZE = 1024


def encode(px) -> bytes:
    """px[y][x] vrai = point noir (allumé). Retourne les 1024 octets du logo."""
    out = bytearray(SIZE)
    for y in range(H):
        row = px[y]
        base = (7 - y // 8) * 128
        bit = 1 << (y % 8)
        for x in range(W):
            if row[x]:
                out[base + x] |= bit
    return bytes(out)


def decode(data: bytes):
    """1024 octets -> px[y][x] (booléens)."""
    if len(data) < SIZE:
        raise ValueError("logo : 1024 octets attendus")
    return [[bool(data[(7 - y // 8) * 128 + x] >> (y % 8) & 1) for x in range(W)] for y in range(H)]


def from_gray(gray, threshold: int = 128, dither: bool = False, invert: bool = False):
    """gray[y][x] = 0 (noir) à 255 (blanc), 128 × 64. Retourne px[y][x] (vrai = point noir).
    Tramage de Floyd-Steinberg en option (dégradés, photos)."""
    g = [[float(v) for v in row] for row in gray]
    px = [[False] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            old = g[y][x]
            black = old < threshold
            px[y][x] = black != invert
            if dither:
                err = old - (0.0 if black else 255.0)
                if x + 1 < W:
                    g[y][x + 1] += err * 7 / 16
                if y + 1 < H:
                    if x > 0:
                        g[y + 1][x - 1] += err * 3 / 16
                    g[y + 1][x] += err * 5 / 16
                    if x + 1 < W:
                        g[y + 1][x + 1] += err * 1 / 16
    return px


def black_ratio(px) -> float:
    return sum(sum(1 for v in row if v) for row in px) / (W * H)
