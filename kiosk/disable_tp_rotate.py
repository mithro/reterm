#!/usr/bin/env python3
"""Disable tp_point_rotate in the mipi_dsi touch driver.

The driver's x_y_rotate() and cage's 270-degree wlr-randr transform
double-rotate touch coordinates. Disabling the driver's rotation lets
cage handle it correctly.
"""
import sys

path = "/opt/seeed-linux-dtoverlays/modules/mipi_dsi/touch_panel.c"

with open(path) as f:
    content = f.read()

# Find and disable the x_y_rotate call
old = "\tif(md->tp_point_rotate)\n\t\tx_y_rotate(&input_x, &input_y);"
new = "\t/* Disabled: cage's wlr-randr 270 transform handles rotation.\n\t * Enabling this causes double-rotation of touch coordinates.\n\t * if(md->tp_point_rotate)\n\t *\tx_y_rotate(&input_x, &input_y);\n\t */"

if old not in content:
    if "/* Disabled: cage" in content:
        print("Already patched (x_y_rotate disabled)")
        sys.exit(0)
    print("ERROR: Could not find x_y_rotate call")
    print("Looking for the pattern...")
    idx = content.find("x_y_rotate")
    if idx >= 0:
        print(f"Found at offset {idx}:")
        print(repr(content[max(0,idx-100):idx+100]))
    sys.exit(1)

content = content.replace(old, new)

with open(path, "w") as f:
    f.write(content)

print("Disabled x_y_rotate in touch_panel.c")
