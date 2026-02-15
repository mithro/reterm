#!/usr/bin/env python3
"""Fix the i2c_md_read function in mipi_dsi_drv.c to use combined I2C transactions.

The original code does two separate I2C transfers (STOP between address write and
data read), which causes the STM32 MCU to reset its register pointer. The fix uses
a single i2c_transfer call with 2 messages, producing a repeated START.
"""
import sys

path = "/opt/seeed-linux-dtoverlays/modules/mipi_dsi/mipi_dsi_drv.c"

with open(path) as f:
    content = f.read()

# The old i2c_md_read function (two separate transfers)
old_func = """/*static */int i2c_md_read(struct i2c_mipi_dsi *md, u8 reg, u8 *buf, int len)
{
\tstruct i2c_client *client = md->i2c;
\tstruct i2c_msg msgs[1];
\tu8 addr_buf[1] = { reg };
\tu8 data_buf[1] = { 0, };
\tint ret;

\tmutex_lock(&md->mutex);
\t/* Write register address */
\tmsgs[0].addr = client->addr;
\tmsgs[0].flags = 0;
\tmsgs[0].len = ARRAY_SIZE(addr_buf);
\tmsgs[0].buf = addr_buf;

\tret = i2c_transfer(client->adapter, msgs, ARRAY_SIZE(msgs));
\tif (ret != ARRAY_SIZE(msgs)) {
\t\tmutex_unlock(&md->mutex);
\t\treturn -EIO;
\t}

\tusleep_range(1000, 1500);

\t/* Read data from register */
\tmsgs[0].addr = client->addr;
\tmsgs[0].flags = I2C_M_RD;
\tif (NULL == buf) {
\t\tmsgs[0].len = 1;
\t\tmsgs[0].buf = data_buf;
\t}
\telse {
\t\tmsgs[0].len = len;
\t\tmsgs[0].buf = buf;
\t}

\tret = i2c_transfer(client->adapter, msgs, ARRAY_SIZE(msgs));
\tif (ret != ARRAY_SIZE(msgs)) {
\t\tmutex_unlock(&md->mutex);
\t\treturn -EIO;
\t}
\tmutex_unlock(&md->mutex);

\tif (NULL == buf) {
\t\treturn data_buf[0];\t
\t}
\telse {
\t\treturn ret;
\t}
}"""

# The new i2c_md_read function (combined transaction with repeated START)
new_func = """/*static */int i2c_md_read(struct i2c_mipi_dsi *md, u8 reg, u8 *buf, int len)
{
\tstruct i2c_client *client = md->i2c;
\tstruct i2c_msg msgs[2];
\tu8 addr_buf[1] = { reg };
\tu8 data_buf[1] = { 0, };
\tint ret;

\tmutex_lock(&md->mutex);

\t/* Combined I2C transaction: write register address, repeated START, read data */
\tmsgs[0].addr = client->addr;
\tmsgs[0].flags = 0;
\tmsgs[0].len = ARRAY_SIZE(addr_buf);
\tmsgs[0].buf = addr_buf;

\tmsgs[1].addr = client->addr;
\tmsgs[1].flags = I2C_M_RD;
\tif (NULL == buf) {
\t\tmsgs[1].len = 1;
\t\tmsgs[1].buf = data_buf;
\t}
\telse {
\t\tmsgs[1].len = len;
\t\tmsgs[1].buf = buf;
\t}

\tret = i2c_transfer(client->adapter, msgs, ARRAY_SIZE(msgs));
\tif (ret != ARRAY_SIZE(msgs)) {
\t\tmutex_unlock(&md->mutex);
\t\treturn -EIO;
\t}
\tmutex_unlock(&md->mutex);

\tif (NULL == buf) {
\t\treturn data_buf[0];
\t}
\telse {
\t\treturn ret;
\t}
}"""

if old_func not in content:
    print("ERROR: Could not find original i2c_md_read function")
    print("It may have been modified already. Checking...")
    if "struct i2c_msg msgs[2];" in content:
        print("Already patched (msgs[2] found)")
        sys.exit(0)
    # Show what we're looking for
    idx = content.find("/*static */int i2c_md_read")
    if idx >= 0:
        print(f"Found function at offset {idx}:")
        print(repr(content[idx:idx+200]))
    sys.exit(1)

content = content.replace(old_func, new_func)

with open(path, "w") as f:
    f.write(content)

print("Fixed i2c_md_read to use combined I2C transactions")
