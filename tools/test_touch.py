#!/usr/bin/env python3
"""Test the reTerminal touchscreen I2C registers directly."""
import struct
import time
import fcntl

# I2C constants
I2C_SLAVE = 0x0703
I2C_SLAVE_FORCE = 0x0706
I2C_DEV = "/dev/i2c-1"
I2C_ADDR = 0x45

# Register addresses (from mipi_dsi.h enum, starting at 0x80)
REG_ID = 0x80
REG_TP_RST = 0x94
REG_TP_STATUS = 0x95
REG_TP_POINT = 0x96
REG_TP_VERSION = 0x97

def i2c_read(fd, reg, length):
    """Read from I2C register."""
    import os
    os.write(fd, bytes([reg]))
    return os.read(fd, length)

def i2c_write(fd, reg, data):
    """Write to I2C register."""
    import os
    os.write(fd, bytes([reg]) + data)

def main():
    import os

    fd = os.open(I2C_DEV, os.O_RDWR)
    fcntl.ioctl(fd, I2C_SLAVE_FORCE, I2C_ADDR)

    # Read ID
    data = i2c_read(fd, REG_ID, 1)
    print(f"REG_ID (0x80): 0x{data[0]:02x}")

    # Read TP version
    data = i2c_read(fd, REG_TP_VERSION, 1)
    print(f"REG_TP_VERSION (0x97): 0x{data[0]:02x}")

    # Read TP status several times
    print("\nPolling TP_STATUS for 5 seconds (tap the screen!):")
    for i in range(100):
        data = i2c_read(fd, REG_TP_STATUS, 10)
        status = data[0]
        if status & 0x80:  # GOODIX_BUFFER_STATUS_READY
            touch_num = status & 0x0f
            print(f"  [{i:3d}] STATUS=0x{status:02x} touches={touch_num} data={data.hex()}")
        elif i % 20 == 0:
            print(f"  [{i:3d}] STATUS=0x{status:02x} (no touch)")
        time.sleep(0.05)

    os.close(fd)
    print("Done.")

if __name__ == "__main__":
    main()
