#!/usr/bin/env python3
"""Customize and flash a generic reTerminal image to the device's eMMC.

Usage:
    uv run flash.py <hostname>

Where <hostname> is 'reterm1' or 'reterm2'.

Takes the generic image built by build-image.py (or downloaded from CI),
injects per-device configuration (hostname, WiFi, SSH keys, dashboard URL),
then guides you through the physical setup and flashes to eMMC.
"""

import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import jinja2
import requests

VALID_HOSTNAMES = ("reterm1", "reterm2")
WIFI_SSID = "ansells-iot"

KIOSK_URLS = {
    "reterm1": "https://ha.monarto.mithis.com/local/reterminal.html",
    "reterm2": "https://ha.monarto.mithis.com/local/reterminal.html",
}

SCRIPT_DIR = Path(__file__).resolve().parent
CLOUD_INIT_DIR = SCRIPT_DIR / "cloud-init"
OUTPUT_DIR = SCRIPT_DIR / "output"
TMP_DIR = SCRIPT_DIR / "tmp"

# Generic image name (produced by build-image.py or downloaded from CI)
GENERIC_IMAGE_XZ = "reterminal-base.img.xz"


# ---------------------------------------------------------------------------
# Secrets and SSH keys (local only, never on CI)
# ---------------------------------------------------------------------------

def get_wifi_psk() -> str:
    """Retrieve WiFi PSK from NetworkManager or environment variable."""
    try:
        result = subprocess.run(
            [
                "nmcli", "--show-secrets", "-g",
                "802-11-wireless-security.psk",
                "connection", "show", WIFI_SSID,
            ],
            capture_output=True, text=True, check=True,
        )
        psk = result.stdout.strip()
        if psk:
            print(f"Retrieved WiFi PSK from NetworkManager for '{WIFI_SSID}'")
            return psk
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    psk = os.environ.get("WIFI_PSK", "").strip()
    if psk:
        print("Using WiFi PSK from WIFI_PSK environment variable")
        return psk

    print(
        f"ERROR: Could not retrieve WiFi PSK.\n"
        f"Either:\n"
        f"  1. Connect this machine to '{WIFI_SSID}' first, or\n"
        f"  2. Set the WIFI_PSK environment variable",
        file=sys.stderr,
    )
    sys.exit(1)


def get_ssh_keys() -> list[str]:
    """Fetch SSH public keys from GitHub for user 'mithro'."""
    url = "https://github.com/mithro.keys"
    print(f"Fetching SSH keys from {url}")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    keys = [line.strip() for line in resp.text.strip().splitlines() if line.strip()]
    if not keys:
        print(f"ERROR: No SSH keys found at {url}", file=sys.stderr)
        sys.exit(1)
    print(f"  Found {len(keys)} SSH key(s)")
    return keys


# ---------------------------------------------------------------------------
# Cloud-init rendering
# ---------------------------------------------------------------------------

def render_cloud_init(hostname: str, wifi_psk: str, ssh_keys: list[str]) -> dict[str, str]:
    """Render cloud-init templates with per-device parameters.

    Returns a dict mapping filename -> rendered content.
    """
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(CLOUD_INIT_DIR)),
        keep_trailing_newline=True,
    )

    template_vars = {
        "hostname": hostname,
        "wifi_psk": wifi_psk,
        "ssh_keys": ssh_keys,
    }

    rendered = {}
    for template_name in ("user-data.yaml", "network-config.yaml", "meta-data.yaml"):
        template = env.get_template(template_name)
        output_name = template_name.removesuffix(".yaml")
        rendered[output_name] = template.render(**template_vars)

    return rendered


# ---------------------------------------------------------------------------
# Partition handling
# ---------------------------------------------------------------------------

def find_partition_offsets(img_path: Path) -> tuple[int, int]:
    """Find byte offsets of boot (FAT32) and rootfs (Linux) partitions."""
    result = subprocess.run(
        ["fdisk", "-l", str(img_path)],
        capture_output=True, text=True, check=True,
    )

    sector_size = 512
    for line in result.stdout.splitlines():
        m = re.search(r"Sector size.*?:\s*(\d+)\s*bytes", line)
        if m:
            sector_size = int(m.group(1))
            break

    boot_offset = None
    rootfs_offset = None

    for line in result.stdout.splitlines():
        if not line.startswith(str(img_path)):
            continue
        parts = line.split()
        start_sector = int(parts[1])
        offset = start_sector * sector_size

        if "FAT32" in line or " c " in line:
            boot_offset = offset
        elif "Linux" in line:
            rootfs_offset = offset

    if boot_offset is None or rootfs_offset is None:
        print("ERROR: Could not find partitions in image", file=sys.stderr)
        print(f"fdisk output:\n{result.stdout}", file=sys.stderr)
        sys.exit(1)

    return boot_offset, rootfs_offset


# ---------------------------------------------------------------------------
# Image customization
# ---------------------------------------------------------------------------

