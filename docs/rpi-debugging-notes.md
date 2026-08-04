# RPi OS / Cloud-Init / Config.txt Debugging Notes

Hard-won gotchas from the reTerminal bring-up (2026-02), generic to
CM4/RPi OS Trixie/cloud-init work. The fixes below are already baked into
build-image.py + cloud-init/ — these notes explain WHY they are there.

## config.txt section ordering (CRITICAL)
- Sections: `[cm4]`, `[cm5]`, `[all]` — settings between headers apply to the section ABOVE them
- Settings placed between `[cm5]` and `[all]` are in the `[cm5]` section, NOT global
- Always put global settings AFTER `[all]` header
- `enable_uart=1` and `dtoverlay=disable-bt` MUST be in `[all]` section to apply to CM4

## RPi OS SSH requirement
- RPi OS Trixie does NOT start sshd by default, regardless of cloud-init config
- Must create empty `ssh` file on boot partition (`sudo touch /boot/firmware/ssh`)
- Cloud-init `ssh: install_server: true` alone is NOT sufficient

## Cloud-init bringup=False
- Cloud-init generates netplan config but sets `bringup=False` when user-data lacks "network requiring elements"
- `packages` and `runcmd` are NOT considered network-requiring
- Fix: use `bootcmd: [rfkill unblock wifi, netplan apply]` — runs every boot
- On subsequent boots, cloud-init denies network events ("Event Denied: scopes=['network'] EventType=boot")

## WiFi rfkill on RPi OS
- WiFi radio is soft-blocked by rfkill on RPi OS by default
- Must `rfkill unblock wifi` before WiFi will work
- Put this in cloud-init `bootcmd` (runs every boot)

## GPIO UART on CM4
- PL011 (ttyAMA0) is the full UART — Bluetooth claims it by default
- `dtoverlay=disable-bt` frees PL011 for GPIO header use (pins 8 TXD, 10 RXD, 6 GND)
- `enable_uart=1` changes firmware-injected `8250.nr_uarts=0` to `8250.nr_uarts=1`
- `dtoverlay=miniuart-bt` doesn't reliably free PL011 on CM4

## USB gadget serial on reTerminal
- g_serial loads and binds on device side but host never sees USB gadget
- Both dwc_otg (legacy) and dwc2 load simultaneously — conflict
- reTerminal USB-C port likely only routes data in bootloader mode (rpiboot)
- USB gadget serial appears to be a hardware limitation of the reTerminal carrier board

## network-config.yaml
- Do NOT use `optional: true` — causes cloud-init to skip network activation entirely

## In-place rootfs fixes
- Unreliable when device has partial package installs from previous boot attempts
- Always prefer full re-flash with clean image for reliable results
