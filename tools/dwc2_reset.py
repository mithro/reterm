#!/usr/bin/env python3
"""Force DWC2 USB controller into device mode via register manipulation.

The BCM2711's DWC2 controller at 0xfe980000 may not properly initialize
after the boot ROM used it for rpiboot. This script:
1. Unbinds g_serial from the UDC
2. Performs a DWC2 core soft reset via GRSTCTL
3. Forces device mode via GUSBCFG
4. Clears soft disconnect via DCTL
5. Rebinds g_serial

Must be run as root.
"""
import mmap
import os
import struct
import time
import subprocess
import sys

# DWC2 register base on BCM2711
DWC2_BASE = 0xfe980000
DWC2_SIZE = 0x1000

# Key register offsets
GOTGCTL  = 0x000
GOTGINT  = 0x004
GAHBCFG  = 0x008
GUSBCFG  = 0x00C
GRSTCTL  = 0x010
GINTSTS  = 0x014
GINTMSK  = 0x018
GRXFSIZ  = 0x024
GNPTXFSIZ = 0x028
DCFG     = 0x800
DCTL     = 0x804
DSTS     = 0x808
PCGCTL   = 0xE00

# Register bits
GRSTCTL_CSFTRST     = (1 << 0)   # Core soft reset
GRSTCTL_AHBIDLE     = (1 << 31)  # AHB master idle
GUSBCFG_FORCEDEVMODE = (1 << 30)  # Force device mode
GUSBCFG_FORCEHSTMODE = (1 << 29)  # Force host mode
DCTL_SFTDISCON      = (1 << 1)   # Soft disconnect
DCTL_SDNAKEFF       = (1 << 7)   # Set global OUT NAK effective
DCTL_SGNPINNAK      = (1 << 7)   # Set global non-periodic IN NAK
GOTGCTL_BSESVLD     = (1 << 19)  # B-session valid (VBUS detected)
GOTGCTL_CONIDSTS    = (1 << 16)  # Connector ID status

def read_reg(mm, offset):
    mm.seek(offset)
    return struct.unpack('<I', mm.read(4))[0]

def write_reg(mm, offset, value):
    mm.seek(offset)
    mm.write(struct.pack('<I', value))

