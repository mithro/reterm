#!/usr/bin/env python3
"""Button test server for reTerminal.

Serves a webpage that listens for keyboard events and reports which
GPIO buttons (keycodes A/S/D/F) were pressed. Results are POST'd back.

Usage:
    python3 test_buttons_server.py
    # Then point chromium at http://localhost:8098
"""
import http.server
import json
import sys

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Button Test</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; cursor: none; }
body {
    background: #1a1a2e; color: white;
    font-family: sans-serif;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    height: 100vh; width: 100vw;
    overflow: hidden;
}
h1 { font-size: 28px; margin-bottom: 20px; }
.buttons {
    display: flex; gap: 20px;
    margin-bottom: 30px;
}
.btn {
    width: 140px; height: 140px;
    border-radius: 16px;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    font-size: 48px; font-weight: bold;
    transition: all 0.15s;
}
.btn .label { font-size: 14px; margin-top: 8px; opacity: 0.7; }
.btn.waiting { background: #333; border: 3px solid #555; }
.btn.pressed { background: #0f3; border: 3px solid #0f3; color: #000; transform: scale(1.05); }
.btn.active { background: #ff0; border: 3px solid #ff0; color: #000; transform: scale(1.1); }
#status { font-size: 20px; margin-top: 20px; }
#log { font-size: 14px; margin-top: 10px; opacity: 0.6; max-height: 80px; overflow: hidden; }
.result { font-size: 36px; font-weight: bold; margin-top: 20px; }
.result.pass { color: #0f3; }
.result.partial { color: #ff0; }
</style>
</head>
<body>
<h1>GPIO Button Test</h1>
<div class="buttons">
    <div class="btn waiting" id="btn-a"><span>A</span><div class="label">Button 1</div></div>
    <div class="btn waiting" id="btn-s"><span>S</span><div class="label">Button 2</div></div>
    <div class="btn waiting" id="btn-d"><span>D</span><div class="label">Button 3</div></div>
    <div class="btn waiting" id="btn-f"><span>F</span><div class="label">Button 4</div></div>
</div>
<div id="status">Press each button. Waiting 60 seconds...</div>
<div id="log"></div>
<div id="result"></div>

<script>
const BUTTONS = {
    'a': {id: 'btn-a', name: 'Button 1 (A)'},
    's': {id: 'btn-s', name: 'Button 2 (S)'},
    'd': {id: 'btn-d', name: 'Button 3 (D)'},
    'f': {id: 'btn-f', name: 'Button 4 (F)'},
};

const pressed = new Set();
const events = [];
const startTime = Date.now();
let done = false;
const TIMEOUT = 60000;

function addLog(msg) {
    const log = document.getElementById('log');
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    log.textContent = `[${elapsed}s] ${msg}\\n` + log.textContent;
}

function checkDone() {
    if (done) return;
    const count = pressed.size;
    document.getElementById('status').textContent =
        `${count}/4 buttons detected. ${done ? '' : 'Press remaining...'}`;

    if (count === 4) {
        finish();
    }
}

function finish() {
    if (done) return;
    done = true;
    const count = pressed.size;
    const result = document.getElementById('result');

    if (count === 4) {
        result.textContent = 'ALL 4 BUTTONS WORKING';
        result.className = 'result pass';
    } else {
        const missing = ['a','s','d','f'].filter(k => !pressed.has(k));
        result.textContent = `${count}/4 detected (missing: ${missing.join(', ').toUpperCase()})`;
        result.className = 'result partial';
    }

    document.getElementById('status').textContent = 'Test complete.';

    // POST results
    const data = {
        buttons_detected: Array.from(pressed),
        total: count,
        all_passed: count === 4,
        events: events,
    };
    fetch('/results', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data),
    });
}

document.addEventListener('keydown', function(e) {
    if (done) return;
    const key = e.key.toLowerCase();
    const btn = BUTTONS[key];
    if (btn) {
        e.preventDefault();
        const el = document.getElementById(btn.id);
        el.className = 'btn active';
        if (!pressed.has(key)) {
            pressed.add(key);
            events.push({key: key, name: btn.name, time: Date.now() - startTime});
            addLog(`${btn.name}: DETECTED`);
        }
        checkDone();
    } else {
        addLog(`Unknown key: ${e.key} (code=${e.code})`);
    }
});

document.addEventListener('keyup', function(e) {
    const key = e.key.toLowerCase();
    const btn = BUTTONS[key];
    if (btn) {
        e.preventDefault();
        const el = document.getElementById(btn.id);
        el.className = pressed.has(key) ? 'btn pressed' : 'btn waiting';
    }
});

// Timeout
setTimeout(function() {
    if (!done) finish();
}, TIMEOUT);
</script>
</body>
</html>
"""

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML.encode())

    def do_POST(self):
        if self.path == "/results":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body)

            print("\n" + "=" * 50)
            print("BUTTON TEST RESULTS")
            print("=" * 50)
            print(f"Buttons detected: {data['total']}/4")
            for evt in data.get("events", []):
                print(f"  {evt['name']}: detected at {evt['time']}ms")

            missing = set("asdf") - set(data.get("buttons_detected", []))
            if missing:
                print(f"Missing: {', '.join(sorted(missing)).upper()}")

            if data.get("all_passed"):
                print("\nRESULT: ALL 4 BUTTONS WORKING")
            else:
                print(f"\nRESULT: {data['total']}/4 buttons detected")
            print("=" * 50)
            sys.stdout.flush()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress request logging

if __name__ == "__main__":
    port = 8098
    print(f"Button test server on http://localhost:{port}")
    print("Point chromium at this URL, then press each GPIO button.")
    print("Results will be printed here when all buttons are pressed (or after 60s timeout).")
    sys.stdout.flush()
    server = http.server.HTTPServer(("0.0.0.0", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
