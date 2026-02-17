# MQTT Sensor Publisher Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create a systemd service that reads reTerminal sensors and publishes them to Home Assistant via MQTT auto-discovery.

**Architecture:** A single Python script (`kiosk/mqtt-sensors`) reads sysfs sensors, accelerometer, input events, and system stats, then publishes JSON payloads to Mosquitto. HA auto-discovers entities via retained config messages on `homeassistant/+/reterm{N}_*/config` topics. Two HA device groups: environment (light) and system (CPU temp, WiFi, accel, buttons, backlight).

**Tech Stack:** Python 3.11+, paho-mqtt 2.1.0 (Debian `python3-paho-mqtt`), systemd, sysfs/procfs, Linux input subsystem.

**Design doc:** `docs/plans/2026-02-18-mqtt-sensors-design.md`

---

## Codebase Context

**Existing patterns to follow:**
- Kiosk scripts are plain Python files (no `.py` extension) in `kiosk/`, installed to `/usr/local/bin/` with mode 0o755
- Service files in `kiosk/` are installed to `/etc/systemd/system/` with mode 0o644
- Scripts use `#!/usr/bin/env python3`, stdlib only (this service adds paho-mqtt as the first external dep)
- `build-image.py:697` has the `packages` list; `build-image.py:377` has the `file_map` for kiosk files; `build-image.py:494` has the `enable` list for services
- Signal handling pattern: `signal.signal(SIGTERM/SIGINT, handler)` with `running = True` flag
- Button device found via `glob.glob("/sys/class/input/event*/device/name")` checking for `"gpio_keys"`
- Accelerometer at `/sys/devices/platform/lis3lv02d/position`, format `(x,y,z)` in milli-g
- Light sensor at `/sys/bus/iio/devices/iio:device0/in_illuminance_input`

**paho-mqtt 2.x API (on device, NOT 1.x):**
```python
import paho.mqtt.client as mqtt
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="reterm1")
client.will_set("reterminal/reterm1/availability", "offline", retain=True)
client.on_connect = lambda client, userdata, flags, rc, properties: ...
client.connect("ha.monarto.mithis.com", 1883)
client.loop_start()  # background thread
client.publish(topic, payload, retain=True)
```

**MQTT broker:** `ha.monarto.mithis.com:1883`, anonymous access, no TLS.

**Hostname:** read from `socket.gethostname()` — returns `reterm1` or `reterm2`.

---

### Task 1: Add paho-mqtt to image build

**Files:**
- Modify: `build-image.py:697-700` (packages list)

**Step 1: Add python3-paho-mqtt to packages list**

In `build-image.py`, find the `packages` list at line 697:
```python
    packages = [
        "cage", "chromium", "wlr-randr", "seatd", "fonts-noto-color-emoji",
        "git", "build-essential", "dkms", "linux-headers-rpi-v8",
    ]
```

Change to:
```python
    packages = [
        "cage", "chromium", "wlr-randr", "seatd", "fonts-noto-color-emoji",
        "git", "build-essential", "dkms", "linux-headers-rpi-v8",
        "python3-paho-mqtt",
    ]
```

**Step 2: Commit**

```bash
git add build-image.py
git commit -m "Add python3-paho-mqtt to image packages"
```

---

### Task 2: Create the systemd service file

**Files:**
- Create: `kiosk/mqtt-sensors.service`

**Step 1: Write the service file**