def find_generic_image() -> Path:
    """Find the generic image in output/ directory."""
    xz_path = OUTPUT_DIR / GENERIC_IMAGE_XZ
    if xz_path.exists():
        return xz_path

    print(f"ERROR: Generic image not found: {xz_path}", file=sys.stderr)
    print("Build it with: uv run build-image.py", file=sys.stderr)
    print("Or download from CI and place in output/", file=sys.stderr)
    sys.exit(1)


def extract_for_device(xz_path: Path, hostname: str) -> Path:
    """Extract generic image to output/<hostname>.img."""
    img_path = OUTPUT_DIR / f"{hostname}.img"

    if img_path.exists():
        print(f"Removing existing {img_path}")
        img_path.unlink()

    print(f"Extracting {xz_path.name} -> {img_path.name}")
    with lzma.open(xz_path) as xz_f:
        with open(img_path, "wb") as img_f:
            shutil.copyfileobj(xz_f, img_f)

    size_mb = img_path.stat().st_size // (1024 * 1024)
    print(f"  Extracted: {img_path} ({size_mb} MB)")
    return img_path


def customize_image(
    img_path: Path,
    hostname: str,
    rendered_cloud_init: dict[str, str],
    kiosk_url: str,
) -> None:
    """Inject per-device configuration into the image.

    Mounts boot partition to write cloud-init files, and rootfs to
    replace the @@KIOSK_URL@@ placeholder in kiosk-browser.
    """
    print("Customizing image for device...")
    boot_offset, rootfs_offset = find_partition_offsets(img_path)

    boot_mount = TMP_DIR / "boot-mount"
    rootfs_mount = TMP_DIR / "rootfs-mount"
    boot_mount.mkdir(parents=True, exist_ok=True)
    rootfs_mount.mkdir(parents=True, exist_ok=True)

    try:
        # Mount boot partition
        print(f"Mounting boot partition at {boot_mount}")
        subprocess.run(
            [
                "sudo", "mount",
                "-o", f"loop,offset={boot_offset}",
                str(img_path), str(boot_mount),
            ],
            check=True,
        )

        # Write cloud-init files
        for filename, content in rendered_cloud_init.items():
            target = boot_mount / filename
            print(f"  Writing {filename}")
            subprocess.run(
                ["sudo", "tee", str(target)],
                input=content.encode(),
                stdout=subprocess.DEVNULL,
                check=True,
            )

        print("  Cloud-init files written")

        # Unmount boot before mounting rootfs (both are loop mounts on same image)
        subprocess.run(["sudo", "umount", str(boot_mount)], check=True)

        # Mount rootfs partition
        print(f"Mounting rootfs at {rootfs_mount}")
        subprocess.run(
            [
                "sudo", "mount",
                "-o", f"loop,offset={rootfs_offset}",
                str(img_path), str(rootfs_mount),
            ],
            check=True,
        )

        # Replace @@KIOSK_URL@@ in kiosk-browser
        kiosk_browser = rootfs_mount / "usr/local/bin/kiosk-browser"
        if kiosk_browser.exists():
            content = subprocess.run(
                ["sudo", "cat", str(kiosk_browser)],
                capture_output=True, text=True, check=True,
            ).stdout
            if "@@KIOSK_URL@@" in content:
                new_content = content.replace("@@KIOSK_URL@@", kiosk_url)
                subprocess.run(
                    ["sudo", "tee", str(kiosk_browser)],
                    input=new_content.encode(),
                    stdout=subprocess.DEVNULL,
                    check=True,
                )
                print(f"  Set kiosk URL: {kiosk_url}")
            else:
                print("ERROR: @@KIOSK_URL@@ placeholder not found in kiosk-browser",
                      file=sys.stderr)
                print("The kiosk browser will not load the correct dashboard.",
                      file=sys.stderr)
                sys.exit(1)
        else:
            print("ERROR: /usr/local/bin/kiosk-browser not found in image",
                  file=sys.stderr)
            print("Was the generic image built with build-image.py?",
                  file=sys.stderr)
            sys.exit(1)

    finally:
        # Unmount everything
        subprocess.run(["sudo", "umount", str(boot_mount)], check=False)
        subprocess.run(["sudo", "umount", str(rootfs_mount)], check=False)

        for d in [boot_mount, rootfs_mount]:
            if d.exists():
                try:
                    d.rmdir()
                except OSError:
                    pass
        if TMP_DIR.exists():
            try:
                TMP_DIR.rmdir()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Flashing
# ---------------------------------------------------------------------------

