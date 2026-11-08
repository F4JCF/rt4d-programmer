"""Radio RT-4D simulée (pour les tests, sans matériel)."""

from __future__ import annotations

import os
import threading

from rt4dprog import layout as L

from rt4dprog import stock as S

REGION_ADDR = {r: a for r, a, _, _ in L.SPI_RESTORE_REGIONS}
REGION_ADDR[0x80] = 0x0C7000
STOCK_REGION_ADDR = {r: a for r, a, _, _ in S.WRITE_REGIONS}
from rt4dprog import legacy as O  # noqa: E402
LEGACY_REGION_ADDR = {r: a for r, a, _, _ in O.WRITE_REGIONS}


def _sum(data, base=0):
    s = base
    for b in data:
        s = (s + b) & 0xFF
    return s


class FakeRadio:
    def __init__(self, spi: bytes | None = None, bootloader: bool = False, stock: bool = False,
                 legacy: bool = False):
        self.region_addr = (LEGACY_REGION_ADDR if legacy else
                            STOCK_REGION_ADDR if stock else REGION_ADDR)
        self.stock = stock or legacy
        self.spi = bytearray(spi if spi else b"\xFF" * (4096 * 1024))
        self.bootloader = bootloader
        self.inbuf = bytearray()
        self.out = bytearray()
        self.firmware = bytearray()
        self.erased = False
        self.closed_sessions = 0
        self.writes = []            # (region, block)
        self.addressbook = bytearray()

    # interface type pyserial
    timeout = 0.1

    def write(self, data: bytes):
        self.inbuf.extend(data)
        self._process()
        return len(data)

    def read(self, n: int = 1) -> bytes:
        r = bytes(self.out[:n])
        del self.out[:n]
        return r

    def reset_input_buffer(self):
        self.out.clear()

    def flush(self):
        pass

    def close(self):
        pass

    # logique
    def _process(self):
        while self.inbuf:
            b0 = self.inbuf[0]
            if self.bootloader:
                if b0 == 0xFF:
                    del self.inbuf[0]
                    self.out.append(0xFF)
                elif b0 == 0x39:
                    if len(self.inbuf) < 5:
                        return
                    cmd = self.inbuf[:5]
                    del self.inbuf[:5]
                    assert cmd[4] == _sum(cmd[:4], 0x48)
                    if cmd[3] == 0x55:
                        self.erased = True
                        self.firmware.clear()
                    self.out.append(0x06)
                elif b0 == 0x57:
                    if len(self.inbuf) < 1028:
                        return
                    cmd = self.inbuf[:1028]
                    del self.inbuf[:1028]
                    assert self.erased
                    assert cmd[-1] == _sum(cmd[:-1], 0x48)
                    self.firmware.extend(cmd[3:1027])
                    self.out.append(0x06)
                else:
                    del self.inbuf[0]
                    self.out.append(0xFF)
                continue
            if b0 == 0x52:
                if len(self.inbuf) < 4:
                    return
                cmd = self.inbuf[:4]
                del self.inbuf[:4]
                assert cmd[3] == _sum(cmd[:3])
                kb = (cmd[1] << 8) | cmd[2]
                blk = bytearray([0x52, cmd[1], cmd[2]]) + self.spi[kb * 1024:(kb + 1) * 1024]
                blk.append(_sum(blk))
                self.out.extend(blk)
            elif b0 == 0x34:
                if len(self.inbuf) < 5:
                    return
                cmd = self.inbuf[:5]
                del self.inbuf[:5]
                if cmd[1] == 0x52 and cmd[3] == 0xEE:
                    self.closed_sessions += 1
                else:
                    self.out.append(0x06)
            elif b0 == 0xA4:
                if len(self.inbuf) < 1028:
                    return
                cmd = self.inbuf[:1028]
                del self.inbuf[:1028]
                assert cmd[-1] == _sum(cmd[:-1])
                blk = (cmd[1] << 8) | cmd[2]
                assert blk * 1024 == len(self.addressbook), "blocs dans l'ordre"
                self.addressbook.extend(cmd[3:1027])
                self.out.append(0x06)
            elif b0 in self.region_addr:
                if len(self.inbuf) < 1028:
                    return
                cmd = self.inbuf[:1028]
                del self.inbuf[:1028]
                assert cmd[-1] == _sum(cmd[:-1]), "checksum écriture"
                blk = (cmd[1] << 8) | cmd[2]
                addr = self.region_addr[b0]
                if b0 == 0x90 and not self.stock:   # réglages : banque active
                    for bank in L.SETTINGS_BANKS:
                        if self.spi[bank + 0xFFC:bank + 0x1000] == L.MAGIC:
                            addr = bank
                            break
                a = addr + blk * 1024
                self.spi[a:a + 1024] = cmd[3:1027]
                self.writes.append((b0, blk))
                self.out.append(0x06)
            else:
                del self.inbuf[0]


class PtyRadio(threading.Thread):
    """Expose une FakeRadio sur un pseudo-terminal (Linux) pour tester le vrai pyserial."""

    def __init__(self, radio: FakeRadio):
        super().__init__(daemon=True)
        import tty
        self.radio = radio
        self.master, slave = os.openpty()
        tty.setraw(slave)
        self.port = os.ttyname(slave)
        self._slave = slave
        self.stop = False

    def run(self):
        import select
        while not self.stop:
            r, _, _ = select.select([self.master], [], [], 0.05)
            if r:
                try:
                    data = os.read(self.master, 65536)
                except OSError:
                    break
                self.radio.write(data)
            if self.radio.out:
                out = bytes(self.radio.out)
                self.radio.out.clear()
                os.write(self.master, out)
