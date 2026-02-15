#!/usr/bin/env python3
"""Add debug prints to touch_panel.c"""
import sys

path = "/opt/seeed-linux-dtoverlays/modules/mipi_dsi/touch_panel.c"

with open(path) as f:
    content = f.read()

# Check if already patched
if "seeed-tp: status=" in content:
    print("Already patched, skipping")
    sys.exit(0)

# Add debug print after i2c_md_read in goodix_ts_read_input_report
old = """\t\tret = i2c_md_read(md, REG_TP_STATUS, data, header);
\t\tif (ret < 0) {
\t\t\treturn -EIO;
\t\t}

\t\tif (data[0] & GOODIX_BUFFER_STATUS_READY) {"""

new = """\t\tret = i2c_md_read(md, REG_TP_STATUS, data, header);
\t\tif (ret < 0) {
\t\t\tprintk_ratelimited(KERN_INFO "seeed-tp: i2c read failed ret=%d\\n", ret);
\t\t\treturn -EIO;
\t\t}
\t\tprintk_ratelimited(KERN_INFO "seeed-tp: status=0x%02x data=0x%02x%02x ret=%d\\n", data[0], data[1], data[2], ret);

\t\tif (data[0] & GOODIX_BUFFER_STATUS_READY) {"""

if old not in content:
    print("ERROR: Could not find target code to patch")
    print("Looking for:", repr(old[:80]))
    # Show what's actually there
    import re
    m = re.search(r'i2c_md_read\(md, REG_TP_STATUS.*?\n.*?\n.*?\n.*?\n.*?GOODIX_BUFFER', content, re.DOTALL)
    if m:
        print("Found:", repr(m.group()))
    sys.exit(1)

content = content.replace(old, new)

with open(path, "w") as f:
    f.write(content)

print("Patched successfully")
