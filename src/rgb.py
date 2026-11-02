#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "jeepney>=0.8",
#     "liquidctl>=1.16",
# ]
# ///
"""Set the RGB lighting on this PC's devices, optionally following the KDE
Plasma accent colour.

Devices are the modules in rgbsync/devices/. The keyboard is deliberately left
alone: KDE's Kameleon module drives it.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

from rgbsync.base import Color, DeviceNotFound, RGBDevice
from rgbsync.config import load_corrections, zone_colors
from rgbsync.devices import discover

log = logging.getLogger("rgb")


def parse_color(text: str) -> Color:
    s = text.strip().removeprefix("#")
    if re.fullmatch(r"[0-9A-Fa-f]{6}", s):
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    parts = [p.strip() for p in s.split(",")]
    if len(parts) == 3 and all(p.isdigit() and int(p) <= 255 for p in parts):
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    raise argparse.ArgumentTypeError(f"not a colour: {text!r} (use RRGGBB or R,G,B)")


def hex_color(c: Color) -> str:
    return "#{:02X}{:02X}{:02X}".format(*c)


def kdeglobals_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kdeglobals"


def read_accent_color() -> Color | None:
    """AccentColor=R,G,B from the [General] group of kdeglobals."""
    try:
        text = kdeglobals_path().read_text()
    except OSError as e:
        log.error("can't read %s: %s", kdeglobals_path(), e)
        return None
    group = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1]
        elif group == "General" and line.split("=", 1)[0].split("[", 1)[0].strip() == "AccentColor":
            try:
                return parse_color(line.split("=", 1)[1])
            except argparse.ArgumentTypeError:
                log.error("unexpected AccentColor value: %r", line)
                return None
    return None


def apply(devices: list[type[RGBDevice]], color: Color | None, corrected: bool = True,
          missing_is_failure: bool = False) -> list[type[RGBDevice]]:
    """Set (or, with color=None, turn off) each device, applying the colour
    correction from the config file. One failing device doesn't stop the rest.
    Returns the devices that failed; absent devices count as failed only with
    missing_is_failure (after resume, when they may still be re-enumerating)."""
    corrections = load_corrections() if corrected else {}
    failed = []
    for cls in devices:
        try:
            dev = cls()
            if color is None:
                dev.off()
                log.info("%s: off", cls.name)
            else:
                colors = zone_colors(cls.name, cls.zones, color, corrections)
                dev.set_zone_colors(colors)
                adjusted = ", ".join(f"{z} {hex_color(c)}" for z, c in colors.items() if c != color)
                log.info("%s: %s%s", cls.name, hex_color(color),
                         f" (corrected: {adjusted})" if adjusted else "")
        except DeviceNotFound:
            if missing_is_failure:
                failed.append(cls)
                log.warning("%s: not present (yet)", cls.name)
            else:
                log.warning("%s: not present, skipped", cls.name)
        except PermissionError as e:
            failed.append(cls)
            log.error("%s: permission denied (%s) - is the udev rule installed?",
                      cls.name, e.filename or e)
        except Exception as e:  # noqa: BLE001 - report and carry on with the others
            failed.append(cls)
            log.error("%s: %s", cls.name, e)
    return failed


RESUME_DELAY_S = 3      # let USB devices re-enumerate after resume
RESUME_RETRIES = 5


def follow_accent(devices: list[type[RGBDevice]]) -> int:
    """Apply the accent colour at startup, whenever KDE reports a change to
    it, and after resume from suspend (the Corsair loses its colours whenever
    it powers off)."""
    import queue
    import threading
    import time

    from jeepney import MatchRule
    from jeepney.bus_messages import message_bus
    from jeepney.io.blocking import open_dbus_connection

    events: queue.Queue[tuple[str, object]] = queue.Queue()

    def listen(bus: str, rule: MatchRule, classify) -> None:
        # Subscribe before returning so no signal is missed; messages queue up
        # in the socket until the thread starts reading.
        conn = open_dbus_connection(bus=bus)
        conn.send_and_get_reply(message_bus.AddMatch(rule))

        def run() -> None:
            try:
                with conn.filter(rule) as q:
                    while True:
                        reason = classify(conn.recv_until_filtered(q))
                        if reason:
                            events.put((reason, None))
            except Exception as e:  # noqa: BLE001 - hand it to the main thread
                events.put(("error", e))

        threading.Thread(target=run, name=f"dbus-{bus.lower()}", daemon=True).start()

    def accent_changed(msg) -> str | None:
        # Body is a{saay}: group name -> changed key names (as bytes).
        if not msg.body:
            return "changed"
        keys = msg.body[0].get("General", [])
        return "changed" if any(bytes(k).rstrip(b"\0") == b"AccentColor" for k in keys) else None

    def resumed(msg) -> str | None:
        return "resume" if msg.body and msg.body[0] is False else None

    listen("SESSION", MatchRule(type="signal", interface="org.kde.kconfig.notify",
                                member="ConfigChanged", path="/kdeglobals"), accent_changed)
    try:
        listen("SYSTEM", MatchRule(type="signal", interface="org.freedesktop.login1.Manager",
                                   member="PrepareForSleep", path="/org/freedesktop/login1"),
               resumed)
    except Exception as e:  # noqa: BLE001
        log.warning("can't watch for resume from suspend: %s", e)

    last: Color | None = None

    def sync(reason: str) -> None:
        nonlocal last
        color = read_accent_color()
        if color is None:
            log.warning("no AccentColor in %s", kdeglobals_path())
            return
        if reason == "changed" and color == last:
            return
        log.info("accent colour %s (%s)", hex_color(color), reason)
        resume = reason == "resume"
        pending = devices
        for attempt in range(RESUME_RETRIES if resume else 1):
            if resume:
                time.sleep(RESUME_DELAY_S)
                if attempt:
                    log.info("retrying %s (%d/%d)", ", ".join(c.name for c in pending),
                             attempt + 1, RESUME_RETRIES)
            pending = apply(pending, color, missing_is_failure=resume)
            if not pending:
                last = color
                return
        log.error("gave up on %s", ", ".join(c.name for c in pending))
        last = None   # make the next change notification try again

    sync("startup")
    while True:
        reason, error = events.get()
        if reason == "error":
            log.error("D-Bus listener failed: %s", error)
            return 1
        sync(reason)


def main(argv: list[str] | None = None) -> int:
    registry = discover()

    p = argparse.ArgumentParser(prog="rgb", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    device_opt = dict(action="append", choices=list(registry), dest="devices",
                      help="limit to this device (repeatable; default: all)")

    s = sub.add_parser("set", help="set a static colour")
    s.add_argument("--device", "-d", **device_opt)
    s.add_argument("--no-correction", dest="corrected", action="store_false",
                   help="ignore the colour correction in the config file")
    s.add_argument("color", type=parse_color, help="RRGGBB, #RRGGBB or R,G,B")

    o = sub.add_parser("off", help="turn the LEDs off")
    o.add_argument("--device", "-d", **device_opt)

    sub.add_parser("list", help="show supported devices and where they are")

    f = sub.add_parser("follow-accent", help="apply the KDE accent colour and track changes")
    f.add_argument("--device", "-d", **device_opt)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s" if sys.stderr.isatty()
                        else "%(message)s")
    if not args.verbose:
        logging.getLogger("liquidctl").setLevel(logging.WARNING)

    if args.command == "list":
        for name, cls in registry.items():
            print(f"{name:10}  {cls.label:32}  {cls().locate() or 'not present'}")
            if len(cls.zones) > 1:
                print(f"{'':10}  zones: {', '.join(cls.zones)}")
        return 0

    devices = [registry[n] for n in (getattr(args, "devices", None) or registry)]
    if args.command == "set":
        return 1 if apply(devices, args.color, args.corrected) else 0
    if args.command == "off":
        return 1 if apply(devices, None) else 0
    if args.command == "follow-accent":
        return follow_accent(devices)
    return 2


if __name__ == "__main__":
    sys.exit(main())
