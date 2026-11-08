"""Carte mémoire du RT-4D (firmware REFW de Dual Tachyon, beta 41 et suivantes).

L'image du codeplug en mémoire suit le format de fichier « .4rdmf »
(0x43500 octets), échangeable avec RT4D Editor. Chaque section correspond à
une région de la flash SPI de la radio.
"""

FILE_SIZE = 0x43500
FILE_SIZE_LEGACY = 0x43400        # anciens fichiers sans noms DTMF

MAGIC = b"DTCN"                   # marque des réglages REFW beta41+

# Section : (offset fichier, taille, adresse SPI, numéro de région d'écriture)
SECTIONS = {
    "settings": (0x00000, 0x01000, None, 0x90),    # adresse = banque active (0x2000/0x3000)
    "channels": (0x01000, 0x0C000, 0x004000, 0x91),
    "contacts": (0x0D000, 0x10000, 0x05C000, 0x93),
    "groups":   (0x1D000, 0x03000, 0x07C000, 0x94),
    "keys":     (0x20000, 0x03000, 0x082000, 0x95),
    "zones":    (0x23000, 0x20000, 0x01C000, 0x92),
    "fm":       (0x43000, 0x00400, 0x0D6000, 0x99),
    "dtmf":     (0x43400, 0x00100, 0x0C7000, 0x80),
}

SECTION_LABELS = {
    "settings": "Réglages généraux",
    "channels": "Canaux",
    "contacts": "Contacts DMR",
    "groups": "Groupes RX",
    "keys": "Clés de chiffrement",
    "zones": "Zones",
    "fm": "Radio FM",
    "dtmf": "Noms DTMF",
}

# Sections que le logiciel sait relire et écrire
EDITABLE_SECTIONS = ["settings", "channels", "contacts", "groups", "zones"]
READ_SECTIONS = ["settings", "channels", "contacts", "groups", "keys", "zones", "fm", "dtmf"]

SETTINGS_BANKS = (0x002000, 0x003000)

# Zones : chaque page de 4 Ko peut être dans la banque A ou B (beta41+)
ZONE_PAGE = 0x1000
ZONE_BANK_B = 0x20000
ZONE_MARKER_OFF = 0xFF8
ZONE_MARKER_PRISTINE = b"\xFF\xFF\xFF\xFFDTCN"

# Tailles d'enregistrements
CH_SIZE, CH_MAX = 48, 1024
CT_SIZE, CT_MAX = 32, 2048
GL_SIZE, GL_MAX, GL_IDS = 80, 150, 32
ZN_SIZE, ZN_MAX, ZN_CH_MAX = 512, 256, 200
ZN_SCAN_OFF = 0x1A4

# Régions de la flash complète (pour restauration d'une sauvegarde 4 Mo)
# (numéro, adresse, taille, nom)
SPI_RESTORE_REGIONS = [
    (0x40, 0x000000, 0x001000, "Calibration"),
    (0x90, 0x002000, 0x001000, "Réglages"),
    (0x91, 0x004000, 0x00C000, "Canaux"),
    (0x92, 0x01C000, 0x020000, "Zones"),
    (0x93, 0x05C000, 0x010000, "Contacts"),
    (0x94, 0x07C000, 0x003000, "Groupes"),
    (0x95, 0x082000, 0x003000, "Clés DMR"),
    (0x96, 0x088000, 0x00C000, "Journal d'appels"),
    (0x97, 0x094000, 0x001000, "SMS prédéfinis"),
    (0x98, 0x0C6000, 0x008000, "Programmations"),
    (0x99, 0x0D6000, 0x001000, "Radio FM"),
    (0x9A, 0x100000, 0x001000, "Logo de démarrage"),
    (0x9B, 0x126000, 0x001000, "Inconnu 9B"),
    (0x9C, 0x14C000, 0x018000, "Table UTF16"),
    (0x9D, 0x164000, 0x024000, "Phonèmes"),
    (0x9E, 0x188000, 0x010000, "Inconnu 9E"),
    (0x9F, 0x198000, 0x004000, "Inconnu 9F"),
    (0xA0, 0x19C000, 0x002000, "Polices 1"),
    (0xA1, 0x19E000, 0x0B4000, "Polices 2"),
    (0xA2, 0x343000, 0x00F000, "Inconnu A2"),
    (0xA3, 0x352000, 0x0AE000, "Voix"),
]
