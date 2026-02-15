#!/usr/bin/env python3
"""Diagnose which STM32 register write breaks touch reporting.

The kernel driver's probe does: POWERON=1, LCD_RST=0, LCD_RST=1, PWM=brightness
After remove (module unload), touch works from userspace.
This script systematically replays each register write to find which one
breaks the touch controller.
"""
import ctypes
import fcntl
import os
import struct
import sys
import time

I2C_SLAVE_FORCE = 0x0706
I2C_RDWR = 0x0707
I2C_DEV = "/dev/i2c-1"
I2C_ADDR = 0x45

# Register addresses from mipi_dsi.h enum (starting at 0x80)
REG_ID         = 0x80
REG_POWERON    = 0x85
REG_PWM        = 0x86
REG_LCD_RST    = 0x93
REG_TP_RST     = 0x94
REG_TP_STATUS  = 0x95
REG_TP_POINT   = 0x96
REG_TP_VERSION = 0x97

I2C_M_RD = 0x0001

class i2c_msg(ctypes.Structure):
    _fields_ = [
        ("addr", ctypes.c_ushort),
        ("flags", ctypes.c_ushort),
        ("len", ctypes.c_ushort),
        ("buf", ctypes.POINTER(ctypes.c_ubyte)),
    ]

class i2c_rdwr_ioctl_data(ctypes.Structure):
    _fields_ = [
        ("msgs", ctypes.POINTER(i2c_msg)),
        ("nmsgs", ctypes.c_uint),
    ]


def i2c_read(fd, reg, length):
    """Read using combined I2C transaction (repeated START)."""
    write_buf = (ctypes.c_ubyte * 1)(reg)
    read_buf = (ctypes.c_ubyte * length)()

    msgs = (i2c_msg * 2)()
    msgs[0].addr = I2C_ADDR
    msgs[0].flags = 0
    msgs[0].len = 1
    msgs[0].buf = write_buf
    msgs[1].addr = I2C_ADDR
    msgs[1].flags = I2C_M_RD
    msgs[1].len = length
    msgs[1].buf = read_buf

    data = i2c_rdwr_ioctl_data()
    data.msgs = msgs
    data.nmsgs = 2

    fcntl.ioctl(fd, I2C_RDWR, data)
    return bytes(read_buf)


def i2c_write_byte(fd, reg, val):
    """Write a single register byte using SMBus-style write."""
    # Write: [reg, val]
    write_buf = (ctypes.c_ubyte * 2)(reg, val)

    msgs = (i2c_msg * 1)()
    msgs[0].addr = I2C_ADDR
    msgs[0].flags = 0
    msgs[0].len = 2
    msgs[0].buf = write_buf

    data = i2c_rdwr_ioctl_data()
    data.msgs = msgs
    data.nmsgs = 1

    fcntl.ioctl(fd, I2C_RDWR, data)


def read_touch_status(fd, label=""):
    """Read and display touch status."""
    data = i2c_read(fd, REG_TP_STATUS, 10)
    status = data[0]
    ready = bool(status & 0x80)
    touch_num = status & 0x0f
    prefix = f"  [{label}]" if label else "  "
    print(f"{prefix} STATUS=0x{status:02x} (ready={ready}, touches={touch_num}) data={data.hex()}")
    return status


def read_touch_n_times(fd, n, label=""):
    """Read touch status N times, report summary."""
    ready_count = 0
    for i in range(n):
        status = read_touch_status(fd, f"{label} {i+1}/{n}" if i < 3 or i == n-1 else None)
        if status & 0x80:
            ready_count += 1
        time.sleep(0.05)
    if n > 4:
        print(f"  => {ready_count}/{n} reads had BUFFER_STATUS_READY set")
    return ready_count


