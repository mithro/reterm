# reTerminal Kiosk Setup

## Overview

The reTerminal (Seeed Studio, CM4108032) runs a kiosk displaying Home Assistant
using cage (Wayland compositor) + Chromium. Images are built generically on CI
and customized per-device at flash time.

## Hardware

- **SoC**: BCM2711, CM4108032 (8 GB RAM, 32 GB eMMC, WiFi)
- **Display**: 5" DSI, 720x1280 native (rotated 270 deg for landscape)
- **Touch**: Goodix capacitive, I2C address 0x45, via Seeed `mipi_dsi` DKMS driver
- **Light sensor**: LTR-303ALS, IIO device at `iio:device0`
- **Backlight**: I2C controlled, `/sys/class/backlight/1-0045/brightness` (0-255)
- **Buttons**: 4x GPIO keys (keycodes 30-33 = A/S/D/F) + power button (keycode 142 = KEY_SLEEP); event number varies between boots
- **Serial console**: GPIO UART on `/dev/ttyAMA0` at 115200 baud

## Build Architecture

Two-stage process:

1. **`build-image.py`** (runs on CI) — produces a generic `reterminal-base.img.xz` with all software pre-installed. No secrets, no per-device config.
2. **`flash.py <hostname>`** (runs locally) — extracts the generic image, injects device-specific config (hostname, WiFi PSK, SSH keys, dashboard URL), and flashes to eMMC.

See [build.md](build.md) for detailed build/flash instructions.

## Kiosk Architecture

```
systemd (cage-kiosk@tty7.service)
  -> cage -s (Wayland compositor, via seatd)
    -> wlr-randr --output DSI-1 --transform 270
    -> chromium --kiosk --no-sandbox --ozone-platform=wayland
```

### Key Files on Device

| File | Purpose |
|------|---------|
| `/etc/systemd/system/cage-kiosk@.service` | Cage kiosk systemd template |
| `/usr/local/bin/kiosk-browser` | Display rotation + chromium launch |
| `/etc/pam.d/cage` | PAM config for cage logind session |
| `/usr/local/bin/backlight-manager` | Ambient light -> backlight control |
| `/etc/systemd/system/backlight-manager.service` | Backlight manager unit |
| `/usr/local/bin/power-button-handler` | Power button: 5s = restart browser, 10s = reboot |
| `/etc/systemd/system/power-button-handler.service` | Power button handler unit |
| `/usr/local/share/kiosk/hide-cursor-extension/` | Chromium extension to hide cursor |
| `/usr/share/icons/transparent/` | Transparent cursor theme |

### Source Files (this repo)

Production deployment files are in `kiosk/`:

| Source | Deployed to |
|--------|------------|
| `kiosk/cage-kiosk@.service` | `/etc/systemd/system/` |
| `kiosk/kiosk-browser.template` | `/usr/local/bin/kiosk-browser` (URL injected at flash time) |
| `kiosk/backlight-manager` | `/usr/local/bin/` |
| `kiosk/backlight-manager.service` | `/etc/systemd/system/` |
| `kiosk/power-button-handler` | `/usr/local/bin/` |
| `kiosk/power-button-handler.service` | `/etc/systemd/system/` |
| `kiosk/hide-cursor-extension/` | `/usr/local/share/kiosk/hide-cursor-extension/` |
| `kiosk/create_transparent_cursor.py` | Used at build time only |
| `kiosk/disable_tp_rotate.py` | Patch reference (applied at build time) |

Debug/test scripts live in `tools/` and are not deployed to devices.

## Fixes Applied

### Touchscreen Fixes (Two Issues)

#### 1. I2C Transaction Type: Separate Required

The Seeed `mipi_dsi` driver's `i2c_md_read()` uses two separate I2C transfers
(STOP between address write and data read). This is the **correct** approach
for the reTerminal's STM32 MCU at address 0x45.

**Important**: Combined I2C transactions (repeated START via `i2c_transfer()`
with `msgs[2]`) do NOT work with this STM32. Testing proved:
- Separate transactions (STOP between write/read): touch detected correctly
- Combined transactions (repeated START): always reads status=0x00

The original Seeed `i2c_md_read()` code is correct. No patching needed.

#### 2. Touch Rotation: Disable `x_y_rotate()`

