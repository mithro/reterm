#!/usr/bin/env python3
"""Test touch I2C reads using BOTH separate and combined transactions.

This helps determine if the combined transaction (repeated START) is the
issue, or if it's something else about the kernel driver.
"""
import ctypes
import fcntl
import os
import struct
import time

I2C_SLAVE_FORCE = 0x0706
I2C_RDWR = 0x0707
I2C_DEV = "/dev/i2c-1"
I2C_ADDR = 0x45
REG_TP_STATUS = 0x95

# Structures for I2C_RDWR ioctl
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

I2C_M_RD = 0x0001

def read_separate(fd, reg, length):
    """Read using separate write + read (two transactions, STOP between)."""
    os.write(fd, bytes([reg]))
    return os.read(fd, length)

def read_combined(fd, reg, length):
    """Read using combined transaction (I2C_RDWR, repeated START)."""
    write_buf = (ctypes.c_ubyte * 1)(reg)
    read_buf = (ctypes.c_ubyte * length)()

    msgs = (i2c_msg * 2)()
    msgs[0].addr = I2C_ADDR
    msgs[0].flags = 0  # write
    msgs[0].len = 1
    msgs[0].buf = write_buf
    msgs[1].addr = I2C_ADDR
    msgs[1].flags = I2C_M_RD  # read
    msgs[1].len = length
    msgs[1].buf = read_buf

    data = i2c_rdwr_ioctl_data()
    data.msgs = msgs
    data.nmsgs = 2

    fcntl.ioctl(fd, I2C_RDWR, data)
    return bytes(read_buf)

def main():
    fd = os.open(I2C_DEV, os.O_RDWR)
    fcntl.ioctl(fd, I2C_SLAVE_FORCE, I2C_ADDR)

    print("Testing SEPARATE transactions (STOP between write/read):")
    for i in range(20):
        data = read_separate(fd, REG_TP_STATUS, 10)
        status = data[0]
        if status & 0x80 or i % 5 == 0:
            print(f"  [{i:2d}] STATUS=0x{status:02x} data={data.hex()}")
        time.sleep(0.05)

    print("\nTesting COMBINED transactions (repeated START, no STOP):")
    for i in range(20):
        data = read_combined(fd, REG_TP_STATUS, 10)
        status = data[0]
        if status & 0x80 or i % 5 == 0:
            print(f"  [{i:2d}] STATUS=0x{status:02x} data={data.hex()}")
        time.sleep(0.05)

    os.close(fd)
    print("\nDone.")

if __name__ == "__main__":
    main()