Create `kiosk/mqtt-sensors.service`:
```ini
[Unit]
Description=MQTT sensor publisher for Home Assistant
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/mqtt-sensors
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

This follows the exact pattern of `backlight-manager.service` and `power-button-handler.service`.

**Step 2: Commit**

```bash
git add kiosk/mqtt-sensors.service
git commit -m "Add systemd service file for mqtt-sensors"
```

---

### Task 3: Register mqtt-sensors in build-image.py

**Files:**
- Modify: `build-image.py:377-385` (file_map)
- Modify: `build-image.py:494-499` (enable list)

**Step 1: Add to file_map**

In `build-image.py`, find the `file_map` dict at line 377:
```python
    file_map = {
        # (source, target_dir, target_name, mode)
        "cage-kiosk@.service": ("/etc/systemd/system/", None, 0o644),
        "kiosk-browser.template": ("/usr/local/bin/", "kiosk-browser", 0o755),
        "backlight-manager": ("/usr/local/bin/", None, 0o755),
        "backlight-manager.service": ("/etc/systemd/system/", None, 0o644),
        "power-button-handler": ("/usr/local/bin/", None, 0o755),
        "power-button-handler.service": ("/etc/systemd/system/", None, 0o644),
    }
```

Add the two new entries:
```python
    file_map = {
        # (source, target_dir, target_name, mode)
        "cage-kiosk@.service": ("/etc/systemd/system/", None, 0o644),
        "kiosk-browser.template": ("/usr/local/bin/", "kiosk-browser", 0o755),
        "backlight-manager": ("/usr/local/bin/", None, 0o755),
        "backlight-manager.service": ("/etc/systemd/system/", None, 0o644),
        "power-button-handler": ("/usr/local/bin/", None, 0o755),
        "power-button-handler.service": ("/etc/systemd/system/", None, 0o644),
        "mqtt-sensors": ("/usr/local/bin/", None, 0o755),
        "mqtt-sensors.service": ("/etc/systemd/system/", None, 0o644),
    }
```

**Step 2: Add to enable list**

Find the `enable` list at line 494:
```python
    enable = [
        "cage-kiosk@tty7",
        "backlight-manager",
        "power-button-handler",
        "seatd",
    ]
```

Add `"mqtt-sensors"`:
```python
    enable = [
        "cage-kiosk@tty7",
        "backlight-manager",
        "power-button-handler",
        "mqtt-sensors",
        "seatd",
    ]
```

**Step 3: Commit**

```bash
git add build-image.py
git commit -m "Register mqtt-sensors in image build pipeline"
```

---

### Task 4: Create mqtt-sensors script — sensor readers

**Files:**
- Create: `kiosk/mqtt-sensors`

This task creates the script with all sensor reading functions but NO MQTT yet — just the data collection layer. We'll add MQTT in Task 5.

**Step 1: Create the script with sensor reader functions**

Create `kiosk/mqtt-sensors`:
```python
#!/usr/bin/env python3
"""MQTT sensor publisher for reTerminal.

Reads sensors (light, accelerometer, CPU temp, WiFi, backlight, buttons)
and publishes to Home Assistant via MQTT auto-discovery.
"""
import glob
import json
import math
import os
import re
import select
import signal
import socket
import struct
import sys
import time

# --- Hardware paths ---
LIGHT_SENSOR = "/sys/bus/iio/devices/iio:device0/in_illuminance_input"
ACCEL_POSITION = "/sys/devices/platform/lis3lv02d/position"
CPU_TEMP = "/sys/class/thermal/thermal_zone0/temp"
BACKLIGHT = "/sys/class/backlight/1-0045/brightness"
PROC_WIRELESS = "/proc/net/wireless"

# --- Linux input event format (arm64) ---
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 0x01

# Button keycodes
KEY_MAP = {
    30: "a",
    31: "s",
    32: "d",
    33: "f",
    142: "power",
}

# --- Vibration detection ---
VIBRATION_THRESHOLD_MG = 100  # milli-g change between readings
VIBRATION_HOLD_S = 2.0  # how long vibration stays "on" after detected

# --- MQTT config ---
MQTT_BROKER = "ha.monarto.mithis.com"
MQTT_PORT = 1883


def read_sysfs(path):
    """Read a sysfs file, return stripped string or None on error."""
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def read_illuminance():
    """Read ambient light in lux. Returns int or None."""
    val = read_sysfs(LIGHT_SENSOR)
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        return None


def read_cpu_temperature():
    """Read CPU temperature in degrees C. Returns float or None."""
    val = read_sysfs(CPU_TEMP)
    if val is None:
        return None
    try:
        return round(int(val) / 1000.0, 1)
    except ValueError:
        return None


