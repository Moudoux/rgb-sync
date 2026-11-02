"""Finding and talking to devices through /dev/hidraw*.

hidraw numbers change between boots, so devices are always looked up by USB
vendor and product ID via sysfs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from .base import RGBDevice


def _ioc(direction: int, kind: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(kind) << 8) | nr


def HIDIOCSFEATURE(size: int) -> int:
    return _ioc(3, "H", 0x06, size)


def HIDIOCGFEATURE(size: int) -> int:
    return _ioc(3, "H", 0x07, size)


@dataclass
class HidrawNode:
    path: str               # /dev/hidrawN
    vid: int
    pid: int
    interface: int | None   # USB bInterfaceNumber
    name: str


def find_hidraw(vid: int, pid: int) -> list[HidrawNode]:
    """All hidraw nodes for a USB vendor/product ID, sorted by interface."""
    nodes = []
    for entry in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        dev = entry / "device"
        try:
            uevent = (dev / "uevent").read_text()
        except OSError:
            continue
        fields = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
        m = re.fullmatch(r"[0-9A-Fa-f]+:([0-9A-Fa-f]+):([0-9A-Fa-f]+)", fields.get("HID_ID", ""))
        if not m or (int(m[1], 16), int(m[2], 16)) != (vid, pid):
            continue
        iface = None
        try:
            iface = int((dev.resolve().parent / "bInterfaceNumber").read_text(), 16)
        except (OSError, ValueError):
            pass
        nodes.append(HidrawNode(f"/dev/{entry.name}", vid, pid, iface, fields.get("HID_NAME", "")))
    return sorted(nodes, key=lambda n: (n.interface is None, n.interface, n.path))


class HidrawDevice(RGBDevice):
    """Base for devices driven directly through hidraw."""

    vid: ClassVar[int]
    pid: ClassVar[int]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "vid") and hasattr(cls, "pid"):
            cls.usb_ids = ((cls.vid, cls.pid),)

    def nodes(self) -> list[HidrawNode]:
        return find_hidraw(self.vid, self.pid)

    def locate(self) -> str | None:
        nodes = self.nodes()
        if not nodes:
            return None
        return ", ".join(f"{n.path} (interface {n.interface})" for n in nodes)
