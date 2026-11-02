"""Device modules. Every module here (not starting with "_") is imported and
every concrete RGBDevice subclass defined in it is registered."""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from ..base import RGBDevice

log = logging.getLogger("rgb")


def discover() -> dict[str, type[RGBDevice]]:
    found: dict[str, type[RGBDevice]] = {}
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as e:  # noqa: BLE001 - a broken module mustn't take the others down
            log.error("device module %r failed to load: %s", info.name, e)
            continue
        for obj in vars(module).values():
            if (inspect.isclass(obj) and issubclass(obj, RGBDevice)
                    and obj.__module__ == module.__name__
                    and not inspect.isabstract(obj) and obj.enabled):
                if obj.name in found:
                    log.error("device name %r defined twice; ignoring %s", obj.name, module.__name__)
                    continue
                found[obj.name] = obj
    return dict(sorted(found.items()))