The driver's `x_y_rotate()` function (enabled by `tp_point_rotate=1` in the
device tree) rotates touch coordinates 90 degrees. But cage's `wlr-randr
--transform 270` also rotates touch input. This causes **double rotation**:
- Center of screen: correct (rotation around center is identity)
- Corners: touch coordinates wildly off (616px average error)

**Fix**: Comment out the `x_y_rotate()` call in `touch_panel.c`. This is
applied automatically by `build-image.py` during the Seeed driver build.

After both fixes, calibration shows avg 15.7px accuracy (max 40.8px).

#### Rebuilding the Module (manual, on device)

```bash
cd /opt/seeed-linux-dtoverlays/modules/mipi_dsi
sudo make -C /lib/modules/$(uname -r)/build M=$(pwd) modules
sudo cp mipi_dsi.ko /lib/modules/$(uname -r)/updates/dkms/
sudo rm -f /lib/modules/$(uname -r)/updates/dkms/mipi_dsi.ko.xz
sudo xz /lib/modules/$(uname -r)/updates/dkms/mipi_dsi.ko
sudo depmod -a
```

Note: The kernel prefers `.ko.xz` over `.ko` files. Always compress with `xz`.

### Chromium in systemd Service

Chromium needs several workarounds when launched from a systemd service:

1. `--no-sandbox` - zygote sandbox fails without proper user namespace setup
2. `--user-data-dir=/home/tim/.config/chromium` - HOME not inherited
3. `Environment=HOME=/home/tim` - chromium needs HOME for crash dumps
4. `Environment=DBUS_SESSION_BUS_ADDRESS=/dev/null` - suppress D-Bus errors
5. `LIBSEAT_BACKEND=seatd` - cage uses seatd, not logind
6. `--overscroll-history-navigation=0` - disable swipe back/forward (interferes with touch)
7. `--pull-to-refresh=0` - disable pull-to-refresh gesture
8. `--load-extension=/usr/local/share/kiosk/hide-cursor-extension` - hide mouse cursor via CSS
9. URL gets `?cb=$(date +%s)` cache buster appended at each launch

### cage + seatd (not logind)

cage 0.2.0 on Debian Trixie uses seatd for DRM/input access.
The service requires `seatd.service` and sets `LIBSEAT_BACKEND=seatd`.
User `tim` must be in groups: `video`, `render`, `input`.

## Backlight Manager

Python script that:
- Reads ambient light every 2s from LTR-303ALS
- Maps lux to brightness: <4 lux = off, 4-500 lux = linear 50-255
- Monitors GPIO buttons for wake: any press = brightness 200 for 5 minutes
- 3-reading smoothing window prevents oscillation at boundary
- Uses `select()` on gpio_keys device (auto-detected, no extra dependencies)

## Power Button Handler

Python script that monitors the power button (KEY_SLEEP, keycode 142):
- **Hold 5 seconds**: restart kiosk browser (stop + clean wayland socket + start)
- **Hold 10 seconds**: reboot device (triggers immediately, no need to release)
- Actions trigger during hold, not on release

The cage service uses `KillSignal=SIGKILL` for instant stop (no graceful shutdown
needed for a kiosk). Stale Wayland sockets are cleaned via `ExecStartPre`.

## USB Gadget Serial - NOT WORKING

**Status**: USB gadget mode does not work on the reTerminal. This is a
hardware limitation of the board design.

**Investigation summary** (2026-02-15):
- DWC2 controller at `fe980000.usb` correctly configured (ForceDevMode,
  dr_mode=peripheral, DCTL.sftdiscon=0, GOTGCTL.bsesvld=1)
- Full core soft reset + rebind + soft connect -> no host response
- 5 rapid D+ disconnect/reconnect cycles -> zero USB events on host
- Only ~24 IRQ triggers total (should be hundreds)
- No VL805 on PCIe (`lspci` empty) - USB-A ports likely share OTG pins
- [Seeed forum confirms: nobody has gotten gadget mode working](https://forum.seeedstudio.com/t/reterminal-usb-otg/262669)

**Why rpiboot works**: The BCM2711 boot ROM has its own USB implementation
at the silicon level, which drives D+/D- before the board's USB hub powers up.

**Alternative**: GPIO serial on `/dev/ttyAMA0` at 115200 baud works correctly
as a debug console. Use a USB-to-serial adapter connected to GPIO pins 8
(TX), 10 (RX), 6 (GND).

**Important**: `modules-load=dwc2,g_serial` and `console=ttyGS0,115200` must be
removed from `cmdline.txt`. The ttyGS0 agetty acquires a `flock()` on
`/dev/console` which blocks the ttyAMA0 agetty from displaying its login
prompt. Since USB gadget doesn't work on this hardware, the ttyGS0 getty
holds the lock indefinitely. The serial-getty@ttyGS0 service should also
be masked (`systemctl mask serial-getty@ttyGS0`).

## Peripheral Status

| Peripheral | Status | Notes |
|-----------|--------|-------|
| DSI Display | Working | 720x1280, rotated 270 deg via wlr-randr |
| Touchscreen | Working | Separate I2C, x_y_rotate disabled (cage handles rotation) |
| GPIO Buttons | Working | A/S/D/F keys via gpio_keys (event number varies between boots) |
| Light Sensor | Working | LTR-303ALS, reads lux via IIO |
| Backlight | Working | I2C controlled, 0-255 range |
| GPIO Serial | Working | ttyAMA0 at 115200, CH341 adapter |
| USB Gadget | NOT WORKING | Hardware limitation, see above |
| WiFi | Working | ansells-iot network |

## Services

```bash
# Start/stop kiosk
sudo systemctl start cage-kiosk@tty7
sudo systemctl stop cage-kiosk@tty7

# Check status
sudo systemctl status cage-kiosk@tty7
sudo systemctl status backlight-manager
sudo systemctl status power-button-handler

# View logs
sudo journalctl -u cage-kiosk@tty7 -f
sudo journalctl -u backlight-manager -f
sudo journalctl -u power-button-handler -f
```
