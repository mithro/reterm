# MQTT Sensor Publisher for reTerminal

**Date:** 2026-02-18
**Status:** Approved

## Overview

A systemd service (`mqtt-sensors`) on each reTerminal that reads local sensors
and system state, then publishes to an MQTT broker with Home Assistant
auto-discovery. This gives HA visibility into the reTerminal's environment and
health without modifying the existing backlight-manager or power-button-handler.

## Architecture

```
reTerminal                          Network
+---------------------------+       +------------------+
| mqtt-sensors service      |       | Mosquitto (MQTT) |
|  - reads sysfs sensors    | ----> | on HA server     |
|  - reads /dev/input       |       +--------+---------+
|  - publishes MQTT         |                |
+---------------------------+       +--------+---------+
| backlight-manager (no change)     | Home Assistant   |
| power-button-handler (no change)  | auto-discovers   |
+---------------------------+       | entities via     |
                                    | MQTT discovery   |
                                    +------------------+
```

- **Single Python script**: `kiosk/mqtt-sensors`
- **Dependency**: `python3-paho-mqtt` (Debian package, installed in image)
- **Systemd unit**: `kiosk/mqtt-sensors.service`
- **MQTT broker**: `ha.monarto.mithis.com:1883`, anonymous access
- **Hostname-based**: reads `/etc/hostname` to derive entity IDs (reterm1/reterm2)

## HA Device Groups

Two logical devices per reTerminal, providing clean grouping in the HA UI.

### Environment Device

Identifier: `reterm{N}_environment`
Manufacturer: Seeed Studio
Model: reTerminal CM4

| Entity ID | Type | Unit | Source | Poll |
|-----------|------|------|--------|------|
| `sensor.reterm{N}_illuminance` | sensor | lux | IIO `iio:device0/in_illuminance_input` | 2s |

### System Device

Identifier: `reterm{N}_system`
Manufacturer: Seeed Studio
Model: reTerminal CM4

| Entity ID | Type | Unit | Source | Poll |
|-----------|------|------|--------|------|
| `sensor.reterm{N}_cpu_temperature` | sensor | C | `thermal_zone0/temp` | 10s |
| `sensor.reterm{N}_uptime` | sensor | s | `/proc/uptime` | 60s |
| `sensor.reterm{N}_wifi_rssi` | sensor | dBm | `/proc/net/wireless` | 10s |
| `sensor.reterm{N}_backlight` | sensor | - | `backlight/1-0045/brightness` | 2s |
| `binary_sensor.reterm{N}_screen` | binary_sensor | on/off | brightness > 0 | 2s |
| `sensor.reterm{N}_accel_x` | sensor | mg | `lis3lv02d/position` | 2s |
| `sensor.reterm{N}_accel_y` | sensor | mg | `lis3lv02d/position` | 2s |
| `sensor.reterm{N}_accel_z` | sensor | mg | `lis3lv02d/position` | 2s |
| `sensor.reterm{N}_orientation` | sensor | text | derived from accel | 2s |
| `sensor.reterm{N}_tilt_angle` | sensor | degrees | derived from accel | 2s |
| `binary_sensor.reterm{N}_vibration` | binary_sensor | on/off | accel delta spike | 2s |
| Button A/S/D/F | device_trigger | event | gpio_keys input device | event |
| Power Button | device_trigger | event | gpio_keys KEY_SLEEP | event |

## MQTT Topics

```
# Auto-discovery (published once on connect, retained)
homeassistant/sensor/reterm{N}_illuminance/config
homeassistant/sensor/reterm{N}_cpu_temperature/config
homeassistant/binary_sensor/reterm{N}_screen/config
# ... etc for each entity

# State (published periodically, JSON payloads)
reterminal/reterm{N}/environment/state
reterminal/reterm{N}/system/state

# Availability (LWT)
reterminal/reterm{N}/availability  -> "online" / "offline"

# Button events (published on press)
reterminal/reterm{N}/button/event
```

### Example State Payloads

Environment:
```json
{
  "illuminance": 142
}
```

System:
```json
{
  "cpu_temperature": 45.2,
  "uptime": 86400,
  "wifi_rssi": -52,
  "backlight": 200,
  "screen": "ON",
  "accel_x": -18,
  "accel_y": 0,
  "accel_z": -1152,
  "orientation": "flat",
  "tilt_angle": 1.4,
  "vibration": "OFF"
}
```

## Sensor Details

### Light Sensor (LTR-303ALS)

- Path: `/sys/bus/iio/devices/iio:device0/in_illuminance_input`
- Value: integer lux
- Also available: `in_intensity_ir_raw`, `in_intensity_both_raw` (IR and combined channels)

### Accelerometer (LIS331DLH)

- Path: `/sys/devices/platform/lis3lv02d/position`
- Format: `(x,y,z)` in milli-g (needs parsing)
- Rate: 50 Hz (we poll at 2s intervals)

**Derived values:**

- **Orientation**: computed from dominant axis
  - flat: |z| > |x| and |z| > |y| and z < 0
  - inverted: |z| > |x| and |z| > |y| and z > 0
  - tilted: otherwise (with direction derivable from x/y)
- **Tilt angle**: `acos(|z| / sqrt(x^2 + y^2 + z^2))` in degrees
- **Vibration**: magnitude of (current - previous) reading exceeds threshold

### CPU Temperature

- Path: `/sys/class/thermal/thermal_zone0/temp`
- Value: integer in millidegrees C (divide by 1000)

### WiFi RSSI

- Path: `/proc/net/wireless`
- Parse the `wlan0` line for signal level (dBm)

### Buttons

- Source: `/dev/input/eventN` (auto-detected via `gpio_keys` name)
- Events: EV_KEY press (value=1) for keycodes 30-33 (A/S/D/F) and 142 (power)
- Published as HA device triggers (not sensors)

## Dependencies

Added to image build (`build-image.py`):
- `python3-paho-mqtt` (Debian package)

## Files

| File | Purpose |
|------|---------|
| `kiosk/mqtt-sensors` | Main Python script |
| `kiosk/mqtt-sensors.service` | systemd unit file |
| `build-image.py` | Add `python3-paho-mqtt` to package list |

## Error Handling

- MQTT disconnect: auto-reconnect with exponential backoff (paho built-in)
- Sensor read failure: skip that reading, don't crash
- LWT: broker publishes `offline` if service disconnects unexpectedly
- Service restarts: `Restart=on-failure` with `RestartSec=5`