def read_uptime():
    """Read system uptime in seconds. Returns int or None."""
    val = read_sysfs("/proc/uptime")
    if val is None:
        return None
    try:
        return int(float(val.split()[0]))
    except (ValueError, IndexError):
        return None


def read_wifi_rssi():
    """Read WiFi signal strength in dBm. Returns int or None."""
    val = read_sysfs(PROC_WIRELESS)
    if val is None:
        return None
    for line in val.splitlines():
        if "wlan0" in line:
            # Format: "wlan0: 0000  -52.  -256.  ..."
            # Signal level is the 3rd field (after interface and status)
            match = re.search(r"wlan0:\s+\S+\s+(-?\d+)\.", line)
            if match:
                return int(match.group(1))
    return None


def read_backlight():
    """Read current backlight brightness (0-255). Returns int or None."""
    val = read_sysfs(BACKLIGHT)
    if val is None:
        return None
    try:
        return int(val)
    except ValueError:
        return None


def read_accelerometer():
    """Read accelerometer (x, y, z) in milli-g. Returns tuple or None."""
    val = read_sysfs(ACCEL_POSITION)
    if val is None:
        return None
    # Format: "(x,y,z)" e.g. "(-18,0,-1152)"
    match = re.match(r"\((-?\d+),(-?\d+),(-?\d+)\)", val)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def compute_orientation(x, y, z):
    """Derive orientation string from accelerometer milli-g values."""
    ax, ay, az = abs(x), abs(y), abs(z)
    if az > ax and az > ay:
        return "flat" if z < 0 else "inverted"
    if ax > ay:
        return "left" if x < 0 else "right"
    return "up" if y < 0 else "down"


def compute_tilt_angle(x, y, z):
    """Compute tilt angle from vertical in degrees."""
    magnitude = math.sqrt(x * x + y * y + z * z)
    if magnitude == 0:
        return 0.0
    # z points down when flat, so tilt = angle from z-axis
    cos_angle = abs(z) / magnitude
    cos_angle = min(1.0, max(-1.0, cos_angle))  # clamp for acos safety
    return round(math.degrees(math.acos(cos_angle)), 1)


def detect_vibration(prev_accel, curr_accel):
    """Return True if acceleration change exceeds threshold."""
    if prev_accel is None or curr_accel is None:
        return False
    dx = curr_accel[0] - prev_accel[0]
    dy = curr_accel[1] - prev_accel[1]
    dz = curr_accel[2] - prev_accel[2]
    delta = math.sqrt(dx * dx + dy * dy + dz * dz)
    return delta > VIBRATION_THRESHOLD_MG


def find_gpio_keys_device():
    """Find the input event device for gpio_keys by name."""
    for name_path in glob.glob("/sys/class/input/event*/device/name"):
        try:
            with open(name_path) as f:
                if f.read().strip() == "gpio_keys":
                    return "/dev/input/" + name_path.split("/")[4]
        except OSError:
            continue
    return None
```

**Step 2: Verify script is syntactically valid**

Run: `python3 -c "import ast; ast.parse(open('kiosk/mqtt-sensors').read()); print('OK')"` from the repo root.
Expected: `OK`

**Step 3: Commit**

```bash
git add kiosk/mqtt-sensors
git commit -m "Add mqtt-sensors sensor reader functions"
```

---

### Task 5: Add MQTT connection and HA auto-discovery

**Files:**
- Modify: `kiosk/mqtt-sensors` (append MQTT logic)

**Step 1: Add the MQTT discovery and publishing functions**

Append to `kiosk/mqtt-sensors`, after the `find_gpio_keys_device()` function:

```python
def get_hostname():
    """Return short hostname (e.g. 'reterm1')."""
    return socket.gethostname().split(".")[0]


def make_device_info(hostname, group):
    """Build HA MQTT device info dict."""
    return {
        "identifiers": [f"{hostname}_{group}"],
        "name": f"{hostname} {group}",
        "manufacturer": "Seeed Studio",
        "model": "reTerminal CM4",
    }


