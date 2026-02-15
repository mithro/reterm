#!/usr/bin/env python3
"""Temporarily force DWC2 into host mode to check for USB hub chip.

This writes ForceHstMode to GUSBCFG, rebinds the driver, and checks
if a USB hub is detected. This is to verify the theory that the
reTerminal has a USB hub between CM4 OTG pins and the USB-C connector.
"""
import mmap
import os
import struct
import time
import subprocess
import sys

DWC2_BASE = 0xfe980000
DWC2_SIZE = 0x1000
GUSBCFG = 0x00C
GUSBCFG_FORCEDEVMODE = (1 << 30)
GUSBCFG_FORCEHSTMODE = (1 << 29)

def read_reg(mm, offset):
    mm.seek(offset)
    return struct.unpack('<I', mm.read(4))[0]

def write_reg(mm, offset, value):
    mm.seek(offset)
    mm.write(struct.pack('<I', value))

def run(cmd):
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.stdout.strip():
        print(f"    {result.stdout.strip()}")
    if result.returncode != 0 and result.stderr.strip():
        print(f"    (rc={result.returncode}) {result.stderr.strip()}")
    return result

def main():
    if os.geteuid() != 0:
        print("ERROR: Must be run as root")
        sys.exit(1)

    fd = os.open('/dev/mem', os.O_RDWR | os.O_SYNC)
    mm = mmap.mmap(fd, DWC2_SIZE, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                   offset=DWC2_BASE)

    print("=== Forcing DWC2 into host mode ===")

    # Force host mode
    gusbcfg = read_reg(mm, GUSBCFG)
    print(f"  GUSBCFG before: 0x{gusbcfg:08x}")
    gusbcfg |= GUSBCFG_FORCEHSTMODE
    gusbcfg &= ~GUSBCFG_FORCEDEVMODE
    write_reg(mm, GUSBCFG, gusbcfg)
    gusbcfg = read_reg(mm, GUSBCFG)
    print(f"  GUSBCFG after:  0x{gusbcfg:08x}")

    time.sleep(0.1)

    # Rebind DWC2
    print("\n--- Rebinding DWC2 ---")
    run("echo fe980000.usb | tee /sys/bus/platform/drivers/dwc2/bind")
    time.sleep(3)

    # Check for USB devices
    print("\n--- Checking for USB devices ---")
    run("lsusb")
    run("lsusb -t")

    # Check dmesg for USB hub detection
    print("\n--- Recent dmesg ---")
    run("dmesg | tail -20")

    mm.close()
    os.close(fd)

    print("\n=== Test complete. Restore peripheral mode by rebooting or running dwc2_reset.py ===")

if __name__ == '__main__':
    main()
