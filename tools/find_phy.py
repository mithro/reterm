#!/usr/bin/env python3
"""Find the device tree node with a specific phandle."""
import os
import struct

TARGET = 0x17  # PHY phandle from usb@7e980000/phys

for dirpath, dirnames, filenames in os.walk("/proc/device-tree"):
    if "phandle" in filenames:
        path = os.path.join(dirpath, "phandle")
        with open(path, "rb") as f:
            data = f.read()
        if len(data) == 4:
            val = struct.unpack(">I", data)[0]
            if val == TARGET:
                print(f"Found phandle 0x{TARGET:x} at: {dirpath}")
                for fn in ["compatible", "status", "name"]:
                    fp = os.path.join(dirpath, fn)
                    if os.path.exists(fp):
                        with open(fp, "rb") as f2:
                            content = f2.read()
                            print(f"  {fn}: {content!r}")
                # List all files
                for fn in sorted(filenames):
                    print(f"  file: {fn}")