def get_block_devices() -> set[str]:
    """Get the current set of block device paths using lsblk."""
    result = subprocess.run(
        ["lsblk", "--json", "--output", "PATH,TYPE,SIZE,MODEL"],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    return {
        dev["path"]
        for dev in data.get("blockdevices", [])
        if dev.get("type") == "disk"
    }


def wait_for_new_device(known_devices: set[str], timeout: int = 60) -> str:
    """Poll for a new block device to appear, return its path."""
    print(f"Waiting for eMMC block device to appear (timeout: {timeout}s)...")
    start = time.monotonic()

    while time.monotonic() - start < timeout:
        current = get_block_devices()
        new_devices = current - known_devices
        if new_devices:
            device = sorted(new_devices)[0]
            return device
        time.sleep(1)
        elapsed = int(time.monotonic() - start)
        print(f"\r  Waiting... ({elapsed}s)", end="", flush=True)

    print()
    print("ERROR: Timed out waiting for eMMC device to appear.", file=sys.stderr)
    print("Check that:", file=sys.stderr)
    print("  - The boot mode switch is in the ON position", file=sys.stderr)
    print("  - USB-C is connected to your computer", file=sys.stderr)
    print("  - The reTerminal is powered on", file=sys.stderr)
    sys.exit(1)


def get_device_info(device: str) -> str:
    """Get human-readable info about a block device."""
    result = subprocess.run(
        ["lsblk", "--output", "PATH,SIZE,MODEL", "--nodeps", device],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Customize and flash a reTerminal image to eMMC."
    )
    parser.add_argument(
        "hostname",
        choices=VALID_HOSTNAMES,
        help="Target hostname (reterm1 or reterm2)",
    )
    args = parser.parse_args()

    hostname = args.hostname
    kiosk_url = KIOSK_URLS[hostname]

    print(f"Preparing image for: {hostname}")
    print(f"Dashboard URL: {kiosk_url}")
    print("=" * 50)

    # Step 1: Find or download generic image
    xz_path = find_generic_image()

    # Step 2: Extract to device-specific image
    img_path = extract_for_device(xz_path, hostname)

    # Step 3: Get secrets
    wifi_psk = get_wifi_psk()
    ssh_keys = get_ssh_keys()

    # Step 4: Render cloud-init
    print("Rendering cloud-init templates...")
    rendered = render_cloud_init(hostname, wifi_psk, ssh_keys)

    # Step 5: Inject device configuration
    customize_image(img_path, hostname, rendered, kiosk_url)

    print()
    print(f"Image customized: {img_path}")
    size_mb = img_path.stat().st_size // (1024 * 1024)
    print(f"Size: {size_mb} MB")
    print()

    # Step 6: Flash to eMMC
    print("=" * 60)
    print("PHYSICAL SETUP")
    print("=" * 60)
    print()
    print("1. Open the reTerminal back shell:")
    print("   - Remove 4 rubber covers to expose screws")
    print("   - Remove 4 screws")
    print("   - Carefully lift off the back cover")
    print()
    print("2. Remove the heatsink (2 screws)")
    print()
    print("3. Flip the boot mode switch to the ON position")
    print("   (small switch near the CM4 module)")
    print()
    print("4. Connect USB-C cable from reTerminal to this computer")
    print()
    print("5. Power on the reTerminal")
    print()
    if sys.stdin.isatty():
        input("Press Enter when ready to continue...")
    else:
        print("(non-interactive mode, continuing automatically)")
    print()

    # Check rpiboot
    if not shutil.which("rpiboot"):
        print("ERROR: 'rpiboot' not found.", file=sys.stderr)
        print("Install it with: sudo apt install rpiboot", file=sys.stderr)
        sys.exit(1)

    # Snapshot current devices
    known_devices = get_block_devices()

    # Run rpiboot
    print("Running rpiboot to expose eMMC as USB mass storage...")
    try:
        subprocess.run(["sudo", "rpiboot"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: rpiboot failed with exit code {e.returncode}", file=sys.stderr)
        sys.exit(1)

    # Wait for new block device
    print()
    device = wait_for_new_device(known_devices)
    print()

    # Confirm
    print(f"Detected new device:")
    print(get_device_info(device))
    print()
    if sys.stdin.isatty():
        confirm = input(f"Flash {img_path.name} to {device}? This will ERASE ALL DATA. [y/N] ")
        if confirm.lower() != "y":
            print("Aborted.")
            sys.exit(0)
    else:
        print("WARNING: Non-interactive mode. Refusing to flash without confirmation.",
              file=sys.stderr)
        sys.exit(1)

    # Flash
    print()
    print(f"Flashing {img_path.name} -> {device}")
    print("(this will take a few minutes)")
    subprocess.run(
        [
            "sudo", "dd",
            f"if={img_path}",
            f"of={device}",
            "bs=4M",
            "status=progress",
            "conv=fsync",
        ],
        check=True,
    )

    print()
    print("=" * 60)
    print("FLASH COMPLETE")
    print("=" * 60)
    print()
    print("Post-flash steps:")
    print()
    print("1. Flip the boot mode switch back to OFF")
    print()
    print("2. Disconnect USB-C cable")
    print()
    print("3. Reassemble:")
    print("   - Reattach heatsink (2 screws)")
    print("   - Replace back cover (4 screws)")
    print("   - Replace 4 rubber covers")
    print()
    print("4. Power on the reTerminal")
    print()
    print("The device will:")
    print(f"  - Set hostname to '{hostname}'")
    print("  - Connect to 'ansells-iot' WiFi")
    print("  - Configure SSH with GitHub keys")
    print(f"  - Display dashboard at: {kiosk_url}")
    print()
    print("~60 seconds from power-on to working kiosk (no internet needed)")
    print()
    print(f"SSH: ssh tim@{hostname}")


if __name__ == "__main__":
    main()
