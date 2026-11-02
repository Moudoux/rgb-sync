"""The generic interface every RGB device module implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

Color = tuple[int, int, int]


class DeviceNotFound(Exception):
    """The device isn't connected."""


class RGBDevice(ABC):
    """One physical RGB device.

    Each subclass lives in its own module in ``rgbsync/devices/`` and is found
    automatically: adding a module there adds a device, deleting it removes
    one, and ``enabled = False`` keeps the module but skips the device.
    """

    name: ClassVar[str]                                   # CLI name, e.g. "firefly"
    label: ClassVar[str]                                  # human-readable name
    usb_ids: ClassVar[tuple[tuple[int, int], ...]] = ()   # (vendor, product) pairs
    enabled: ClassVar[bool] = True
    # Parts that can show different colours, e.g. so each can get its own
    # colour correction. Single-zone devices keep the default.
    zones: ClassVar[tuple[str, ...]] = ("all",)

    @abstractmethod
    def locate(self) -> str | None:
        """Where the device is (e.g. its hidraw nodes), or None if absent."""

    @abstractmethod
    def set_color(self, color: Color) -> None:
        """Show one static colour on every LED. Raise DeviceNotFound if absent."""

    def set_zone_colors(self, colors: dict[str, Color]) -> None:
        """Show a static colour per zone (keys are `zones`). Multi-zone devices
        override this; single-zone devices get it for free."""
        if len(self.zones) != 1:
            raise NotImplementedError(f"{type(self).__name__} must implement set_zone_colors")
        self.set_color(colors[self.zones[0]])

    @abstractmethod
    def off(self) -> None:
        """Turn every LED off. Raise DeviceNotFound if absent."""
