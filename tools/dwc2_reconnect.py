#!/usr/bin/env python3
"""Toggle DWC2 D+ pull-up to force USB re-enumeration.

Rapidly disconnects and reconnects to force the host to notice us.
Also tests if anything changes when we toggle.
"""
import mmap
import os
import struct
import time
import subprocess
import sys

DWC2_BASE = 0xfe980000
DWC2_SIZE = 0x1000
GOTGCTL = 0x000
GUSBCFG = 0x00C
GRSTCTL = 0x010
GINTSTS = 0x014
DCTL = 0x804
DSTS = 0x808

DCTL_SFTDISCON = (1 << 1)
GOTGCTL_BSESVLD = (1 << 19)
GOTGCTL_ASESVLD = (1 << 18)
GRSTCTL_CSFTRST = (1 << 0)
GRSTCTL_AHBIDLE = (1 << 31)

def read_reg(mm, offset):
    mm.seek(offset)
    return struct.unpack('<I', mm.read(4))[0]

def write_reg(mm, offset, value):
    mm.seek(offset)
    mm.write(struct.pack('<I', value))

def main():
    if os.geteuid() != 0:
        print("ERROR: Must be run as root")
        sys.exit(1)

    # First restore peripheral mode: rebind DWC2 and g_serial
    print("--- Restoring peripheral mode ---")
    # Try unbind first (might already be unbound)
    subprocess.run("echo fe980000.usb | tee /sys/bus/platform/drivers/dwc2/unbind",
                   shell=True, capture_output=True)
    time.sleep(0.5)

    fd = os.open('/dev/mem', os.O_RDWR | os.O_SYNC)
    mm = mmap.mmap(fd, DWC2_SIZE, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                   offset=DWC2_BASE)

    # Core reset
    print("--- Core soft reset ---")
    for i in range(100):
        if read_reg(mm, GRSTCTL) & GRSTCTL_AHBIDLE:
            break
        time.sleep(0.01)
    write_reg(mm, GRSTCTL, read_reg(mm, GRSTCTL) | GRSTCTL_CSFTRST)
    for i in range(100):
        time.sleep(0.01)
        if not (read_reg(mm, GRSTCTL) & GRSTCTL_CSFTRST):
            print(f"  Reset done in {(i+1)*10}ms")
            break
    time.sleep(0.1)

    # Rebind
    print("--- Rebinding DWC2 ---")
    subprocess.run("echo fe980000.usb | tee /sys/bus/platform/drivers/dwc2/bind",
                   shell=True, capture_output=True)
    time.sleep(2)

    print("--- Loading g_serial ---")
    subprocess.run("modprobe g_serial", shell=True, capture_output=True)
    time.sleep(1)

    # Read initial state
    gotgctl = read_reg(mm, GOTGCTL)
    dctl = read_reg(mm, DCTL)
    dsts = read_reg(mm, DSTS)
    print(f"\nInitial: GOTGCTL=0x{gotgctl:08x} DCTL=0x{dctl:08x} DSTS=0x{dsts:08x}")
    print(f"  VBUS valid: {bool(gotgctl & GOTGCTL_BSESVLD)}")
    print(f"  A-session valid: {bool(gotgctl & GOTGCTL_ASESVLD)}")
    print(f"  SftDiscon: {bool(dctl & DCTL_SFTDISCON)}")

    # Now toggle D+ rapidly
    print("\n--- Toggling D+ pull-up (disconnect/reconnect) ---")
    for i in range(5):
        # Disconnect (assert soft disconnect)
        dctl = read_reg(mm, DCTL)
        write_reg(mm, DCTL, dctl | DCTL_SFTDISCON)
        time.sleep(0.5)
        dctl_disc = read_reg(mm, DCTL)
        dsts_disc = read_reg(mm, DSTS)
        gintsts_disc = read_reg(mm, GINTSTS)

        # Reconnect (clear soft disconnect)
        dctl = read_reg(mm, DCTL)
        write_reg(mm, DCTL, dctl & ~DCTL_SFTDISCON)
        time.sleep(1.0)
        dctl_conn = read_reg(mm, DCTL)
        dsts_conn = read_reg(mm, DSTS)
        gintsts_conn = read_reg(mm, GINTSTS)

        print(f"  Cycle {i+1}: disc DSTS=0x{dsts_disc:08x} GINTSTS=0x{gintsts_disc:08x}"
              f" | conn DSTS=0x{dsts_conn:08x} GINTSTS=0x{gintsts_conn:08x}")

        # Check if we got a USB reset interrupt (bit 12 of GINTSTS)
        if gintsts_conn & (1 << 12):
            print(f"    *** USB RESET detected! Host is responding! ***")

    # Check interrupt count
    print("\n--- Interrupt count ---")
    result = subprocess.run("grep fe980000 /proc/interrupts", shell=True,
                          capture_output=True, text=True)
    print(f"  {result.stdout.strip()}")

    # Check UDC state
    result = subprocess.run("cat /sys/class/udc/fe980000.usb/state", shell=True,
                          capture_output=True, text=True)
    print(f"\nUDC state: {result.stdout.strip()}")

    mm.close()
    os.close(fd)

if __name__ == '__main__':
    main()
