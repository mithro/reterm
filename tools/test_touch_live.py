#!/usr/bin/env python3
"""Live touch test - reads continuously, shows when touches are detected.

Run this, then touch the screen. STATUS should show 0x8X when touching.
"""
import ctypes
import fcntl
import os
import time

I2C_SLAVE_FORCE = 0x0706
I2C_RDWR = 0x0707
I2C_DEV = "/dev/i2c-1"
I2C_ADDR = 0x45
REG_TP_STATUS = 0x95

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


def read_separate(fd, reg, length):
    """Separate transactions (STOP between write/read)."""
    os.write(fd, bytes([reg]))
    return os.read(fd, length)


def read_combined(fd, reg, length):
    """Combined transaction (repeated START)."""
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


def main():
    fd = os.open(I2C_DEV, os.O_RDWR)
    fcntl.ioctl(fd, I2C_SLAVE_FORCE, I2C_ADDR)

    duration = 30
    print(f"Reading touch status for {duration} seconds...")
    print("TOUCH THE SCREEN NOW! Touch events will be highlighted.")
    print("Testing SEPARATE transactions (STOP between write/read)")
    print("-" * 60)

    start = time.time()
    touch_count = 0
    read_count = 0
    last_status = None

    while time.time() - start < duration:
        data = read_separate(fd, REG_TP_STATUS, 10)
        status = data[0]
        read_count += 1

        if status & 0x80:
            touch_num = status & 0x0f
            touch_count += 1
            # Parse X,Y from first touch point (bytes 2-5)
            x = data[3] | (data[4] << 8)
            y = data[5] | (data[6] << 8)
            print(f"  >>> TOUCH! status=0x{status:02x} points={touch_num} "
                  f"x={x} y={y} data={data.hex()}")
        elif status != last_status:
            elapsed = time.time() - start
            print(f"  [{elapsed:5.1f}s] status=0x{status:02x} data={data.hex()}")

        last_status = status
        time.sleep(0.02)  # 20ms poll

    print(f"\nSummary (separate): {touch_count} touches in {read_count} reads "
          f"({duration}s)")

    # Now test combined
    print(f"\nTesting COMBINED transactions (repeated START) for {duration}s")
    print("TOUCH THE SCREEN AGAIN!")
    print("-" * 60)

    start = time.time()
    touch_count = 0
    read_count = 0
    last_status = None

    while time.time() - start < duration:
        data = read_combined(fd, REG_TP_STATUS, 10)
        status = data[0]
        read_count += 1

        if status & 0x80:
            touch_num = status & 0x0f
            touch_count += 1
            x = data[3] | (data[4] << 8)
            y = data[5] | (data[6] << 8)
            print(f"  >>> TOUCH! status=0x{status:02x} points={touch_num} "
                  f"x={x} y={y} data={data.hex()}")
        elif status != last_status:
            elapsed = time.time() - start
            print(f"  [{elapsed:5.1f}s] status=0x{status:02x} data={data.hex()}")

        last_status = status
        time.sleep(0.02)

    print(f"\nSummary (combined): {touch_count} touches in {read_count} reads "
          f"({duration}s)")

    os.close(fd)


if __name__ == "__main__":
    main()
