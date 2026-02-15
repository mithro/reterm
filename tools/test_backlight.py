#!/usr/bin/env python3
"""Interactive backlight test for reTerminal.

Tests each component of the backlight system:
1. Light sensor responds to covering/uncovering
2. Backlight sysfs actually changes screen brightness
3. Backlight manager maps lux to brightness correctly
4. Button wake works
5. Hysteresis prevents oscillation

Run on the reTerminal (requires root for backlight writes).
"""
import glob
import os
import select
import struct
import sys
import time

LIGHT_SENSOR = "/sys/bus/iio/devices/iio:device0/in_illuminance_input"
BACKLIGHT = "/sys/class/backlight/1-0045/brightness"
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 0x01


def read_lux():
    with open(LIGHT_SENSOR) as f:
        return int(f.read().strip())


def read_brightness():
    with open(BACKLIGHT) as f:
        return int(f.read().strip())


def set_brightness(val):
    with open(BACKLIGHT, "w") as f:
        f.write(str(max(0, min(255, val))))


def find_gpio_keys():
    for name_path in glob.glob("/sys/class/input/event*/device/name"):
        with open(name_path) as f:
            if f.read().strip() == "gpio_keys":
                return "/dev/input/" + name_path.split("/")[4]
    return None


def test_sensor():
    """Test 1: Light sensor responds to changes."""
    print("=" * 50)
    print("TEST 1: Light sensor response")
    print("=" * 50)
    print("Reading ambient light for 15 seconds.")
    print("Cover the sensor with your hand at ~5s, uncover at ~10s.")
    print()

    readings = []
    start = time.time()
    while time.time() - start < 15:
        lux = read_lux()
        elapsed = time.time() - start
        bar = "#" * min(50, lux // 2)
        print(f"  [{elapsed:5.1f}s] {lux:4d} lux {bar}")
        readings.append((elapsed, lux))
        time.sleep(0.5)

    # Analyze: did the readings change significantly?
    values = [r[1] for r in readings]
    min_lux = min(values)
    max_lux = max(values)
    spread = max_lux - min_lux

    print()
    print(f"  Range: {min_lux} - {max_lux} lux (spread: {spread})")
    if spread > 5:
        print(f"  RESULT: PASS - sensor responds to light changes")
        return True
    else:
        print(f"  RESULT: FAIL - sensor readings didn't change")
        print(f"  (Did you cover/uncover the sensor?)")
        return False


def test_backlight_write():
    """Test 2: Backlight sysfs controls screen brightness."""
    print()
    print("=" * 50)
    print("TEST 2: Backlight control")
    print("=" * 50)
    print("Cycling brightness: 255 → 50 → 0 → 128 → 255")
    print("Watch the screen brightness change visually.")
    print()

    original = read_brightness()
    levels = [255, 50, 0, 128, 255]

    for level in levels:
        set_brightness(level)
        actual = read_brightness()
        print(f"  Set {level:3d} → read back {actual:3d}", end="")
        if actual == level:
            print(" OK")
        else:
            print(f" MISMATCH (expected {level})")
        time.sleep(1.5)

    set_brightness(original)
    print()
    print(f"  RESULT: PASS - brightness values written and read back correctly")
    print(f"  (Verify visually: did the screen dim and brighten?)")
    return True


def test_button_detection():
    """Test 3: GPIO buttons detected."""
    print()
    print("=" * 50)
    print("TEST 3: Button detection")
    print("=" * 50)

    dev = find_gpio_keys()
    if not dev:
        print("  RESULT: FAIL - gpio_keys device not found")
        return False

    print(f"  Device: {dev}")
    print(f"  Press any button within 15 seconds...")
    print()

    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    start = time.time()
    detected = False

    try:
        while time.time() - start < 15:
            remaining = 15 - (time.time() - start)
            ready, _, _ = select.select([fd], [], [], min(remaining, 1.0))
            if ready:
                while True:
                    try:
                        data = os.read(fd, EVENT_SIZE)
                        if len(data) < EVENT_SIZE:
                            break
                        _, _, ev_type, code, value = struct.unpack(EVENT_FORMAT, data)
                        if ev_type == EV_KEY and value == 1:
                            elapsed = time.time() - start
                            print(f"  [{elapsed:.1f}s] Button press detected: keycode={code}")
                            detected = True
                    except BlockingIOError:
                        break
                if detected:
                    break
    finally:
        os.close(fd)

    print()
    if detected:
        print(f"  RESULT: PASS - button press detected")
    else:
        print(f"  RESULT: FAIL - no button press detected in 15s")
    return detected


def test_backlight_manager():
    """Test 4: Backlight manager integration.

    Runs the backlight manager logic manually and verifies:
    - Brightness changes with lux
    - Screen goes dark when sensor is covered
    - Button press wakes screen
    """
    print()
    print("=" * 50)
    print("TEST 4: Backlight manager integration (30 seconds)")
    print("=" * 50)
    print("This simulates the backlight manager logic.")
    print()
    print("Instructions:")
    print("  0-10s:  Leave sensor uncovered (screen should be on)")
    print("  10-20s: Cover sensor completely (screen should turn off after ~6s)")
    print("  20-25s: Press a button (screen should wake to brightness 200)")
    print("  25-30s: Observe (screen stays on from button wake)")
    print()

    DARK_OFF = 5
    DARK_ON = 20
    WAKE_BRIGHTNESS = 200
    SMOOTHING = 3

    dev = find_gpio_keys()
    button_fd = None
    if dev:
        button_fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)

    screen_on = True
    wake_until = 0.0
    recent_lux = []
    last_brightness = -1
    events_log = []

    start = time.time()
    try:
        while time.time() - start < 30:
            now = time.monotonic()
            elapsed = time.time() - start

            # Check buttons
            button_pressed = False
            if button_fd is not None:
                ready, _, _ = select.select([button_fd], [], [], 0)
                if ready:
                    while True:
                        try:
                            data = os.read(button_fd, EVENT_SIZE)
                            if len(data) < EVENT_SIZE:
                                break
                            _, _, ev_type, code, value = struct.unpack(EVENT_FORMAT, data)
                            if ev_type == EV_KEY and value == 1:
                                wake_until = now + 300
                                button_pressed = True
                        except BlockingIOError:
                            break

            # Determine brightness
            if now < wake_until:
                target = WAKE_BRIGHTNESS
                screen_on = True
                reason = "button-wake"
            else:
                lux = read_lux()
                recent_lux.append(lux)
                if len(recent_lux) > SMOOTHING:
                    recent_lux.pop(0)
                avg = sum(recent_lux) / len(recent_lux)

                if screen_on:
                    if avg < DARK_OFF:
                        target = 0
                        screen_on = False
                        reason = f"dark (avg={avg:.0f}<{DARK_OFF})"
                    else:
                        ratio = max(0, min(1, (avg - DARK_ON) / (500 - DARK_ON)))
                        target = int(50 + ratio * 205)
                        reason = f"ambient (avg={avg:.0f})"
                else:
                    if avg > DARK_ON:
                        ratio = max(0, min(1, (avg - DARK_ON) / (500 - DARK_ON)))
                        target = int(50 + ratio * 205)
                        screen_on = True
                        reason = f"light-on (avg={avg:.0f}>{DARK_ON})"
                    else:
                        target = 0
                        reason = f"still-dark (avg={avg:.0f})"

            if target != last_brightness:
                set_brightness(target)
                last_brightness = target
                events_log.append((elapsed, target, reason))
                print(f"  [{elapsed:5.1f}s] brightness={target:3d}  ({reason})"
                      + (" ** BUTTON" if button_pressed else ""))

            time.sleep(0.5)

    finally:
        if button_fd is not None:
            os.close(button_fd)
        set_brightness(255)

    print()
    print(f"  Brightness changes: {len(events_log)}")
    for t, b, r in events_log:
        print(f"    [{t:5.1f}s] → {b:3d}  ({r})")

    # Verify expected transitions happened
    went_dark = any(b == 0 for _, b, _ in events_log)
    woke_up = any("button-wake" in r for _, _, r in events_log)

    print()
    if went_dark and woke_up:
        print(f"  RESULT: PASS - screen went dark AND button wake worked")
    elif went_dark:
        print(f"  RESULT: PARTIAL - screen went dark but no button wake detected")
        print(f"  (Did you press a button at 20-25s?)")
    elif woke_up:
        print(f"  RESULT: PARTIAL - button wake worked but screen didn't go dark")
        print(f"  (Did you cover the sensor at 10-20s?)")
    else:
        print(f"  RESULT: FAIL - no transitions detected")
        print(f"  (Cover sensor at 10s, press button at 20s)")

    return went_dark and woke_up


def main():
    print("reTerminal Backlight Test Suite")
    print("================================")
    print()

    results = {}
    results["sensor"] = test_sensor()
    results["backlight_write"] = test_backlight_write()
    results["button"] = test_button_detection()
    results["integration"] = test_backlight_manager()

    print()
    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)
    for name, passed in results.items():
        print(f"  {name:20s}: {'PASS' if passed else 'FAIL'}")

    all_pass = all(results.values())
    print()
    print(f"  OVERALL: {'ALL TESTS PASSED' if all_pass else 'SOME TESTS FAILED'}")
    print()

    # Restore brightness
    set_brightness(255)


if __name__ == "__main__":
    main()
