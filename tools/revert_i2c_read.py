#!/usr/bin/env python3
"""Revert i2c_md_read to use separate I2C transactions (the working approach).

Testing proved that the STM32 MCU on the reTerminal does NOT support
combined transactions (repeated START). Separate transactions (with STOP
between write and read) work correctly for touch status reads.
"""
import sys

path = "/opt/seeed-linux-dtoverlays/modules/mipi_dsi/mipi_dsi_drv.c"

with open(path) as f:
    content = f.read()

# The current (broken) combined transaction version
combined_func = """/*static */int i2c_md_read(struct i2c_mipi_dsi *md, u8 reg, u8 *buf, int len)
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

# The fixed version: separate transactions (STOP between write and read)
separate_func = """/*static */int i2c_md_read(struct i2c_mipi_dsi *md, u8 reg, u8 *buf, int len)
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

if combined_func not in content:
    print("ERROR: Could not find combined i2c_md_read function")
    if "struct i2c_msg msgs[1];" in content:
        print("Already using separate transactions (msgs[1] found)")
        sys.exit(0)
    idx = content.find("/*static */int i2c_md_read")
    if idx >= 0:
        print(f"Found function at offset {idx}:")
        print(repr(content[idx:idx+300]))
    sys.exit(1)

content = content.replace(combined_func, separate_func)

with open(path, "w") as f:
    f.write(content)

print("Reverted i2c_md_read to use separate I2C transactions")
