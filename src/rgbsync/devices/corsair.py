"""Corsair H115i Platinum AIO (1b1c:0c17), through liquidctl's Hydro Platinum
driver. Lighting covers the pump head (16 LEDs) and the two ML RGB fans
cabled to it (4 LEDs each).

COOLING SAFETY. On this cooler, lighting and cooling share one family of
messages, and liquidctl's initialize() sets the pump mode and re-sends the fan
settings (unconfigured fans fall back to 100% fixed duty). Nothing on this PC
configures the cooler, so it runs its firmware defaults (pump at 100% duty,
fans on the firmware's own temperature curve), and those must be kept.

So this module only ever calls set_color(), which sends lighting messages
and no cooling message. It never calls initialize(), set_fixed_speed() or
set_speed_profile(), and never sends raw packets of its own.
"""

from __future__ import annotations

import logging

from liquidctl.driver.hydro_platinum import HydroPlatinum

from ..base import Color, DeviceNotFound, RGBDevice
from ..hidraw import find_hidraw

log = logging.getLogger("rgb")


class Corsair(RGBDevice):
    name = "corsair"
    label = "Corsair H115i Platinum AIO"
    vid, pid = 0x1B1C, 0x0C17
    usb_ids = ((vid, pid),)

    # LED order in liquidctl's 24-colour "super-fixed" list, checked on the
    # hardware: 16 pump LEDs, then 4 per fan.
    zones = ("pump", "fan1", "fan2")
    LED_COUNTS = {"pump": 16, "fan1": 4, "fan2": 4}

    def _set(self, mode: str, colors: list[Color]) -> None:
        devices = [d for d in HydroPlatinum.find_supported_devices()
                   if (d.vendor_id, d.product_id) == (self.vid, self.pid)]
        if not devices:
            raise DeviceNotFound(self.label)
        for dev in devices:
            with dev.connect():
                # liquidctl sends a one-off "enable LEDs" sequence (lighting
                # messages only) when its cached flag says the LEDs haven't been
                # enabled since initialize(). The cache lives in
                # $XDG_RUNTIME_DIR and survives suspend while the cooler doesn't,
                # so clear the flag to have that sequence sent every time.
                dev._data.store("leds_enabled", 0)
                dev.set_color("led", mode, colors)

    def set_color(self, color: Color) -> None:
        self.set_zone_colors({zone: color for zone in self.zones})

    def set_zone_colors(self, colors: dict[str, Color]) -> None:
        leds = [colors[zone] for zone in self.zones for _ in range(self.LED_COUNTS[zone])]
        self._set("super-fixed", leds)

    def off(self) -> None:
        self._set("off", [])

    def locate(self) -> str | None:
        nodes = find_hidraw(self.vid, self.pid)
        return ", ".join(n.path for n in nodes) or None
