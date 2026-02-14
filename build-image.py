#!/usr/bin/env python3
"""Build a ready-to-flash reTerminal image with cloud-init configuration.

Usage:
    uv run build-image.py <hostname>

Where <hostname> is 'reterm1' or 'reterm2'.

Downloads Raspberry Pi OS Lite Trixie (arm64), injects cloud-init
configuration with WiFi credentials and SSH keys, and produces a
ready-to-flash .img file.
"""

import argparse
import hashlib
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import jinja2
import requests

# Raspberry Pi OS Lite Trixie 64-bit
IMAGE_URL = "https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2025-12-04/2025-12-04-raspios-trixie-arm64-lite.img.xz"
IMAGE_SHA256_URL = IMAGE_URL + ".sha256"

VALID_HOSTNAMES = ("reterm1", "reterm2")
WIFI_SSID = "ansells-iot"

SCRIPT_DIR = Path(__file__).resolve().parent
CLOUD_INIT_DIR = SCRIPT_DIR / "cloud-init"
OUTPUT_DIR = SCRIPT_DIR / "output"


def get_wifi_psk() -> str:
    """Retrieve WiFi PSK from NetworkManager or environment variable.

    Tries nmcli first (requires the build host to have connected to
    the network previously), falls back to WIFI_PSK env var.
    """
    # Try NetworkManager first
    try:
        result = subprocess.run(
            [
                "nmcli",
                "--show-secrets",
                "-g",
                "802-11-wireless-security.psk",
                "connection",
                "show",
                WIFI_SSID,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        psk = result.stdout.strip()
        if psk:
            print(f"Retrieved WiFi PSK from NetworkManager for '{WIFI_SSID}'")
            return psk
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Fall back to environment variable
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
        print("ERROR: No SSH keys found at {url}", file=sys.stderr)
        sys.exit(1)
    print(f"  Found {len(keys)} SSH key(s)")
    return keys


def download_image() -> Path:
    """Download the RPi OS image if not already cached."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    xz_path = OUTPUT_DIR / Path(IMAGE_URL).name

    if xz_path.exists():
        print(f"Using cached image: {xz_path}")
        return xz_path

    print(f"Downloading {IMAGE_URL}")
    print("  (this may take a while, ~450 MB)")
    resp = requests.get(IMAGE_URL, stream=True, timeout=60)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0

    with open(xz_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // total
                mb = downloaded // (1024 * 1024)
                total_mb = total // (1024 * 1024)
                print(f"\r  {mb}/{total_mb} MB ({pct}%)", end="", flush=True)

    print()  # newline after progress
    print(f"Downloaded to {xz_path}")
    return xz_path


def verify_checksum(xz_path: Path) -> None:
    """Verify SHA256 checksum of the downloaded image."""
    print(f"Downloading checksum from {IMAGE_SHA256_URL}")
    resp = requests.get(IMAGE_SHA256_URL, timeout=30)
    resp.raise_for_status()

    # Format is: "<hash>  <filename>" or just "<hash>"
    expected_hash = resp.text.strip().split()[0].lower()
    print(f"  Expected: {expected_hash}")

    print(f"Computing SHA256 of {xz_path.name}...")
    sha256 = hashlib.sha256()
    with open(xz_path, "rb") as f:
        while True:
            data = f.read(65536)
            if not data:
                break
            sha256.update(data)

    actual_hash = sha256.hexdigest()
    print(f"  Actual:   {actual_hash}")

    if actual_hash != expected_hash:
        print("ERROR: Checksum mismatch! The download may be corrupt.", file=sys.stderr)
        print(f"  Removing {xz_path}", file=sys.stderr)
        xz_path.unlink()
        sys.exit(1)

    print("  Checksum OK")


def extract_image(xz_path: Path, hostname: str) -> Path:
    """Extract .img.xz to output/{hostname}.img."""
    img_path = OUTPUT_DIR / f"{hostname}.img"

    if img_path.exists():
        print(f"Removing existing {img_path}")
        img_path.unlink()

    print(f"Extracting {xz_path.name} -> {img_path.name}")
    print("  (this may take a moment)")

    with lzma.open(xz_path) as xz_f:
        with open(img_path, "wb") as img_f:
            shutil.copyfileobj(xz_f, img_f)

    size_mb = img_path.stat().st_size // (1024 * 1024)
    print(f"  Extracted: {img_path} ({size_mb} MB)")
    return img_path


def find_boot_partition_offset(img_path: Path) -> int:
    """Find the byte offset of the FAT32 boot partition using fdisk."""
    result = subprocess.run(
        ["fdisk", "-l", str(img_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    # Parse fdisk output for sector size and partition table
    sector_size = 512  # default
    for line in result.stdout.splitlines():
        # "Sector size (logical / physical): 512 bytes / 512 bytes"
        m = re.search(r"Sector size.*?:\s*(\d+)\s*bytes", line)
        if m:
            sector_size = int(m.group(1))
            break

    # Find the first FAT32 partition (type W95 FAT32 or type c)
    # fdisk output lines look like:
    # /path/to/image.img1  8192  1056767  1048576  512M  c W95 FAT32 (LBA)
    for line in result.stdout.splitlines():
        if "FAT32" in line or " c " in line:
            parts = line.split()
            # The start sector is the second column (after device name)
            start_sector = int(parts[1])
            offset = start_sector * sector_size
            print(f"  Boot partition: start sector {start_sector}, offset {offset} bytes")
            return offset

    print("ERROR: Could not find FAT32 boot partition in image", file=sys.stderr)
    print(f"fdisk output:\n{result.stdout}", file=sys.stderr)
    sys.exit(1)


def render_cloud_init(hostname: str, wifi_psk: str, ssh_keys: list[str]) -> dict[str, str]:
    """Render cloud-init templates with the given parameters.

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
        # Output filenames drop the .yaml extension for cloud-init
        output_name = template_name.removesuffix(".yaml")
        rendered[output_name] = template.render(**template_vars)

    return rendered


def configure_usb_serial(mount_point: Path) -> None:
    """Enable USB serial console (gadget mode) on the USB-C port.

    Modifies config.txt and cmdline.txt on the boot partition to enable
    the dwc2 USB controller in gadget mode with serial console output.
    This allows connecting to the reTerminal's serial console via USB-C.
    """
    print("  Configuring USB serial console on USB-C port...")

    # Add dtoverlay=dwc2 to config.txt (enables USB gadget mode)
    config_txt = mount_point / "config.txt"
    result = subprocess.run(
        ["sudo", "cat", str(config_txt)],
        capture_output=True, text=True, check=True,
    )
    config_content = result.stdout
    if "dtoverlay=dwc2" not in config_content:
        config_content = config_content.rstrip("\n") + "\ndtoverlay=dwc2\n"
        subprocess.run(
            ["sudo", "tee", str(config_txt)],
            input=config_content.encode(),
            stdout=subprocess.DEVNULL,
            check=True,
        )
        print("    config.txt: added dtoverlay=dwc2")
    else:
        print("    config.txt: dtoverlay=dwc2 already present")

    # Add modules-load and console to cmdline.txt
    cmdline_txt = mount_point / "cmdline.txt"
    result = subprocess.run(
        ["sudo", "cat", str(cmdline_txt)],
        capture_output=True, text=True, check=True,
    )
    cmdline = result.stdout.strip()
    modified = False

    if "modules-load=" not in cmdline:
        cmdline += " modules-load=dwc2,g_serial"
        modified = True
        print("    cmdline.txt: added modules-load=dwc2,g_serial")

    if "console=ttyGS0" not in cmdline:
        cmdline += " console=ttyGS0,115200"
        modified = True
        print("    cmdline.txt: added console=ttyGS0,115200")

    if modified:
        subprocess.run(
            ["sudo", "tee", str(cmdline_txt)],
            input=(cmdline + "\n").encode(),
            stdout=subprocess.DEVNULL,
            check=True,
        )
    else:
        print("    cmdline.txt: USB serial already configured")


def inject_cloud_init(img_path: Path, rendered_files: dict[str, str]) -> None:
    """Mount the boot partition and write cloud-init files."""
    offset = find_boot_partition_offset(img_path)

    mount_point = SCRIPT_DIR / "tmp" / "boot-mount"
    mount_point.mkdir(parents=True, exist_ok=True)

    try:
        print(f"Mounting boot partition at {mount_point}")
        subprocess.run(
            [
                "sudo", "mount",
                "-o", f"loop,offset={offset}",
                str(img_path),
                str(mount_point),
            ],
            check=True,
        )

        for filename, content in rendered_files.items():
            target = mount_point / filename
            print(f"  Writing {filename}")
            # Use sudo tee since mount point is owned by root
            subprocess.run(
                ["sudo", "tee", str(target)],
                input=content.encode(),
                stdout=subprocess.DEVNULL,
                check=True,
            )

        print("  Cloud-init files written successfully")

        # Enable USB serial console (dwc2 gadget mode on USB-C port)
        configure_usb_serial(mount_point)

    finally:
        print(f"Unmounting {mount_point}")
        subprocess.run(["sudo", "umount", str(mount_point)], check=False)
        # Clean up mount point directory
        if mount_point.exists():
            mount_point.rmdir()
        tmp_dir = SCRIPT_DIR / "tmp"
        if tmp_dir.exists():
            try:
                tmp_dir.rmdir()
            except OSError:
                pass  # not empty, leave it


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a ready-to-flash reTerminal image with cloud-init configuration."
    )
    parser.add_argument(
        "hostname",
        choices=VALID_HOSTNAMES,
        help="Target hostname (reterm1 or reterm2)",
    )
    args = parser.parse_args()

    print(f"Building image for: {args.hostname}")
    print("=" * 50)

    # Step 1: Get secrets and SSH keys
    wifi_psk = get_wifi_psk()
    ssh_keys = get_ssh_keys()

    # Step 2: Download and verify image
    xz_path = download_image()
    verify_checksum(xz_path)

    # Step 3: Extract image
    img_path = extract_image(xz_path, args.hostname)

    # Step 4: Render cloud-init templates
    print("Rendering cloud-init templates...")
    rendered = render_cloud_init(args.hostname, wifi_psk, ssh_keys)

    # Step 5: Inject cloud-init into image
    inject_cloud_init(img_path, rendered)

    print()
    print("=" * 50)
    print(f"Image ready: {img_path}")
    print(f"Flash with: uv run flash.py {args.hostname}")


if __name__ == "__main__":
    main()
