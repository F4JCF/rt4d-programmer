import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rt4dprog import freqplan as FP
from rt4dprog import stock as S
from tests.test_core import make_stock_spi

CHIRP = """Location,Name,Frequency,Duplex,Offset,Tone,rToneFreq,cToneFreq,DtcsCode,DtcsPolarity,RxDtcsCode,CrossMode,Mode,TStep,Skip,Power,Comment,URCALL,RPT1CALL,RPT2CALL,DVCODE
0,S20,145.500000,,0.000000,,88.5,88.5,023,NN,023,Tone->Tone,FM,12.50,,5.0W,,,,,
1,F1ZBV,145.725000,-,0.600000,Tone,123.0,88.5,023,NN,023,Tone->Tone,FM,12.50,,1.0W,relais,,,,
2,UHF,430.350000,split,438.950000,TSQL,88.5,67.0,023,NN,023,Tone->Tone,NFM,12.50,S,5.0W,,,,,
3,DCS,433.500000,,0.000000,DTCS,88.5,88.5,047,NR,023,Tone->Tone,FM,12.50,,5.0W,,,,,
4,SATER,121.500000,off,0.000000,,88.5,88.5,023,NN,023,Tone->Tone,AM,12.50,,5.0W,,,,,
"""


def test_chirp():
    p = FP.parse_plan(CHIRP)
    assert p.fmt == "CHIRP" and not p.warnings
    e = p.entries
    assert len(e) == 5
    assert e[0].rx == e[0].tx == 14550000 and e[0].power_high
    assert e[1].tx == 14512500 and e[1].tx_tone == "123.0" and e[1].rx_tone == "" and not e[1].power_high
    assert e[2].tx == 43895000 and e[2].rx_tone == e[2].tx_tone == "67.0" and e[2].narrow and not e[2].scan
    assert e[3].tx_tone == "D047N" and e[3].rx_tone == "D047I"
    assert e[4].rx_only and e[4].modulation == 1


def test_free_csv_french():
    txt = ("Indicatif;Ville;Fréquence;Décalage;CTCSS\n"
           "F1ZXX;Nice;145,6375;-600;67\n"
           "F5ZYY;Grasse;430,050;+9,4;88,5\n"
           "F6ZZZ;Cannes;145.400;;\n")
    p = FP.parse_plan(txt)
    assert p.fmt == "CSV" and not p.warnings
    a, b, c = p.entries
    assert a.name == "F1ZXX Nice" and a.tx == 14503750 and a.tx_tone == "67.0"
    assert b.tx == 43945000 and b.tx_tone == "88.5"
    assert c.tx == c.rx == 14540000


def test_text():
    p = FP.parse_plan("# plan ADRASEC\n145,500 S20\n430.350 438.950 Relais\nfoo\n121,5 SATER 121.5\n")
    assert p.fmt == "texte"
    assert [e.name for e in p.entries] == ["S20", "Relais", "SATER 121.5"]
    assert p.entries[1].tx == 43895000
    assert len(p.warnings) == 1


def test_bad_freq_warns():
    p = FP.parse_plan("Nom;Fréquence\nA;5000\nB;145.5\n")
    assert len(p.entries) == 1 and len(p.warnings) == 1


def test_apply():
    cp = S.StockCodeplug(bytes(make_stock_spi()[:S.IMAGE_SIZE]))
    n0 = len(cp.channels)
    p = FP.parse_plan(CHIRP)
    slots, notes = FP.apply_plan(cp, p.entries, FP.ImportOptions(start=1, zone_name="Plan test"))
    # S20 (145,500 simplex) existe déjà dans le codeplug : non ajouté
    assert len(slots) == 4 and len(cp.channels) == n0 + 4
    assert 0 not in slots and 1 not in slots               # emplacements occupés respectés
    sater = cp.channels[slots[-1]]
    assert sater.rx_tx == 1 and sater.modulation == 1 and not sater.power_high
    assert cp.channels[slots[0]].rx_tx == 0                # 145,725 : émission autorisée
    z = [z for z in cp.zones.values() if z.name == "Plan test"]
    assert z and z[0].channels == slots
    cp.commit()                                            # encodage puis relecture
    assert cp.channels[slots[-1]].rx_tx == 1
    assert [z for z in cp.zones.values() if z.name == "Plan test"][0].channels == slots