def discovery_configs(hostname):
    """Generate (topic, payload) tuples for HA MQTT auto-discovery."""
    avail = {"topic": f"reterminal/{hostname}/availability"}
    env_dev = make_device_info(hostname, "environment")
    sys_dev = make_device_info(hostname, "system")
    env_state = f"reterminal/{hostname}/environment/state"
    sys_state = f"reterminal/{hostname}/system/state"

    configs = []

    # --- Environment sensors ---
    configs.append((
        f"homeassistant/sensor/{hostname}_illuminance/config",
        {
            "name": "Illuminance",
            "unique_id": f"{hostname}_illuminance",
            "device_class": "illuminance",
            "unit_of_measurement": "lx",
            "state_topic": env_state,
            "value_template": "{{ value_json.illuminance }}",
            "availability": avail,
            "device": env_dev,
        },
    ))

    # --- System sensors ---
    configs.append((
        f"homeassistant/sensor/{hostname}_cpu_temperature/config",
        {
            "name": "CPU Temperature",
            "unique_id": f"{hostname}_cpu_temperature",
            "device_class": "temperature",
            "unit_of_measurement": "\u00b0C",
            "state_topic": sys_state,
            "value_template": "{{ value_json.cpu_temperature }}",
            "availability": avail,
            "device": sys_dev,
        },
    ))

    configs.append((
        f"homeassistant/sensor/{hostname}_uptime/config",
        {
            "name": "Uptime",
            "unique_id": f"{hostname}_uptime",
            "device_class": "duration",
            "unit_of_measurement": "s",
            "state_topic": sys_state,
            "value_template": "{{ value_json.uptime }}",
            "availability": avail,
            "device": sys_dev,
        },
    ))

    configs.append((
        f"homeassistant/sensor/{hostname}_wifi_rssi/config",
        {
            "name": "WiFi RSSI",
            "unique_id": f"{hostname}_wifi_rssi",
            "device_class": "signal_strength",
            "unit_of_measurement": "dBm",
            "state_topic": sys_state,
            "value_template": "{{ value_json.wifi_rssi }}",
            "availability": avail,
            "device": sys_dev,
        },
    ))

    configs.append((
        f"homeassistant/sensor/{hostname}_backlight/config",
        {
            "name": "Backlight",
            "unique_id": f"{hostname}_backlight",
            "icon": "mdi:brightness-6",
            "state_topic": sys_state,
            "value_template": "{{ value_json.backlight }}",
            "availability": avail,
            "device": sys_dev,
        },
    ))

    configs.append((
        f"homeassistant/binary_sensor/{hostname}_screen/config",
        {
            "name": "Screen",
            "unique_id": f"{hostname}_screen",
            "device_class": "power",
            "state_topic": sys_state,
            "value_template": "{{ value_json.screen }}",
            "payload_on": "ON",
            "payload_off": "OFF",
            "availability": avail,
            "device": sys_dev,
        },
    ))

    # Accelerometer raw
    for axis in ("x", "y", "z"):
        configs.append((
            f"homeassistant/sensor/{hostname}_accel_{axis}/config",
            {
                "name": f"Accelerometer {axis.upper()}",
                "unique_id": f"{hostname}_accel_{axis}",
                "unit_of_measurement": "mg",
                "icon": "mdi:axis-arrow",
                "state_topic": sys_state,
                "value_template": f"{{{{ value_json.accel_{axis} }}}}",
                "availability": avail,
                "device": sys_dev,
                "entity_category": "diagnostic",
            },
        ))

    configs.append((
        f"homeassistant/sensor/{hostname}_orientation/config",
        {
            "name": "Orientation",
            "unique_id": f"{hostname}_orientation",
            "icon": "mdi:screen-rotation",
            "state_topic": sys_state,
            "value_template": "{{ value_json.orientation }}",
            "availability": avail,
            "device": sys_dev,
        },
    ))

    configs.append((
        f"homeassistant/sensor/{hostname}_tilt_angle/config",
        {
            "name": "Tilt Angle",
            "unique_id": f"{hostname}_tilt_angle",
            "unit_of_measurement": "\u00b0",
            "icon": "mdi:angle-acute",
            "state_topic": sys_state,
            "value_template": "{{ value_json.tilt_angle }}",
            "availability": avail,
            "device": sys_dev,
        },
    ))

    configs.append((
        f"homeassistant/binary_sensor/{hostname}_vibration/config",
        {
            "name": "Vibration",
            "unique_id": f"{hostname}_vibration",
            "device_class": "vibration",
            "state_topic": sys_state,
            "value_template": "{{ value_json.vibration }}",
            "payload_on": "ON",
            "payload_off": "OFF",
            "availability": avail,
            "device": sys_dev,
        },
    ))

    # Button device triggers
    for keycode, key_name in KEY_MAP.items():
        configs.append((
            f"homeassistant/device_automation/{hostname}_button_{key_name}/config",
            {
                "automation_type": "trigger",
                "type": "button_short_press",
                "subtype": f"button_{key_name}",
                "topic": f"reterminal/{hostname}/button/event",
                "payload": key_name,
                "device": sys_dev,
            },
        ))

    return configs