def main():
    fd = os.open(I2C_DEV, os.O_RDWR)

    print("=" * 60)
    print("Touch State Diagnosis")
    print("=" * 60)

    # Step 0: Read chip ID to verify communication
    print("\n--- Step 0: Verify I2C communication ---")
    chip_id = i2c_read(fd, REG_ID, 1)
    print(f"  Chip ID: 0x{chip_id[0]:02x} (expected 0xC3)")
    if chip_id[0] != 0xC3:
        print("  WARNING: Unexpected chip ID!")

    fw_ver = i2c_read(fd, REG_TP_VERSION, 2)
    print(f"  FW version: {fw_ver[0]}.{fw_ver[1]}")

    # Step 1: Baseline - read current state
    print("\n--- Step 1: Current touch state ---")
    read_touch_n_times(fd, 5, "baseline")

    # Step 2: Try writing acknowledge (0x00 to TP_STATUS)
    print("\n--- Step 2: Write acknowledge (0x00 to TP_STATUS), then read ---")
    i2c_write_byte(fd, REG_TP_STATUS, 0x00)
    time.sleep(0.1)
    read_touch_n_times(fd, 5, "after ack")

    # Step 3: Try touch reset
    print("\n--- Step 3: Touch reset (TP_RST=1), then read ---")
    i2c_write_byte(fd, REG_TP_RST, 1)
    time.sleep(0.5)
    read_touch_n_times(fd, 5, "after tp_rst=1")

    # Step 4: If still no data, try TP_RST=0 then 1
    print("\n--- Step 4: Touch reset cycle (TP_RST=0, wait, TP_RST=1), then read ---")
    i2c_write_byte(fd, REG_TP_RST, 0)
    time.sleep(0.1)
    i2c_write_byte(fd, REG_TP_RST, 1)
    time.sleep(0.5)
    read_touch_n_times(fd, 5, "after tp_rst cycle")

    # Step 5: Now simulate probe sequence
    print("\n--- Step 5: Simulate probe: POWERON=1 ---")
    i2c_write_byte(fd, REG_POWERON, 1)
    time.sleep(0.1)
    read_touch_n_times(fd, 5, "after poweron=1")

    # Step 6: LCD reset toggle (as probe does)
    print("\n--- Step 6: LCD_RST toggle (0, wait 20ms, 1, wait 50ms) ---")
    i2c_write_byte(fd, REG_LCD_RST, 0)
    time.sleep(0.02)
    i2c_write_byte(fd, REG_LCD_RST, 1)
    time.sleep(0.05)
    read_touch_n_times(fd, 5, "after lcd_rst toggle")

    # Step 7: Set backlight
    print("\n--- Step 7: Set PWM=255 (backlight) ---")
    i2c_write_byte(fd, REG_PWM, 255)
    time.sleep(0.1)
    read_touch_n_times(fd, 5, "after pwm=255")

    # Step 8: Try acknowledge again
    print("\n--- Step 8: Write acknowledge again, then rapid read ---")
    i2c_write_byte(fd, REG_TP_STATUS, 0x00)
    time.sleep(0.05)
    read_touch_n_times(fd, 10, "after final ack")

    # Step 9: Try touch reset after all the probe stuff
    print("\n--- Step 9: Touch reset after probe sequence ---")
    i2c_write_byte(fd, REG_TP_RST, 0)
    time.sleep(0.1)
    i2c_write_byte(fd, REG_TP_RST, 1)
    time.sleep(1.0)
    read_touch_n_times(fd, 10, "after late tp_rst")

    # Step 10: Undo everything - POWERON=0
    print("\n--- Step 10: Undo: POWERON=0, LCD_RST=0 (like module unload) ---")
    i2c_write_byte(fd, REG_POWERON, 0)
    i2c_write_byte(fd, REG_LCD_RST, 0)
    i2c_write_byte(fd, REG_PWM, 0)
    time.sleep(0.5)
    read_touch_n_times(fd, 10, "after power off")

    os.close(fd)
    print("\n" + "=" * 60)
    print("Done. Compare which steps show BUFFER_STATUS_READY.")
    print("=" * 60)


if __name__ == "__main__":
    main()
