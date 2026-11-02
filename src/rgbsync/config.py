"""User configuration: ~/.config/rgb-sync/config.toml.

Colour correction compensates for LEDs that render the same RGB value as a
different shade (e.g. the Corsair pump head looks yellow next to the fans).
Each entry scales red, green and blue by a factor:

    [correction.corsair]
    pump = [0.5, 0.6, 1.0]      # one zone of a device

    [correction.firefly]
    all = [1.0, 0.9, 1.0]       # "all" = every zone without its own entry
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path

from .base import Color

log = logging.getLogger("rgb")

Gain = tuple[float, float, float]


def config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rgb-sync" / "config.toml"


def _parse_gain(where: str, value) -> Gain | None:
    if (isinstance(value, list) and len(value) == 3
            and all(isinstance(v, (int, float)) and v >= 0 for v in value)):
        return (float(value[0]), float(value[1]), float(value[2]))
    log.error("%s: %s should be three non-negative numbers like [1.0, 0.8, 1.0]",
              config_path(), where)
    return None


def load_corrections() -> dict[str, dict[str, Gain]]:
    """{device: {zone: gain}}. A missing or broken file means no correction."""
    try:
        with open(config_path(), "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as e:
        log.error("ignoring %s: %s", config_path(), e)
        return {}
    result: dict[str, dict[str, Gain]] = {}
    for device, zones in data.get("correction", {}).items():
        if not isinstance(zones, dict):
            log.error("%s: [correction.%s] should be a table of zones", config_path(), device)
            continue
        for zone, value in zones.items():
            gain = _parse_gain(f"correction.{device}.{zone}", value)
            if gain:
                result.setdefault(device, {})[zone] = gain
    return result


def correct(color: Color, gain: Gain | None) -> Color:
    if gain is None:
        return color
    return tuple(min(255, round(c * g)) for c, g in zip(color, gain))  # type: ignore[return-value]


def zone_colors(device: str, zones: tuple[str, ...], color: Color,
                corrections: dict[str, dict[str, Gain]]) -> dict[str, Color]:
    table = corrections.get(device, {})
    return {zone: correct(color, table.get(zone, table.get("all"))) for zone in zones}
