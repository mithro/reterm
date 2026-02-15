#!/usr/bin/env python3
"""Check I2C adapter functionality flags."""
import fcntl
import os
import struct

I2C_FUNCS = 0x0705
I2C_DEV = "/dev/i2c-1"

# I2C functionality flags
flags = {
    0x00000001: "I2C_FUNC_I2C",
    0x00000002: "I2C_FUNC_10BIT_ADDR",
    0x00000004: "I2C_FUNC_PROTOCOL_MANGLING",
    0x00000008: "I2C_FUNC_SMBUS_PEC",
    0x00000010: "I2C_FUNC_NOSTART",
    0x00000020: "I2C_FUNC_SLAVE",
    0x00010000: "I2C_FUNC_SMBUS_BLOCK_PROC_CALL",
    0x00020000: "I2C_FUNC_SMBUS_QUICK",
    0x00040000: "I2C_FUNC_SMBUS_READ_BYTE",
    0x00080000: "I2C_FUNC_SMBUS_WRITE_BYTE",
    0x00100000: "I2C_FUNC_SMBUS_READ_BYTE_DATA",
    0x00200000: "I2C_FUNC_SMBUS_WRITE_BYTE_DATA",
    0x00400000: "I2C_FUNC_SMBUS_READ_WORD_DATA",
    0x00800000: "I2C_FUNC_SMBUS_WRITE_WORD_DATA",
    0x01000000: "I2C_FUNC_SMBUS_PROC_CALL",
    0x02000000: "I2C_FUNC_SMBUS_READ_BLOCK_DATA",
    0x04000000: "I2C_FUNC_SMBUS_WRITE_BLOCK_DATA",
    0x08000000: "I2C_FUNC_SMBUS_READ_I2C_BLOCK",
    0x10000000: "I2C_FUNC_SMBUS_WRITE_I2C_BLOCK",
}

fd = os.open(I2C_DEV, os.O_RDWR)
buf = bytearray(8)
fcntl.ioctl(fd, I2C_FUNCS, buf)
funcs = struct.unpack("L", buf[:8])[0]
os.close(fd)

print(f"I2C adapter functionality: 0x{funcs:08x}")
for bit, name in sorted(flags.items()):
    if funcs & bit:
        print(f"  [YES] {name}")
    else:
        print(f"  [ NO] {name}")

# Key check
if funcs & 0x00000001:
    print("\n=> I2C_FUNC_I2C is SET: combined transactions (repeated START) supported")
else:
    print("\n=> I2C_FUNC_I2C is NOT SET: combined transactions NOT supported!")
