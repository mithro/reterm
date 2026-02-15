#!/usr/bin/env python3
"""Test all I2C transaction methods for touch reading.

Compare: 1) I2C_SLAVE_FORCE + os.write/os.read (separate, STOP between)
         2) I2C_RDWR with msgs[2] (combined, repeated START)
         3) SMBus read_byte_data equivalent
"""
import ctypes
import fcntl
import os
import struct
import time

I2C_SLAVE_FORCE = 0x0706
I2C_RDWR = 0x0707
I2C_SMBUS = 0x0720
I2C_DEV = "/dev/i2c-1"
I2C_ADDR = 0x45
REG_TP_STATUS = 0x95

I2C_M_RD = 0x0001

# SMBus constants
I2C_SMBUS_READ = 1
I2C_SMBUS_BYTE_DATA = 2
I2C_SMBUS_I2C_BLOCK_DATA = 8

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

class i2c_smbus_data(ctypes.Union):
    _fields_ = [
        ("byte", ctypes.c_ubyte),
        ("word", ctypes.c_ushort),
        ("block", ctypes.c_ubyte * 34),
    ]

class i2c_smbus_ioctl_data(ctypes.Structure):
    _fields_ = [
        ("read_write", ctypes.c_ubyte),
        ("command", ctypes.c_ubyte),
        ("size", ctypes.c_uint),
        ("data", ctypes.POINTER(i2c_smbus_data)),
    ]


def method_separate(fd, reg, length):
    """Method 1: I2C_SLAVE_FORCE + os.write + os.read (separate transactions, STOP between)."""
    os.write(fd, bytes([reg]))
    return os.read(fd, length)


def method_combined(fd, reg, length):
    """Method 2: I2C_RDWR with 2 messages (combined transaction, repeated START)."""
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


def method_smbus_byte(fd, reg):
    """Method 3: SMBus read_byte_data (kernel i2c_smbus_read_byte_data equivalent)."""
    data = i2c_smbus_data()
    args = i2c_smbus_ioctl_data()
    args.read_write = I2C_SMBUS_READ
    args.command = reg
    args.size = I2C_SMBUS_BYTE_DATA
    args.data = ctypes.pointer(data)
    fcntl.ioctl(fd, I2C_SMBUS, args)
    return bytes([data.byte])


def method_smbus_block(fd, reg, length):
    """Method 4: SMBus I2C block read."""
    data = i2c_smbus_data()
    data.block[0] = length
    args = i2c_smbus_ioctl_data()
    args.read_write = I2C_SMBUS_READ
    args.command = reg
    args.size = I2C_SMBUS_I2C_BLOCK_DATA
    args.data = ctypes.pointer(data)
    fcntl.ioctl(fd, I2C_SMBUS, args)
    return bytes(data.block[1:length+1])


def main():
    fd = os.open(I2C_DEV, os.O_RDWR)
    fcntl.ioctl(fd, I2C_SLAVE_FORCE, I2C_ADDR)

    print("Testing touch I2C reads with different transaction methods")
    print("=" * 70)

    length = 10

    print("\nMethod 1: SEPARATE transactions (I2C_SLAVE_FORCE + write/read, STOP between)")
    for i in range(10):
        data = method_separate(fd, REG_TP_STATUS, length)
        status = data[0]
        ready = bool(status & 0x80)
        print(f"  [{i:2d}] STATUS=0x{status:02x} ready={ready} data={data.hex()}")
        time.sleep(0.05)

    print("\nMethod 2: COMBINED transaction (I2C_RDWR msgs[2], repeated START)")
    for i in range(10):
        data = method_combined(fd, REG_TP_STATUS, length)
        status = data[0]
        ready = bool(status & 0x80)
        print(f"  [{i:2d}] STATUS=0x{status:02x} ready={ready} data={data.hex()}")
        time.sleep(0.05)

    print("\nMethod 3: SMBus read_byte_data (single byte from register)")
    for i in range(10):
        data = method_smbus_byte(fd, REG_TP_STATUS)
        status = data[0]
        ready = bool(status & 0x80)
        print(f"  [{i:2d}] STATUS=0x{status:02x} ready={ready}")
        time.sleep(0.05)

    print("\nMethod 4: SMBus I2C block read (multi-byte from register)")
    for i in range(10):
        try:
            data = method_smbus_block(fd, REG_TP_STATUS, length)
            status = data[0]
            ready = bool(status & 0x80)
            print(f"  [{i:2d}] STATUS=0x{status:02x} ready={ready} data={data.hex()}")
        except OSError as e:
            print(f"  [{i:2d}] ERROR: {e}")
        time.sleep(0.05)

    os.close(fd)
    print("\nDone.")


if __name__ == "__main__":
    main()