```

**Step 2: Verify syntax**

Run: `python3 -c "import ast; ast.parse(open('kiosk/mqtt-sensors').read()); print('OK')"` from the repo root.
Expected: `OK`

**Step 3: Commit**

```bash
git add kiosk/mqtt-sensors
git commit -m "Add MQTT discovery config generation"
```

---

### Task 6: Add main loop with MQTT publishing

**Files:**
- Modify: `kiosk/mqtt-sensors` (append main function)

**Step 1: Add the main function**

Append to `kiosk/mqtt-sensors`:

```python
def main():
    import paho.mqtt.client as mqtt

    hostname = get_hostname()
    avail_topic = f"reterminal/{hostname}/availability"
    env_topic = f"reterminal/{hostname}/environment/state"
    sys_topic = f"reterminal/{hostname}/system/state"
    button_topic = f"reterminal/{hostname}/button/event"

    print(f"MQTT sensor publisher starting for {hostname}", file=sys.stderr)

    # --- MQTT setup ---
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=hostname)
    client.will_set(avail_topic, "offline", qos=1, retain=True)

    connected = False

    def on_connect(client, userdata, flags, rc, properties):
        nonlocal connected
        if rc == 0:
            connected = True
            print(f"Connected to MQTT broker {MQTT_BROKER}", file=sys.stderr)
            # Publish discovery configs (retained)
            for topic, payload in discovery_configs(hostname):
                client.publish(topic, json.dumps(payload), qos=1, retain=True)
            # Announce online
            client.publish(avail_topic, "online", qos=1, retain=True)
            sys.stderr.flush()
        else:
            print(f"MQTT connect failed: rc={rc}", file=sys.stderr)
            sys.stderr.flush()

    def on_disconnect(client, userdata, flags, rc, properties):
        nonlocal connected
        connected = False
        print(f"MQTT disconnected: rc={rc}", file=sys.stderr)
        sys.stderr.flush()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.connect_async(MQTT_BROKER, MQTT_PORT)
    client.loop_start()

    # --- Signal handling ---
    running = True

    def handle_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # --- Button device ---
    button_path = find_gpio_keys_device()
    button_fd = None
    if button_path:
        try:
            button_fd = os.open(button_path, os.O_RDONLY | os.O_NONBLOCK)
            print(f"  Buttons: {button_path}", file=sys.stderr)
        except OSError as e:
            print(f"  Buttons: failed to open {button_path}: {e}", file=sys.stderr)
    else:
        print("  Buttons: gpio_keys not found", file=sys.stderr)
    sys.stderr.flush()

    # --- State ---
    prev_accel = None
    vibration_until = 0.0
    last_env_publish = 0.0
    last_sys_slow_publish = 0.0  # for uptime (60s)
    last_sys_fast_publish = 0.0  # for accel/backlight (2s)
    last_sys_mid_publish = 0.0   # for CPU temp/WiFi (10s)

    ENV_INTERVAL = 2.0
    SYS_FAST_INTERVAL = 2.0
    SYS_MID_INTERVAL = 10.0
    SYS_SLOW_INTERVAL = 60.0

    # Cache for system state (merge fast/mid/slow readings)
    sys_state = {}

    try:
        while running:
            now = time.monotonic()

            # --- Drain button events ---
            if button_fd is not None:
                try:
                    readable, _, _ = select.select([button_fd], [], [], 0)
                    if readable:
                        while True:
                            try:
                                data = os.read(button_fd, EVENT_SIZE)
                                if len(data) < EVENT_SIZE:
                                    break
                                _, _, ev_type, ev_code, ev_value = struct.unpack(
                                    EVENT_FORMAT, data
                                )
                                if ev_type == EV_KEY and ev_value == 1:
                                    key_name = KEY_MAP.get(ev_code)
                                    if key_name and connected:
                                        client.publish(button_topic, key_name)
                                        print(
                                            f"Button: {key_name} (code={ev_code})",
                                            file=sys.stderr,
                                        )
                                        sys.stderr.flush()
                            except BlockingIOError:
                                break
                except (OSError, ValueError):
                    pass

            # --- Environment (2s) ---
            if now - last_env_publish >= ENV_INTERVAL:
                last_env_publish = now
                lux = read_illuminance()
                if lux is not None and connected:
                    client.publish(env_topic, json.dumps({"illuminance": lux}))

            # --- System fast: accel + backlight (2s) ---
            if now - last_sys_fast_publish >= SYS_FAST_INTERVAL:
                last_sys_fast_publish = now

                backlight = read_backlight()
                if backlight is not None:
                    sys_state["backlight"] = backlight
                    sys_state["screen"] = "ON" if backlight > 0 else "OFF"

                accel = read_accelerometer()
                if accel is not None:
                    sys_state["accel_x"] = accel[0]
                    sys_state["accel_y"] = accel[1]
                    sys_state["accel_z"] = accel[2]
                    sys_state["orientation"] = compute_orientation(*accel)
                    sys_state["tilt_angle"] = compute_tilt_angle(*accel)

                    if detect_vibration(prev_accel, accel):
                        vibration_until = now + VIBRATION_HOLD_S
                    prev_accel = accel

                sys_state["vibration"] = "ON" if now < vibration_until else "OFF"

            # --- System mid: CPU temp + WiFi (10s) ---
            if now - last_sys_mid_publish >= SYS_MID_INTERVAL:
                last_sys_mid_publish = now

                cpu_temp = read_cpu_temperature()
                if cpu_temp is not None:
                    sys_state["cpu_temperature"] = cpu_temp

                rssi = read_wifi_rssi()
                if rssi is not None:
                    sys_state["wifi_rssi"] = rssi

            # --- System slow: uptime (60s) ---
            if now - last_sys_slow_publish >= SYS_SLOW_INTERVAL:
                last_sys_slow_publish = now

                uptime = read_uptime()
                if uptime is not None:
                    sys_state["uptime"] = uptime

            # --- Publish merged system state on fastest interval ---
            if (now - last_sys_fast_publish < 0.1) and sys_state and connected:
                client.publish(sys_topic, json.dumps(sys_state))

            # --- Sleep until next poll ---
            if button_fd is not None:
                select.select([button_fd], [], [], SYS_FAST_INTERVAL)
            else:
                time.sleep(SYS_FAST_INTERVAL)

    finally:
        if connected:
            client.publish(avail_topic, "offline", qos=1, retain=True)
            time.sleep(0.5)  # let the message send
        client.loop_stop()
        client.disconnect()
        if button_fd is not None:
            os.close(button_fd)
        print("MQTT sensor publisher stopped", file=sys.stderr)


