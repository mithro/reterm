#!/usr/bin/env python3
"""Test GPIO buttons on reTerminal.

Reads key events from the gpio_keys input device for 30 seconds.
Expects keycodes 30 (A), 31 (S), 32 (D), 33 (F) from the 4 buttons.

Press each button at least once to verify all 4 work.
"""
import glob
import struct
import time
import sys

# Find gpio_keys event device
gpio_event = None
for handler_path in glob.glob("/sys/class/input/event*/device/name"):
    with open(handler_path) as f:
        if f.read().strip() == "gpio_keys":
            gpio_event = "/dev/input/" + handler_path.split("/")[4]
            break

if not gpio_event:
    print("ERROR: gpio_keys device not found")
    sys.exit(1)

print(f"GPIO keys device: {gpio_event}")
print("Press each of the 4 buttons. Waiting 30 seconds...")
print()

KEYCODE_NAMES = {30: "A (button 1)", 31: "S (button 2)", 32: "D (button 3)", 33: "F (button 4)"}
EVENT_SIZE = struct.calcsize("llHHI")
EV_KEY = 0x01
KEY_PRESS = 1
KEY_RELEASE = 0

buttons_seen = set()
events = []

start = time.time()
with open(gpio_event, "rb") as f:
    import select
    while time.time() - start < 30:
        remaining = 30 - (time.time() - start)
        if remaining <= 0:
            break
        ready, _, _ = select.select([f], [], [], min(remaining, 1.0))
        if not ready:
            continue
        data = f.read(EVENT_SIZE)
        if len(data) < EVENT_SIZE:
            continue
        tv_sec, tv_usec, ev_type, code, value = struct.unpack("llHHI", data)
        if ev_type == EV_KEY:
            action = "PRESS" if value == KEY_PRESS else "RELEASE" if value == KEY_RELEASE else f"REPEAT({value})"
            name = KEYCODE_NAMES.get(code, f"unknown({code})")
            elapsed = time.time() - start
            print(f"  [{elapsed:5.1f}s] keycode={code} ({name}) {action}")
            if value == KEY_PRESS:
                buttons_seen.add(code)
                events.append((code, name))
            # If all 4 buttons seen, we can stop early
            if buttons_seen == {30, 31, 32, 33}:
                print("\nAll 4 buttons detected! Waiting 2 more seconds for any extra...")
                time.sleep(2)
                break

print()
print("=" * 50)
print(f"Buttons detected: {len(buttons_seen)}/4")
for code in sorted(KEYCODE_NAMES.keys()):
    name = KEYCODE_NAMES[code]
    status = "PASS" if code in buttons_seen else "NOT PRESSED"
    print(f"  keycode {code} ({name}): {status}")

if buttons_seen == {30, 31, 32, 33}:
    print("\nRESULT: ALL 4 BUTTONS WORKING")
else:
    missing = {30, 31, 32, 33} - buttons_seen
    print(f"\nRESULT: {len(missing)} button(s) not detected")
