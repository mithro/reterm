# reterm

Reproducible setup for [Seeed Studio reTerminal](https://www.seeedstudio.com/ReTerminal-with-CM4-p-4904.html)
(CM4) kiosk devices: a generic image built on CI, customized per-device at
flash time, running a Home Assistant dashboard fullscreen via cage
(Wayland) + Chromium on the built-in 5" DSI touchscreen.

- **Build & flash**: [docs/build.md](docs/build.md) —
  `build-image.py` (generic image, CI) + `flash.py <hostname>`
  (per-device config injection, rpiboot).
- **Hardware & kiosk internals**: [docs/kiosk-setup.md](docs/kiosk-setup.md)
  — display/touch/backlight/buttons details and every fix applied
  (touch I2C transactions, rotation calibration, VT handling).
- `kiosk/` holds everything deployed to devices; `tools/` holds
  debug/test utilities (never deployed).

## Scope

This repo is the **generic, public** half of the setup: no secrets, no
site-specific config. Per-site things (dashboard content, URLs, tokens,
fleet convergence) live in the owner's private ansible repo, which also
regenerates `kiosk-browser` on managed devices after first flash.

`kiosk/mqtt-sensors` is **deprecated** in favour of
[mithro/sensors2mqtt](https://github.com/mithro/sensors2mqtt); it remains
in images for a basic out-of-the-box experience but managed devices run
sensors2mqtt instead.

## License

Apache 2.0 — see [LICENSE](LICENSE).