def dump_key_regs(mm, label=""):
    if label:
        print(f"\n=== {label} ===")
    gotgctl = read_reg(mm, GOTGCTL)
    gusbcfg = read_reg(mm, GUSBCFG)
    grstctl = read_reg(mm, GRSTCTL)
    gintsts = read_reg(mm, GINTSTS)
    dcfg = read_reg(mm, DCFG)
    dctl = read_reg(mm, DCTL)
    dsts = read_reg(mm, DSTS)
    pcgctl = read_reg(mm, PCGCTL)

    print(f"  GOTGCTL  = 0x{gotgctl:08x}  VBUS={'yes' if gotgctl & GOTGCTL_BSESVLD else 'no'}, B-dev={'yes' if gotgctl & GOTGCTL_CONIDSTS else 'no'}")
    print(f"  GUSBCFG  = 0x{gusbcfg:08x}  ForceDevMode={'yes' if gusbcfg & GUSBCFG_FORCEDEVMODE else 'no'}, ForceHstMode={'yes' if gusbcfg & GUSBCFG_FORCEHSTMODE else 'no'}")
    print(f"  GRSTCTL  = 0x{grstctl:08x}  AHBIdle={'yes' if grstctl & GRSTCTL_AHBIDLE else 'no'}")
    print(f"  GINTSTS  = 0x{gintsts:08x}")
    print(f"  DCFG     = 0x{dcfg:08x}")
    print(f"  DCTL     = 0x{dctl:08x}  SftDiscon={'yes' if dctl & DCTL_SFTDISCON else 'no'}")
    print(f"  DSTS     = 0x{dsts:08x}  EnumSpd={((dsts >> 1) & 3)}")
    print(f"  PCGCTL   = 0x{pcgctl:08x}")

    return {
        'gotgctl': gotgctl, 'gusbcfg': gusbcfg, 'grstctl': grstctl,
        'gintsts': gintsts, 'dcfg': dcfg, 'dctl': dctl, 'dsts': dsts,
        'pcgctl': pcgctl,
    }

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

    # Open /dev/mem
    fd = os.open('/dev/mem', os.O_RDWR | os.O_SYNC)
    mm = mmap.mmap(fd, DWC2_SIZE, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                   offset=DWC2_BASE)

    dump_key_regs(mm, "Initial state")

    # Step 1: Read UDC state
    print("\n--- Step 1: Check UDC state ---")
    run("cat /sys/class/udc/fe980000.usb/state")
    run("cat /sys/class/udc/fe980000.usb/function")

    # Step 2: Unbind g_serial
    print("\n--- Step 2: Unbind g_serial ---")
    run("modprobe -r g_serial")
    time.sleep(0.5)

    dump_key_regs(mm, "After g_serial removed")

    # Step 3: Unbind DWC2 from platform
    print("\n--- Step 3: Unbind DWC2 from platform ---")
    run("echo fe980000.usb | tee /sys/bus/platform/drivers/dwc2/unbind")
    time.sleep(1)

    dump_key_regs(mm, "After DWC2 unbound")

    # Step 4: Core soft reset
    print("\n--- Step 4: DWC2 core soft reset ---")

    # Wait for AHB idle
    for i in range(100):
        grstctl = read_reg(mm, GRSTCTL)
        if grstctl & GRSTCTL_AHBIDLE:
            break
        time.sleep(0.01)
    else:
        print("WARNING: AHB not idle after 1s")

    # Trigger core soft reset
    grstctl = read_reg(mm, GRSTCTL)
    write_reg(mm, GRSTCTL, grstctl | GRSTCTL_CSFTRST)

    # Wait for reset to complete (bit 0 self-clears)
    for i in range(100):
        time.sleep(0.01)
        grstctl = read_reg(mm, GRSTCTL)
        if not (grstctl & GRSTCTL_CSFTRST):
            print(f"  Core reset completed in {(i+1)*10}ms")
            break
    else:
        print("WARNING: Core reset did not complete in 1s")

    time.sleep(0.1)
    dump_key_regs(mm, "After core reset")

    # Step 5: Force device mode
    print("\n--- Step 5: Force device mode ---")
    gusbcfg = read_reg(mm, GUSBCFG)
    gusbcfg |= GUSBCFG_FORCEDEVMODE   # Force device mode
    gusbcfg &= ~GUSBCFG_FORCEHSTMODE  # Clear force host mode
    write_reg(mm, GUSBCFG, gusbcfg)

    # Wait 25ms for mode change to take effect (per DWC2 spec)
    time.sleep(0.05)

    dump_key_regs(mm, "After forcing device mode")

    # Step 6: Clear power/clock gating
    print("\n--- Step 6: Clear power/clock gating ---")
    write_reg(mm, PCGCTL, 0)
    time.sleep(0.01)

    # Step 7: Clear soft disconnect (connect D+ pull-up)
    print("\n--- Step 7: Clear soft disconnect ---")
    dctl = read_reg(mm, DCTL)
    dctl &= ~DCTL_SFTDISCON
    write_reg(mm, DCTL, dctl)
    time.sleep(0.01)

    dump_key_regs(mm, "After clearing soft disconnect")

    # Step 8: Rebind DWC2 platform driver
    print("\n--- Step 8: Rebind DWC2 ---")
    run("echo fe980000.usb | tee /sys/bus/platform/drivers/dwc2/bind")
    time.sleep(2)

    dump_key_regs(mm, "After DWC2 rebound")

    # Step 9: Load g_serial
    print("\n--- Step 9: Load g_serial ---")
    run("modprobe g_serial")
    time.sleep(1)

    dump_key_regs(mm, "After g_serial loaded")

    # Step 10: Check final state
    print("\n--- Step 10: Final state ---")
    run("cat /sys/class/udc/fe980000.usb/state")
    run("cat /sys/class/udc/fe980000.usb/function")
    run("grep fe980000 /proc/interrupts")

    # Step 11: Try soft_connect
    print("\n--- Step 11: Force soft connect ---")
    run("echo connect | tee /sys/class/udc/fe980000.usb/soft_connect")
    time.sleep(1)

    dump_key_regs(mm, "After soft connect")
    run("cat /sys/class/udc/fe980000.usb/state")

    mm.close()
    os.close(fd)

    print("\n=== Done. Check host dmesg for USB enumeration. ===")

if __name__ == '__main__':
    main()