if __name__ == "__main__":
    main()
```

**Step 2: Verify syntax**

Run: `python3 -c "import ast; ast.parse(open('kiosk/mqtt-sensors').read()); print('OK')"` from the repo root.
Expected: `OK`

**Step 3: Commit**

```bash
git add kiosk/mqtt-sensors
git commit -m "Add main loop with MQTT publishing"
```

---

### Task 7: Deploy and test on reterm2

This task deploys mqtt-sensors to the running reterm2 device for live testing, without rebuilding the full image.

**Step 1: Install paho-mqtt on the device**

```bash
ssh tim@10.2.90.177 'sudo apt-get update && sudo apt-get install -y python3-paho-mqtt'
```

**Step 2: Copy the script and service file**

```bash
scp kiosk/mqtt-sensors tim@10.2.90.177:/tmp/mqtt-sensors
scp kiosk/mqtt-sensors.service tim@10.2.90.177:/tmp/mqtt-sensors.service
ssh tim@10.2.90.177 'sudo cp /tmp/mqtt-sensors /usr/local/bin/mqtt-sensors && sudo chmod 755 /usr/local/bin/mqtt-sensors'
ssh tim@10.2.90.177 'sudo cp /tmp/mqtt-sensors.service /etc/systemd/system/mqtt-sensors.service'
ssh tim@10.2.90.177 'rm /tmp/mqtt-sensors /tmp/mqtt-sensors.service'
```

**Step 3: Start the service**

```bash
ssh tim@10.2.90.177 'sudo systemctl daemon-reload && sudo systemctl enable mqtt-sensors && sudo systemctl start mqtt-sensors'
```

**Step 4: Verify the service is running**

```bash
ssh tim@10.2.90.177 'systemctl status mqtt-sensors --no-pager -l'
```

Expected: `active (running)`, logs showing "Connected to MQTT broker" and sensor readings.

**Step 5: Verify MQTT messages**

From a machine with mosquitto-clients:
```bash
mosquitto_sub -h ha.monarto.mithis.com -t 'reterminal/reterm2/#' -v
```

Expected: periodic JSON payloads on `environment/state` and `system/state` topics, plus discovery configs on `homeassistant/+/reterm2_*` topics.

**Step 6: Verify HA auto-discovery**

Check Home Assistant UI:
- Navigate to Settings > Devices & Services > MQTT
- Look for "reterm2 environment" and "reterm2 system" devices
- Verify entities appear with correct values

**Step 7: Commit any fixes discovered during testing**

If any changes were needed, commit them:
```bash
git add kiosk/mqtt-sensors
git commit -m "Fix mqtt-sensors issues found during live testing"
```

---

### Task 8: Deploy to reterm1

Repeat the deployment steps from Task 7 for reterm1 at `10.2.90.175`:

```bash
ssh tim@10.2.90.175 'sudo apt-get update && sudo apt-get install -y python3-paho-mqtt'
scp kiosk/mqtt-sensors tim@10.2.90.175:/tmp/mqtt-sensors
scp kiosk/mqtt-sensors.service tim@10.2.90.175:/tmp/mqtt-sensors.service
ssh tim@10.2.90.175 'sudo cp /tmp/mqtt-sensors /usr/local/bin/mqtt-sensors && sudo chmod 755 /usr/local/bin/mqtt-sensors'
ssh tim@10.2.90.175 'sudo cp /tmp/mqtt-sensors.service /etc/systemd/system/mqtt-sensors.service'
ssh tim@10.2.90.175 'rm /tmp/mqtt-sensors /tmp/mqtt-sensors.service'
ssh tim@10.2.90.175 'sudo systemctl daemon-reload && sudo systemctl enable mqtt-sensors && sudo systemctl start mqtt-sensors'
ssh tim@10.2.90.175 'systemctl status mqtt-sensors --no-pager -l'
```

Verify both `reterm1 environment` and `reterm1 system` appear in HA.

---

### Task 9: Push all changes

```bash
git push
```

Verify the CI build triggers and completes successfully.
