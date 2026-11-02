# rgb-sync

A small tool that sets the RGB lighting on this PC's devices to one colour, and
can keep them on the KDE Plasma accent colour.

| Device | USB ID | Name in `rgb` | Driven by | What lights up |
|---|---|---|---|---|
| Razer Firefly mouse mat | `1532:0c00` | `firefly` | own code (OpenRazer protocol) | the mat |
| ASUS Aura LED controller (ROG Strix X570-F) | `0b05:18f3` | `aura` | own code (OpenRGB protocol) | onboard LEDs, both 12 V RGB headers (the two Phanteks strips), both ARGB headers |
| Corsair H115i Platinum | `1b1c:0c17` | `corsair` | liquidctl | pump head and both radiator fans |

The keyboard (Keychron Q3 Max) is left to KDE's Kameleon module
(`DeviceLedsAccentColored=true` in `~/.config/kdeglobals`). This tool never
touches it.

### Device notes

- **Aura.** liquidctl lists `0b05:18f3` only as untested, with a fixed channel
  layout copied from another board, so this driver is our own. It reads the
  controller's config table first. On this board: one fixed zone of 3 onboard
  LEDs plus two 12 V RGB headers (which share one colour), and two ARGB
  headers. Both Phanteks strips are on a 12 V header, so they always match the
  onboard LEDs. Colours aren't saved to the controller's flash: until
  `follow-accent` runs at login, the board shows its stored colour.
