"""Razer Firefly mouse mat (1532:0c00).

Reference: OpenRazer driver/razeraccessory_driver.c (the former razermousemat
driver), razercommon.[ch] and razerchromacommon.c.

- 90-byte report: status, transaction_id, remaining_packets (BE16),
  protocol_type, data_size, command_class, command_id, args[80], crc, 0
- crc = XOR of bytes 2..87
- the Firefly uses transaction ID 0x3F
- sent as a feature report (SET_REPORT, wValue 0x300, report ID 0) on
  interface 0; the response is read back with GET_REPORT after ~600 us
- static: class 0x03, cmd 0x0A, size 4, args [0x06, R, G, B]
- none:   class 0x03, cmd 0x0A, size 1, args [0x00]
"""

from __future__ import annotations

import fcntl
import os
import time

from ..base import Color, DeviceNotFound
from ..hidraw import HIDIOCGFEATURE, HIDIOCSFEATURE, HidrawDevice, HidrawNode


class Firefly(HidrawDevice):
    name = "firefly"
    label = "Razer Firefly mouse mat"
    vid, pid = 0x1532, 0x0C00

    REPORT_LEN = 90
    TRANSACTION_ID = 0x3F
    WAIT_S = 0.0006
    STATUS = {0x00: "new", 0x01: "busy", 0x02: "ok", 0x03: "failure",
              0x04: "timeout", 0x05: "not supported"}

    def control_node(self) -> HidrawNode:
        # OpenRazer talks to the device on interface 0 (USB protocol "mouse").
        for node in self.nodes():
            if node.interface == 0:
                return node
        raise DeviceNotFound(self.label)

    @classmethod
    def build_report(cls, command_class: int, command_id: int, args: list[int]) -> bytes:
        r = bytearray(cls.REPORT_LEN)
        r[1] = cls.TRANSACTION_ID
        r[5] = len(args)
        r[6] = command_class
        r[7] = command_id
        r[8:8 + len(args)] = bytes(args)
        crc = 0
        for b in r[2:88]:
            crc ^= b
        r[88] = crc
        return bytes(r)

    def _send(self, command_class: int, command_id: int, args: list[int]) -> None:
        node = self.control_node()
        report = self.build_report(command_class, command_id, args)
        fd = os.open(node.path, os.O_RDWR)
        try:
            last = "no response"
            for _ in range(5):
                # Report ID 0 prefix, then the 90-byte report.
                fcntl.ioctl(fd, HIDIOCSFEATURE(1 + self.REPORT_LEN), b"\x00" + report)
                time.sleep(self.WAIT_S)
                buf = bytearray(1 + self.REPORT_LEN)
                fcntl.ioctl(fd, HIDIOCGFEATURE(len(buf)), buf, True)
                resp = buf[1:]
                if resp[2:4] == report[2:4] and resp[6:8] == report[6:8]:
                    if resp[0] in (0x01, 0x02):   # busy also means success
                        return
                    last = self.STATUS.get(resp[0], f"status {resp[0]:#04x}")
                else:
                    last = "response doesn't match request"
                time.sleep(0.01)
            raise OSError(f"command failed ({last})")
        finally:
            os.close(fd)

    def set_color(self, color: Color) -> None:
        self._send(0x03, 0x0A, [0x06, *color])

    def off(self) -> None:
        self._send(0x03, 0x0A, [0x00])

    def locate(self) -> str | None:
        nodes = self.nodes()
        if not nodes:
            return None
        return ", ".join(
            f"{n.path} (interface {n.interface}{', control' if n.interface == 0 else ''})"
            for n in nodes
        )
