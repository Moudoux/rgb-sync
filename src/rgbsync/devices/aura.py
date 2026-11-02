"""ASUS Aura USB mainboard LED controller (0b05:18f3).

liquidctl lists this product ID only as untested, with a fixed channel layout
copied from another board, so this driver is our own.

Reference: OpenRGB Controllers/AsusAuraUSBController/AsusAuraUSBController/
(AsusAuraUSBController.cpp, AsusAuraMainboardController.cpp,
RGBController_AsusAuraUSB.cpp).

- 65-byte output reports; byte 0 is the report ID 0xEC
- firmware:      EC 82            -> reply EC 02 <16-byte string>
- config table:  EC B0            -> reply EC 30 .. ..  <60-byte table from byte 4>
    table[0x02] number of addressable (ARGB) headers
    table[0x1B] number of fixed LEDs (onboard + 12 V RGB headers)
    table[0x1D] how many of those fixed LEDs are 12 V RGB headers
- channels: the fixed zone first (effect channel 0, or after the ARGB
  channels on AULA3-AR32 firmware), then one per ARGB header. In the colour
  packet's LED index space the fixed zone takes num_leds slots and each ARGB
  header one slot.
- "gen1" setup, sent once:  EC 52 53 00 01
- effect:  EC 35 <effect channel> 00 <shutdown> <mode>
- colour:  EC 36 <mask hi> <mask lo> <shutdown> then RGB triplets at
           5 + 3 * start_led; mask = ((1 << count) - 1) << start_led
- commit (save to flash): EC 3F 55 - deliberately not sent, see set_color.
"""

from __future__ import annotations

import logging
import os
import select
from dataclasses import dataclass

from ..base import Color, DeviceNotFound
from ..hidraw import HidrawDevice

log = logging.getLogger("rgb")

REPORT_ID = 0xEC
REPORT_LEN = 65

MODE_OFF = 0x00
MODE_STATIC = 0x01


@dataclass
class Channel:
    kind: str            # "fixed" or "argb"
    effect_channel: int
    start_led: int       # index in the colour packet's LED space
    num_leds: int
    num_rgb_headers: int = 0

    def describe(self) -> str:
        if self.kind == "fixed":
            onboard = self.num_leds - self.num_rgb_headers
            return (f"fixed zone (effect channel {self.effect_channel}): "
                    f"{onboard} onboard LEDs + {self.num_rgb_headers} 12 V RGB headers")
        return f"ARGB header (effect channel {self.effect_channel})"


class Aura(HidrawDevice):
    name = "aura"
    label = "ASUS Aura LED controller"
    vid, pid = 0x0B05, 0x18F3

    TIMEOUT_S = 1.0

    def __init__(self) -> None:
        self._fd: int | None = None
        self.firmware = ""
        self.channels: list[Channel] = []

    # -- transport ---------------------------------------------------------- #

    def _open(self) -> None:
        nodes = self.nodes()
        if not nodes:
            raise DeviceNotFound(self.label)
        self._fd = os.open(nodes[0].path, os.O_RDWR)

    def _close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def _write(self, data: list[int]) -> None:
        buf = bytearray(REPORT_LEN)
        buf[0] = REPORT_ID
        buf[1:len(data) + 1] = bytes(data)
        os.write(self._fd, bytes(buf))

    def _query(self, command: int, reply: int) -> bytes:
        # Throw away any stale input reports first.
        while select.select([self._fd], [], [], 0)[0]:
            os.read(self._fd, REPORT_LEN)
        self._write([command])
        while select.select([self._fd], [], [], self.TIMEOUT_S)[0]:
            data = os.read(self._fd, REPORT_LEN)
            if len(data) > 1 and data[0] == REPORT_ID and data[1] == reply:
                return data
        raise OSError(f"no reply to command {command:#04x}")

    # -- setup -------------------------------------------------------------- #

    def _read_config(self) -> None:
        fw = self._query(0x82, 0x02)
        self.firmware = fw[2:18].split(b"\0", 1)[0].decode("ascii", "replace")

        table = self._query(0xB0, 0x30)[4:64]
        log.debug("aura: firmware %s, config table %s", self.firmware, table.hex(" "))
        num_argb = table[0x02]
        num_fixed = table[0x1B]
        num_rgb_headers = table[0x1D]
        if num_fixed < num_rgb_headers:
            num_rgb_headers = 0

        argb_channels_from_zero = self.firmware.startswith("AULA3-AR32")
        channels = []
        effect_channel = 0
        if num_fixed > 0:
            channels.append(Channel("fixed", num_argb if argb_channels_from_zero else 0,
                                    0, num_fixed, num_rgb_headers))
            if not argb_channels_from_zero:
                effect_channel += 1
        start = num_fixed
        for _ in range(num_argb):
            channels.append(Channel("argb", effect_channel, start, 1))
            effect_channel += 1
            start += 1
        if start > 16:
            raise OSError(f"config table describes {start} LED slots; the colour mask holds 16")
        self.channels = channels

    def _connect(self) -> None:
        self._open()
        self._read_config()
        self._write([0x52, 0x53, 0x00, 0x01])   # OpenRGB's SetGen1()

    # -- effect mode -------------------------------------------------------- #

    def _set_mode(self, ch: Channel, mode: int, color: Color) -> None:
        self._write([0x35, ch.effect_channel, 0x00, 0x00, mode])
        mask = ((1 << ch.num_leds) - 1) << ch.start_led
        packet = [0x36, mask >> 8, mask & 0xFF, 0x00] + [0] * (3 * ch.start_led)
        packet += list(color) * ch.num_leds
        self._write(packet)

    def _apply(self, mode: int, color: Color) -> None:
        try:
            self._connect()
            for ch in self.channels:
                log.debug("aura: %s -> mode %d %s", ch.describe(), mode, color)
                self._set_mode(ch, mode, color)
        finally:
            self._close()

    def set_color(self, color: Color) -> None:
        # Not committed to flash (EC 3F 55): follow-accent would wear it out,
        # and the colour is re-applied at login anyway.
        self._apply(MODE_STATIC, color)

    def off(self) -> None:
        self._apply(MODE_OFF, (0, 0, 0))

    def locate(self) -> str | None:
        where = super().locate()
        if where is None:
            return None
        try:
            self._open()
            self._read_config()   # read-only: no setup packet
            return f"{where}; firmware {self.firmware}; " + "; ".join(
                ch.describe() for ch in self.channels)
        except OSError as e:
            return f"{where}; can't read config: {e}"
        finally:
            self._close()
