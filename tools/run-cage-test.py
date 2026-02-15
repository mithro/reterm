#!/usr/bin/env python3
"""Run cage on a VT and capture its output."""
import subprocess
import os
import sys

# Write a tiny test app that just logs and sleeps
test_script = "/home/tim/cage-child.sh"
with open(test_script, "w") as f:
    f.write("#!/bin/bash\n")
    f.write("echo 'CHILD STARTED' > /home/tim/cage-child.log\n")
    f.write("echo \"USER=$(whoami)\" >> /home/tim/cage-child.log\n")
    f.write("env >> /home/tim/cage-child.log\n")
    f.write("wlr-randr >> /home/tim/cage-child.log 2>&1\n")
    f.write("sleep 60\n")
os.chmod(test_script, 0o755)

# Remove old log
try:
    os.unlink("/home/tim/cage-child.log")
except FileNotFoundError:
    pass

# Try running cage on tty7 directly
# First, make sure we have VT access
print("Running cage on tty7...")
result = subprocess.run(
    ["sudo", "openvt", "-c", "7", "-s", "-w", "--",
     "sudo", "-u", "tim",
     "cage", "-s", "--", test_script],
    capture_output=True, text=True, timeout=15
)
print(f"Exit code: {result.returncode}")
print(f"Stdout: {result.stdout}")
print(f"Stderr: {result.stderr}")

# Check if child log exists
if os.path.exists("/home/tim/cage-child.log"):
    with open("/home/tim/cage-child.log") as f:
        print(f"Child log:\n{f.read()}")
else:
    print("No child log created - cage failed to launch child")
