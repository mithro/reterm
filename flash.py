#!/usr/bin/env python3
"""Flash a built reTerminal image to the device's eMMC.

Usage:
    uv run flash.py <hostname>

Where <hostname> is 'reterm1' or 'reterm2'.

Guides you through the physical setup, runs rpiboot to expose the
eMMC as a USB mass storage device, and flashes the image with dd.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

VALID_HOSTNAMES = ("reterm1", "reterm2")
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"


def get_block_devices() -> set[str]:
    """Get the current set of block device paths using lsblk."""
    result = subprocess.run(
        ["lsblk", "--json", "--output", "PATH,TYPE,SIZE,MODEL"],
        capture_output=True,
        text=True,
        check=True,
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
            # If multiple appeared, take the first alphabetically
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
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Flash a built reTerminal image to the device's eMMC."
    )
    parser.add_argument(
        "hostname",
        choices=VALID_HOSTNAMES,
        help="Target hostname (reterm1 or reterm2)",
    )
    args = parser.parse_args()

    img_path = OUTPUT_DIR / f"{args.hostname}.img"
    if not img_path.exists():
        print(f"ERROR: Image not found: {img_path}", file=sys.stderr)
        print(f"Run first: uv run build-image.py {args.hostname}", file=sys.stderr)
        sys.exit(1)

    size_mb = img_path.stat().st_size // (1024 * 1024)
    print(f"Image: {img_path} ({size_mb} MB)")
    print()

    # Physical setup instructions
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

    # Check rpiboot is installed before proceeding
    if not shutil.which("rpiboot"):
        print("ERROR: 'rpiboot' not found.", file=sys.stderr)
        print("Install it with: sudo apt install rpiboot", file=sys.stderr)
        sys.exit(1)

    # Snapshot current devices before rpiboot
    known_devices = get_block_devices()

    # Run rpiboot
    print("Running rpiboot to expose eMMC as USB mass storage...")
    try:
        subprocess.run(
            ["sudo", "rpiboot"],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"ERROR: rpiboot failed with exit code {e.returncode}", file=sys.stderr)
        sys.exit(1)

    # Wait for the new block device
    print()
    device = wait_for_new_device(known_devices)
    print()

    # Show device info and confirm
    print(f"Detected new device:")
    print(get_device_info(device))
    print()
    if sys.stdin.isatty():
        confirm = input(f"Flash {img_path.name} to {device}? This will ERASE ALL DATA. [y/N] ")
        if confirm.lower() != "y":
            print("Aborted.")
            sys.exit(0)
    else:
        print(f"WARNING: Non-interactive mode. Refusing to flash without confirmation.", file=sys.stderr)
        print(f"Run interactively in a terminal to flash.", file=sys.stderr)
        sys.exit(1)

    # Flash the image
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
    print(f"  - Set hostname to '{args.hostname}'")
    print("  - Connect to 'ansells-iot' WiFi")
    print("  - Install Seeed reTerminal display drivers")
    print("  - Reboot once after driver installation")
    print()
    print("After ~5-10 minutes, SSH should be available:")
    print(f"  ssh tim@{args.hostname}")


if __name__ == "__main__":
    main()
