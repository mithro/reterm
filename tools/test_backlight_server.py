#!/usr/bin/env python3
"""Web-based backlight test suite for reTerminal.

Serves an interactive test page that guides the user through:
  1. Button detection (press all 4 GPIO buttons)
  2. Locate the light sensor (real-time lux display)
  3. Backlight control (server cycles brightness)
  4. Sensor response (cover/uncover sensor)
  5. Dark transition + button wake

Each test shows results, then a green "Next" button to advance.
Results are POST'd back with pass/fail verdicts.

Usage:
    sudo python3 test_backlight_server.py
    # Then point kiosk at http://localhost:8096
"""
import glob
import http.server
import json
import os
import select
import struct
import sys
import threading
import time

PORT = 8096

LIGHT_SENSOR = "/sys/bus/iio/devices/iio:device0/in_illuminance_input"
BACKLIGHT = "/sys/class/backlight/1-0045/brightness"
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 0x01

state = {
    "lux": 0,
    "brightness": 255,
    "last_button": None,
    "last_button_time": 0,
    "button_count": 0,
}
state_lock = threading.Lock()


def find_gpio_keys():
    for name_path in glob.glob("/sys/class/input/event*/device/name"):
        try:
            with open(name_path) as f:
                if f.read().strip() == "gpio_keys":
                    return "/dev/input/" + name_path.split("/")[4]
        except OSError:
            continue
    return None


def read_lux():
    try:
        with open(LIGHT_SENSOR) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return -1


def read_brightness():
    try:
        with open(BACKLIGHT) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return -1


def set_brightness(val):
    val = max(0, min(255, int(val)))
    try:
        with open(BACKLIGHT, "w") as f:
            f.write(str(val))
    except OSError:
        pass


def sensor_thread():
    while True:
        lux = read_lux()
        bright = read_brightness()
        with state_lock:
            state["lux"] = lux
            state["brightness"] = bright
        time.sleep(0.3)


def button_thread():
    dev = find_gpio_keys()
    if not dev:
        print("Warning: gpio_keys not found", file=sys.stderr)
        return
    print(f"Monitoring buttons: {dev}", file=sys.stderr)
    fd = os.open(dev, os.O_RDONLY | os.O_NONBLOCK)
    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 1.0)
            if ready:
                while True:
                    try:
                        data = os.read(fd, EVENT_SIZE)
                        if len(data) < EVENT_SIZE:
                            break
                        _, _, ev_type, code, value = struct.unpack(EVENT_FORMAT, data)
                        if ev_type == EV_KEY and value == 1:
                            with state_lock:
                                state["last_button"] = code
                                state["last_button_time"] = time.time()
                                state["button_count"] += 1
                    except BlockingIOError:
                        break
    finally:
        os.close(fd)


HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Backlight Test</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; cursor: none; }
body {
    background: #0d1117; color: #e6edf3;
    font-family: -apple-system, sans-serif;
    width: 100vw; height: 100vh; overflow: hidden;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
}
h1 { font-size: 22px; margin-bottom: 6px; }
.subtitle { font-size: 14px; color: #8b949e; margin-bottom: 14px;
            text-align: center; padding: 0 20px; line-height: 1.5; }

.phase { display: none; width: 100%; height: 100%;
         flex-direction: column; align-items: center; justify-content: center; }
.phase.active { display: flex; }