- **Corsair: cooling is never touched.** Nothing on this PC configures the
  cooler, so it runs its firmware defaults (pump 100% duty; fans on the
  firmware's temperature curve). The `corsair` module only calls liquidctl's
  `set_color`, which sends lighting messages only. It never calls
  `initialize` (which would set a pump mode and default the fans to 100%), and
  never sends raw packets. To confirm the cooling settings are untouched, compare
  `liquidctl status` before and after `rgb set -d corsair ...`. The cooler forgets
  its colours when it powers off, which `follow-accent` handles at login and
  after resume.
- **Corsair pump shade.** The pump head's blue LEDs are much weaker than its
  red and green ones, so the same colour looks warmer (white looks yellow) on
  the pump than on the fans. The tool corrects for this per zone (see
  [Colour correction](#colour-correction)). The `corsair` module sets the 24
  LEDs individually (liquidctl's `super-fixed` mode, still lighting messages
  only): 16 pump LEDs, then 4 per fan.

## Layout

```
src/rgb.py                  main script: CLI and accent following
src/rgbsync/base.py         RGBDevice, the interface every device implements
src/rgbsync/hidraw.py       hidraw lookup by USB ID, ioctls, HidrawDevice base
src/rgbsync/config.py       config file and per-zone colour correction
src/rgbsync/devices/*.py    one module per device, found automatically
data/70-rgb-sync.rules      udev rule giving your user access to the devices
data/rgb-sync.service       systemd user unit for `rgb follow-accent`
data/config.example.toml    example config (colour correction)
arch/PKGBUILD               Arch package
```

## Install

### Option A: Arch package (recommended)

```sh
cd arch
makepkg -si
```

This installs:

- `/usr/bin/rgb`
- the code in `/usr/lib/rgb-sync/`
- the udev rule in `/usr/lib/udev/rules.d/`
- the user unit in `/usr/lib/systemd/user/`
- the example config in `/usr/share/doc/rgb-sync/`

Dependencies come from pacman (`python-jeepney`, `liquidctl`), not from PyPI.

pacman doesn't reload udev rules. After the first install, run this once so
the rule takes effect without a reboot:

```sh
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=hidraw
```

After changing the code, bump `pkgver` or `pkgrel` in `arch/PKGBUILD` and run
`makepkg -si` again.

### Option B: run from the source tree with uv

`src/rgb.py` declares its dependencies as PEP 723 inline metadata, so `uv`
installs them on first run. Nothing else to manage.

```sh
./src/rgb.py list
ln -s "$PWD/src/rgb.py" ~/.local/bin/rgb   # optional
```

Install the udev rule by hand:

```sh
sudo install -m 644 data/70-rgb-sync.rules /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=hidraw
```

Check it worked. `ls -l /dev/hidraw*` should show a `+` after the permissions
of the Firefly, Aura and Corsair nodes (an ACL for your user), and
`./src/rgb.py list` should show them.

## Usage

```sh
rgb list                         # supported devices, their hidraw nodes and zones
rgb set E93D58                   # all devices
rgb set '#E93D58'                # same; also accepts 233,61,88
rgb set --device firefly 00FF00  # one device (-d is repeatable)
rgb set --no-correction FFFFFF   # skip the colour correction (for comparing)
rgb off                          # all devices off
rgb off -d firefly
rgb follow-accent                # apply the accent colour, then track changes
rgb -v ...                       # debug output
```

A device that isn't plugged in is skipped with a warning. A device that fails
doesn't stop the others. The exit status is non-zero if any device failed.

## Follow the accent colour automatically

`rgb follow-accent` reads `AccentColor` from `~/.config/kdeglobals` and applies
it. It then waits for KDE's `ConfigChanged` D-Bus signal (interface
`org.kde.kconfig.notify`, path `/kdeglobals`); it doesn't poll. The colour is
also applied at startup, and again after resume from suspend (logind's
`PrepareForSleep` signal), because the Corsair forgets its colours whenever it
powers off.

With the package installed:

```sh
systemctl --user enable --now rgb-sync.service
journalctl --user -u rgb-sync -f      # watch it
```

Without the package, copy the unit and point it at your checkout:

```sh
mkdir -p ~/.config/systemd/user
sed "s|/usr/bin/rgb|$PWD/src/rgb.py|" data/rgb-sync.service > ~/.config/systemd/user/rgb-sync.service
systemctl --user daemon-reload
systemctl --user enable --now rgb-sync.service
```

The unit starts with your Plasma session (`graphical-session.target`) and
restarts if it fails.

## Colour correction

Some LEDs show the same RGB value as a different shade. Corrections live in
`~/.config/rgb-sync/config.toml` (see `data/config.example.toml`) as
`[R, G, B]` factors per device zone:

```toml
[correction.corsair]
pump = [0.12, 0.15, 1.0]
```

Zones are listed by `rgb list`. Single-zone devices use `all`; on multi-zone
devices `all` covers every zone without an entry of its own. The file is read
every time a colour is set, so changes take effect on the next `rgb set` or
accent change, without restarting the service.

To tune a zone, set white (`rgb set FFFFFF`) and compare it with a device
that looks right. If it looks warm or yellow, lower red and green; if it looks
cold or blue, raise them. Factors can only dim a channel, so matching a weak
channel makes that zone darker overall. The current pump value matches the
shade but leaves the pump dimmer than the fans.

## Adding or removing a device

Each device is one module in `src/rgbsync/devices/`. The main script imports
every module there (except names starting with `_`) and registers every
concrete `RGBDevice` subclass it finds.

- **Remove a device:** delete its module, or set `enabled = False` on the class.
- **Add a device:** create a module with a subclass of `RGBDevice` (or
  `HidrawDevice` for hidraw devices). It needs:

  ```python
  from ..base import Color, DeviceNotFound
  from ..hidraw import HidrawDevice

  class MyThing(HidrawDevice):
      name = "mything"           # used with --device
      label = "My RGB thing"
      vid, pid = 0x1234, 0x5678  # HidrawDevice uses these to find the device
      # zones = ("a", "b")       # optional; then also implement set_zone_colors()

      def set_color(self, color: Color) -> None: ...  # raise DeviceNotFound if absent
      def off(self) -> None: ...
  ```

  `locate()` (used by `rgb list`) comes from `HidrawDevice`; non-hidraw devices
  implement it themselves. Also add the device's USB ID to
  `data/70-rgb-sync.rules`, or your user won't have access.

A module that fails to import (for example because a library is missing) is
reported and skipped. The other devices keep working.
