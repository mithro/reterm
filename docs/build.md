# Building and Flashing reTerminal Images

## Architecture

The build process has two stages:

1. **Generic image build** (`build-image.py`) — runs on CI or locally. Downloads
   RPi OS, expands the image, installs all packages and drivers in a chroot,
   deploys kiosk files, and compresses to `reterminal-base.img.xz`. No secrets
   or per-device config are included.

2. **Device customization** (`flash.py <hostname>`) — runs locally. Extracts the
   generic image, injects cloud-init files (hostname, WiFi PSK, SSH keys) and
   the dashboard URL, then flashes to eMMC via rpiboot.

### What happens at each stage

| Concern | Build time (CI) | Flash time (local) |
|---------|----------------|-------------------|
| RPi OS download + extract | Yes | No |
| Image expansion (2 GB) | Yes | No |
| Package install (cage, chromium, etc.) | Yes (chroot) | No |
| Seeed driver build | Yes (chroot) | No |
| Kiosk file deployment | Yes | No |
| Service enablement | Yes | No |
| Transparent cursor theme | Yes | No |
| PAM config | Yes | No |
| Serial console config | Yes | No |
| **Hostname** | No | Yes (cloud-init) |
| **WiFi PSK** | No | Yes (cloud-init) |
| **SSH keys** | No | Yes (cloud-init) |
| **Dashboard URL** | No | Yes (kiosk-browser) |

## Build prerequisites

### CI (GitHub Actions)

Handled automatically by the workflow (`.github/workflows/build-image.yml`):
- `qemu-user-static` via `docker/setup-qemu-action`
- `parted`, `e2fsprogs`
- `uv` via `astral-sh/setup-uv`

### Local build

```bash
sudo apt-get install qemu-user-static binfmt-support parted e2fsprogs
```

Verify binfmt_misc is working:
```bash
ls /proc/sys/fs/binfmt_misc/qemu-aarch64
```

## Building the generic image

### On CI

Push to `main` or trigger the workflow manually. The image is uploaded as a
build artifact (`reterminal-image`).

### Locally

```bash
uv run build-image.py
```

This produces `output/reterminal-base.img.xz` and `output/reterminal-base.img.xz.sha256`.

The build process:
1. Downloads RPi OS Lite Trixie (~450 MB, cached after first download)
2. Verifies SHA256 checksum
3. Extracts to `output/reterminal-base.img`
4. Expands image by 2 GB for packages
5. Mounts boot and rootfs partitions
6. Configures GPIO UART serial console
7. Deploys kiosk files (no chroot needed)
8. Creates transparent cursor theme and PAM config
9. Enables systemd services
10. Sets up chroot with qemu-aarch64-static and uname wrapper
11. Installs packages: cage, chromium, wlr-randr, seatd, build-essential, etc.
12. Clones Seeed driver repo, applies touch rotation patch, builds module
13. Tears down chroot
14. Compresses with xz

### Chroot uname challenge

In a qemu-user-static chroot, `uname -r` returns the host kernel version,
not the target's. This breaks `raspberrypi-kernel-headers` installation
and DKMS module builds. The build script creates a wrapper at
`/usr/bin/uname` that intercepts `-r` and returns the version from
`/lib/modules/` in the rootfs. The real binary is backed up as
`/usr/bin/uname.real` and restored after the chroot steps.

## Flashing a device

### Prerequisites

- `rpiboot` — `sudo apt install rpiboot`
- WiFi PSK — either connect this machine to `ansells-iot` first, or set `WIFI_PSK` env var
- Internet access — for fetching SSH keys from github.com/mithro.keys
- Generic image in `output/reterminal-base.img.xz`

### Flash process

```bash
uv run flash.py reterm1   # or reterm2
```

The script will:
1. Find `output/reterminal-base.img.xz`
2. Extract to `output/reterm1.img`
3. Retrieve WiFi PSK and SSH keys
4. Render cloud-init templates
5. Mount boot partition, write cloud-init files
6. Mount rootfs, replace `@@KIOSK_URL@@` with the device's dashboard URL
7. Guide you through physical setup (opening case, boot mode switch)
8. Run `rpiboot` to expose eMMC
9. Flash with `dd`

### Per-device configuration

Dashboard URLs are defined in `flash.py`:

```python
KIOSK_URLS = {
    "reterm1": "http://ha.monarto.mithis.com:8123/local/reterminal.html",
    "reterm2": "http://ha.monarto.mithis.com:8123/local/reterminal.html",
}
```

### Adding a new device

1. Add the hostname to `VALID_HOSTNAMES` in `flash.py`
2. Add the dashboard URL to `KIOSK_URLS` in `flash.py`
3. Run `uv run flash.py <new-hostname>`

## Boot timeline

After flashing, the device boots as follows:

1. **~15s** — Kernel boots, loads `mipi_dsi` driver (display appears)
2. **~30s** — Cloud-init runs: sets hostname, connects to WiFi
3. **~45s** — `cage-kiosk@tty7` starts: cage + chromium launch
4. **~60s** — Dashboard visible, touch working

`backlight-manager` and `power-button-handler` start with `multi-user.target`.

No internet is required for boot — all software is pre-installed.

## Troubleshooting

### Chroot failures

If the build fails during chroot steps:
- Check `qemu-aarch64-static` is registered: `ls /proc/sys/fs/binfmt_misc/qemu-aarch64`
- Check mounts are clean: `mount | grep rootfs-mount`
- Clean up stale mounts: `sudo umount -R tmp/rootfs-mount`

### Partition expansion errors

If `resize2fs` fails:
- Run `sudo e2fsck -f <loop-device>` manually
- Check loop devices: `losetup -l`
- Clean up: `sudo losetup -D`

### Display not working after flash

- Verify `mipi_dsi.ko.xz` exists in the image's `/lib/modules/*/updates/dkms/`
- Check `dtoverlay` files in `/boot/firmware/overlays/`
- Try rebuilding the module on device (see kiosk-setup.md)

### WiFi not connecting

- Verify cloud-init files on boot partition: `user-data`, `network-config`, `meta-data`
- Check WiFi PSK is correct: `sudo cat /etc/netplan/50-cloud-init.yaml`
- Try manually: `sudo rfkill unblock wifi && sudo netplan apply`