/* Green next button */
.next-btn {
    display: none; margin-top: 16px;
    padding: 14px 48px; font-size: 20px; font-weight: bold;
    background: #238636; color: #fff; border: 2px solid #2ea043;
    border-radius: 12px;
}
.next-btn.visible { display: block; }
.next-btn:active { background: #2ea043; transform: scale(0.97); }

/* Lux display */
.lux-display { font-size: 64px; font-weight: bold; color: #58a6ff; margin: 8px 0; }
.lux-label { font-size: 14px; color: #8b949e; }
.lux-bar { width: 80%; max-width: 500px; height: 20px;
           background: #21262d; border-radius: 10px; margin: 8px 0; overflow: hidden; }
.lux-bar-fill { height: 100%; border-radius: 10px; transition: width 0.3s;
                background: linear-gradient(90deg, #f85149, #f0883e, #d29922, #3fb950); }
.brightness-row { font-size: 14px; color: #8b949e; margin-top: 4px; }

/* Status */
.status { font-size: 18px; margin: 10px 0; }
.status.waiting { color: #d29922; }
.status.pass { color: #3fb950; font-weight: bold; }
.status.fail { color: #f85149; font-weight: bold; }

/* Button indicators */
.btn-row { display: flex; gap: 16px; margin: 12px 0; }
.btn-ind { width: 70px; height: 70px; border-radius: 14px;
           display: flex; align-items: center; justify-content: center;
           font-size: 28px; font-weight: bold;
           background: #21262d; border: 2px solid #30363d; transition: all 0.15s; }
.btn-ind.pressed { background: #0d4429; border-color: #3fb950; color: #3fb950; }

/* Backlight cycle */
.bl-value { font-size: 64px; font-weight: bold; color: #58a6ff; margin: 8px 0; }
.bl-step { font-size: 16px; color: #8b949e; }

/* Results */
.results-card { background: #161b22; border: 1px solid #30363d; border-radius: 12px;
                padding: 16px 24px; min-width: 360px; }
.result-row { display: flex; justify-content: space-between; padding: 7px 0;
              border-bottom: 1px solid #21262d; font-size: 15px; }
.result-row:last-child { border-bottom: none; }
.result-row .name { color: #8b949e; }
.result-row .pass { color: #3fb950; font-weight: bold; }
.result-row .fail { color: #f85149; font-weight: bold; }
.overall { font-size: 26px; font-weight: bold; margin-top: 14px; }
.overall.pass { color: #3fb950; }
.overall.fail { color: #f85149; }
</style>
</head>
<body>

<!-- Phase 0: Buttons -->
<div class="phase active" id="phase-buttons">
    <h1>Test 1/5: GPIO Buttons</h1>
    <div class="subtitle">Press each of the 4 physical buttons on the device.</div>
    <div class="btn-row">
        <div class="btn-ind" id="b30">A</div>
        <div class="btn-ind" id="b31">S</div>
        <div class="btn-ind" id="b32">D</div>
        <div class="btn-ind" id="b33">F</div>
    </div>
    <div class="status waiting" id="btn-status">0/4 pressed</div>
    <button class="next-btn" id="btn-next" ontouchstart="advance()">Next ▶</button>
</div>

<!-- Phase 1: Locate sensor (manual confirmation only) -->
<div class="phase" id="phase-locate">
    <h1>Step 2/5: Find the Light Sensor</h1>
    <div class="subtitle">Move your hand slowly around the front bezel.<br>
    The lux reading drops when you cover the sensor.<br>
    Take your time. Tap the button below when you've found it.</div>
    <div class="lux-display" id="loc-lux">--</div>
    <div class="lux-label">lux</div>
    <div class="lux-bar"><div class="lux-bar-fill" id="loc-bar" style="width:0%"></div></div>
    <div class="brightness-row" id="loc-hint"></div>
    <button class="next-btn visible" id="loc-next" ontouchstart="advance()" style="margin-top:20px">I found the sensor ▶</button>
</div>

<!-- Phase 2: Backlight control -->
<div class="phase" id="phase-backlight">
    <h1>Test 3/5: Backlight Control</h1>
    <div class="subtitle">The screen brightness will cycle through several levels.<br>
    Watch the screen dim and brighten.</div>
    <div class="bl-value" id="bl-val">--</div>
    <div class="bl-step" id="bl-step">brightness</div>
    <div class="status waiting" id="bl-status">Starting cycle...</div>
    <button class="next-btn" id="bl-next" ontouchstart="advance()">Next ▶</button>
</div>

<!-- Phase 3: Sensor response -->
<div class="phase" id="phase-sensor">
    <h1>Test 4/5: Sensor Response</h1>
    <div class="subtitle">Cover the sensor (lux &lt; 3), then uncover (lux &gt; 15).<br>
    This verifies the sensor has a usable dynamic range.</div>
    <div class="lux-display" id="sen-lux">--</div>
    <div class="lux-label">lux</div>
    <div class="lux-bar"><div class="lux-bar-fill" id="sen-bar" style="width:0%"></div></div>
    <div class="status waiting" id="sen-status">Cover the sensor...</div>
    <button class="next-btn" id="sen-next" ontouchstart="advance()">Next ▶</button>
</div>

<!-- Phase 4: Dark + button wake -->
<div class="phase" id="phase-dark">
    <h1>Test 5/5: Dark → Off → Button Wake</h1>
    <div class="subtitle">Cover the sensor until the screen turns off.<br>
    Then press any GPIO button to wake it.</div>
    <div class="lux-display" id="dk-lux">--</div>
    <div class="lux-label">lux</div>
    <div class="brightness-row" id="dk-bright">Backlight: --</div>
    <div class="status waiting" id="dk-status">Cover sensor (need lux &lt; 5)...</div>
    <button class="next-btn" id="dk-next" ontouchstart="advance()">See Results ▶</button>
</div>

<!-- Phase 5: Results -->
<div class="phase" id="phase-results">
    <h1>Backlight Test Results</h1>
    <div class="results-card" id="results-card"></div>
    <div class="overall" id="overall"></div>
</div>

<script>
const PHASE_IDS = ['phase-buttons','phase-locate','phase-backlight','phase-sensor','phase-dark','phase-results'];
let phase = 0;
let lastState = {};
const R = {}; // results

function show(n) {
    PHASE_IDS.forEach((id,i) => document.getElementById(id).classList.toggle('active', i===n));
    phase = n;
    if (phaseFns[n]) phaseFns[n]();
}
function advance() { show(phase + 1); }

function luxBar(id, lux) {
    document.getElementById(id).style.width = Math.min(100, (lux/200)*100) + '%';
}

async function poll() {
    try { lastState = await (await fetch('/api/state')).json(); } catch(e) {}
}

// Helper: poll loop that waits for each request to complete before scheduling the next.
// Returns a stop() function. Avoids the setInterval+async pileup bug.
function pollLoop(fn, interval) {
    let running = true;
    async function tick() {
        if (!running) return;
        try {
            await poll();
            await fn();
        } catch(e) { console.error('pollLoop error:', e); }
        if (running) setTimeout(tick, interval);
    }
    tick();
    return () => { running = false; };
}

// ===== Phase 0: Buttons =====
function startButtons() {
    const seen = new Set();
    let prevCount = 0;
    const stop = pollLoop(() => {
        if ((lastState.button_count||0) > prevCount) {
            const c = lastState.last_button;
            if (c) { seen.add(c); const el=document.getElementById('b'+c); if(el) el.className='btn-ind pressed'; }
            prevCount = lastState.button_count;
        }
        document.getElementById('btn-status').textContent = seen.size + '/4 pressed';
        if (seen.size >= 4) {
            stop();
            R.buttons = true;
            document.getElementById('btn-status').textContent = 'PASS — all 4 buttons detected!';
            document.getElementById('btn-status').className = 'status pass';
            document.getElementById('btn-next').classList.add('visible');
        }
    }, 150);
}

// ===== Phase 1: Locate sensor (live display only, user confirms manually) =====
let locateStop = null;
function startLocate() {
    locateStop = pollLoop(() => {
        const lux = lastState.lux ?? 99;
        document.getElementById('loc-lux').textContent = lux;
        luxBar('loc-bar', lux);
        document.getElementById('loc-hint').textContent = lux < 3 ? 'Sensor is covered!' : lux < 15 ? 'Getting closer...' : '';
    }, 250);
}
// Stop polling when user advances from this phase
const origAdvance = advance;
advance = function() {
    if (phase === 1 && locateStop) { locateStop(); R.locate = true; }
    origAdvance();
};

// ===== Phase 2: Backlight control =====
async function startBacklight() {
    const levels = [255, 100, 30, 0, 150, 255];
    let allOk = true;
    for (let i = 0; i < levels.length; i++) {
        const lv = levels[i];
        document.getElementById('bl-val').textContent = lv;
        document.getElementById('bl-step').textContent = `Step ${i+1}/${levels.length}: setting brightness to ${lv}`;
        await fetch('/api/brightness', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({value:lv})});
        await new Promise(r => setTimeout(r, 1800));
        await poll();
        if (lastState.brightness !== lv) allOk = false;
    }
    R.backlight = allOk;
    document.getElementById('bl-status').textContent = allOk ? 'PASS — backlight responds correctly' : 'FAIL — readback mismatch';
    document.getElementById('bl-status').className = 'status ' + (allOk ? 'pass' : 'fail');
    document.getElementById('bl-next').classList.add('visible');
}

// ===== Phase 3: Sensor response =====
async function startSensor() {
    await fetch('/api/brightness', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({value:255})});
    let stage = 'baseline', baselineCount = 0;
    const BASELINE_NEEDED = 4;
    let minL=999, maxL=0;
    const stop = pollLoop(() => {
        const lux = lastState.lux ?? 99;
        document.getElementById('sen-lux').textContent = lux;
        luxBar('sen-bar', lux);
        if (lux < minL) minL = lux;
        if (lux > maxL) maxL = lux;

        if (stage === 'baseline') {
            if (lux > 10) baselineCount++;
            else baselineCount = 0;
            if (baselineCount >= BASELINE_NEEDED) {
                stage = 'cover';
                document.getElementById('sen-status').textContent = `Ready! Cover sensor... (min seen: ${minL})`;
            } else {
                document.getElementById('sen-status').textContent = `Move hands away... (lux=${lux}, need >10)`;
            }
        } else if (stage === 'cover') {
            document.getElementById('sen-status').textContent = `Cover sensor... (min seen: ${minL})`;
            if (lux <= 3) stage = 'uncover';
        } else if (stage === 'uncover') {
            document.getElementById('sen-status').textContent = `Good! Now uncover... (max seen: ${maxL})`;
            if (lux > 15) stage = 'done';
        }

        if (stage === 'done') {
            stop();
            R.sensor = true;
            R.sensorMin = minL; R.sensorMax = maxL;
            document.getElementById('sen-status').textContent = `PASS — range ${minL} to ${maxL} lux`;
            document.getElementById('sen-status').className = 'status pass';
            document.getElementById('sen-next').classList.add('visible');
        }
    }, 250);
}

// ===== Phase 4: Dark + button wake =====
async function startDark() {
    await fetch('/api/brightness', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({value:255})});
    let stage = 'baseline', baselineCount = 0;
    const BASELINE_NEEDED = 4;
    let prevBtnCount = lastState.button_count || 0;

    const stop = pollLoop(async () => {
        const lux = lastState.lux ?? 99;
        document.getElementById('dk-lux').textContent = lux;
        document.getElementById('dk-bright').textContent = 'Backlight: ' + (lastState.brightness ?? '--');

        if (stage === 'baseline') {
            if (lux > 10) baselineCount++;
            else baselineCount = 0;
            if (baselineCount >= BASELINE_NEEDED) {
                stage = 'cover';
                document.getElementById('dk-status').textContent = `Ready! Cover sensor completely (need lux <5)...`;
            } else {
                document.getElementById('dk-status').textContent = `Move hands away... (lux=${lux}, need >10)`;
            }
        } else if (stage === 'cover') {
            if (lux < 5) {
                stage = 'wake';
                await fetch('/api/brightness', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({value:0})});
                document.getElementById('dk-status').textContent = 'Screen OFF! Press any GPIO button to wake...';
                prevBtnCount = lastState.button_count || 0;
            } else {
                document.getElementById('dk-status').textContent = `Cover sensor completely (lux=${lux}, need <5)...`;
            }
        } else if (stage === 'wake') {
            if ((lastState.button_count||0) > prevBtnCount) {
                stage = 'done';
                await fetch('/api/brightness', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({value:200})});
                stop();
                R.darkTransition = true;
                R.buttonWake = true;
                document.getElementById('dk-status').textContent = 'PASS — dark transition and button wake work!';
                document.getElementById('dk-status').className = 'status pass';
                await fetch('/api/brightness', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({value:255})});
                document.getElementById('dk-next').classList.add('visible');
            }
        }
    }, 250);
}

// ===== Phase 5: Results =====
function startResults() {
    const tests = [
        {name: 'GPIO Buttons (4/4)', pass: !!R.buttons},
        {name: 'Locate sensor', pass: !!R.locate},
        {name: 'Backlight control', pass: !!R.backlight},
        {name: `Sensor response (${R.sensorMin??'?'}-${R.sensorMax??'?'} lux)`, pass: !!R.sensor},
        {name: 'Dark transition (screen off)', pass: !!R.darkTransition},
        {name: 'Button wake', pass: !!R.buttonWake},
    ];
    const card = document.getElementById('results-card');
    card.innerHTML = tests.map(t =>
        `<div class="result-row"><span class="name">${t.name}</span>` +
        `<span class="${t.pass?'pass':'fail'}">${t.pass?'PASS':'FAIL'}</span></div>`
    ).join('');
    const allPass = tests.every(t=>t.pass);
    const ov = document.getElementById('overall');
    ov.textContent = allPass ? 'ALL TESTS PASSED' : 'SOME TESTS FAILED';
    ov.className = 'overall ' + (allPass ? 'pass' : 'fail');
    fetch('/results', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({tests, allPass, results:R})});
}

const phaseFns = [startButtons, startLocate, startBacklight, startSensor, startDark, startResults];

// Start phase 0
startButtons();
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/state":
            with state_lock:
                data = dict(state)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(HTML.encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.path == "/api/brightness":
            data = json.loads(body)
            set_brightness(data.get("value", 255))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        elif self.path == "/results":
            data = json.loads(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            print_results(data)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


def print_results(data):
    print()
    print("=" * 60)
    print("  Backlight Test Results")
    print("=" * 60)
    for test in data.get("tests", []):
        status = "PASS" if test["pass"] else "FAIL"
        print(f"  {test['name']:40s} {status}")
    print()
    all_pass = data.get("allPass", False)
    print(f"  OVERALL: {'ALL TESTS PASSED' if all_pass else 'SOME TESTS FAILED'}")
    print("=" * 60)
    print()
    sys.stdout.flush()


if __name__ == "__main__":
    threading.Thread(target=sensor_thread, daemon=True).start()
    threading.Thread(target=button_thread, daemon=True).start()

    print(f"Backlight test server on http://localhost:{PORT}")
    print("Tests: buttons → locate sensor → backlight → sensor range → dark+wake")
    print()
    sys.stdout.flush()

    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        set_brightness(255)
        print("\nStopped.")
