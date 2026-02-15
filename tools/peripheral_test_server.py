#!/usr/bin/env python3
"""Interactive peripheral test suite for reTerminal kiosk.

Serves a webpage that runs through all interactive tests:
  1. Display - verifies resolution and orientation
  2. Touch - 9-point accuracy test
  3. GPIO Buttons - tests all 4 physical buttons

Results are automatically POST'd back and printed with PASS/FAIL verdicts.

Usage on the reTerminal:
    python3 peripheral_test_server.py &
    # Stop kiosk, launch test browser:
    sudo systemctl stop cage-kiosk@tty7
    cage -s -- chromium --no-sandbox --kiosk --start-fullscreen \
        --ozone-platform=wayland --enable-features=UseOzonePlatform \
        --user-data-dir=/home/tim/.config/chromium-test \
        http://localhost:8097

Or remotely:
    scp peripheral_test_server.py tim@DEVICE:/home/tim/
    ssh tim@DEVICE 'python3 /home/tim/peripheral_test_server.py'
"""
import http.server
import json
import sys
import textwrap

PORT = 8097

# Thresholds for pass/fail
TOUCH_MAX_AVG_ERROR_PX = 40  # avg touch error must be under this
TOUCH_MAX_SINGLE_ERROR_PX = 80  # no single point worse than this
EXPECTED_WIDTH = 1280  # after 270-degree rotation of 720x1280
EXPECTED_HEIGHT = 720

HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>reTerminal Peripheral Test</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; cursor: none; }
body {
    background: #0d1117; color: #e6edf3;
    font-family: -apple-system, sans-serif;
    width: 100vw; height: 100vh; overflow: hidden;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
}
h1 { font-size: 24px; margin-bottom: 8px; }
h2 { font-size: 20px; margin-bottom: 12px; color: #58a6ff; }
.subtitle { font-size: 14px; color: #8b949e; margin-bottom: 16px; }

/* Test phases */
.phase { display: none; width: 100%; height: 100%;
         flex-direction: column; align-items: center; justify-content: center; }
.phase.active { display: flex; }

/* Display test */
.display-info { font-size: 16px; line-height: 2; }
.display-info .val { color: #58a6ff; font-weight: bold; }
.display-info .pass { color: #3fb950; }
.display-info .fail { color: #f85149; }

/* Touch test */
.touch-area { position: absolute; top: 0; left: 0; width: 100%; height: 100%; }
.target {
    position: absolute; width: 40px; height: 40px;
    border: 3px solid #58a6ff; border-radius: 50%;
    transform: translate(-50%, -50%);
    transition: all 0.2s;
}
.target.active { border-color: #3fb950; background: rgba(63,185,80,0.3); animation: pulse 1s infinite; }
.target.done { border-color: #3fb950; background: rgba(63,185,80,0.5); }
.target .dot { position: absolute; top: 50%; left: 50%; width: 6px; height: 6px;
               background: #58a6ff; border-radius: 50%; transform: translate(-50%, -50%); }
.target.active .dot { background: #3fb950; }
.target.done .dot { background: #3fb950; }
.touch-hint {
    position: absolute; bottom: 30px; left: 0; right: 0; text-align: center;
    font-size: 18px; color: #8b949e; z-index: 10;
}
.touch-progress {
    position: absolute; top: 15px; left: 0; right: 0; text-align: center;
    font-size: 16px; color: #58a6ff; z-index: 10;
}
@keyframes pulse {
    0%, 100% { transform: translate(-50%, -50%) scale(1); }
    50% { transform: translate(-50%, -50%) scale(1.3); }
}

/* Button test */
.buttons-row { display: flex; gap: 24px; margin-bottom: 20px; }
.btn-box {
    width: 130px; height: 130px; border-radius: 16px;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    font-size: 44px; font-weight: bold; transition: all 0.15s;
}
.btn-box .label { font-size: 13px; margin-top: 6px; opacity: 0.7; }
.btn-box.waiting { background: #21262d; border: 2px solid #30363d; }
.btn-box.pressed { background: #0d4429; border: 2px solid #3fb950; color: #3fb950; }
.btn-box.active { background: #3fb950; border: 2px solid #3fb950; color: #000; transform: scale(1.08); }
.btn-status { font-size: 16px; color: #8b949e; }

/* Results */
.results-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    padding: 20px 30px; min-width: 400px;
}
.result-row { display: flex; justify-content: space-between; padding: 8px 0;
              border-bottom: 1px solid #21262d; font-size: 16px; }
.result-row:last-child { border-bottom: none; }
.result-row .name { color: #8b949e; }
.result-row .pass { color: #3fb950; font-weight: bold; }
.result-row .fail { color: #f85149; font-weight: bold; }
.overall { font-size: 28px; font-weight: bold; margin-top: 16px; }
.overall.pass { color: #3fb950; }
.overall.fail { color: #f85149; }
</style>
</head>
<body>

<!-- Phase 0: Display test (auto, no interaction needed) -->
<div class="phase active" id="phase-display">
    <h1>Test 1/3: Display</h1>
    <div class="subtitle">Checking resolution and orientation...</div>
    <div class="display-info" id="display-info"></div>
</div>

<!-- Phase 1: Touch test -->
<div class="phase" id="phase-touch">
    <div class="touch-area" id="touch-area"></div>
    <div class="touch-progress" id="touch-progress">Test 2/3: Touch Accuracy (0/9)</div>
    <div class="touch-hint" id="touch-hint">Tap the pulsing circle</div>
</div>

<!-- Phase 2: Button test -->
<div class="phase" id="phase-buttons">
    <h1>Test 3/3: GPIO Buttons</h1>
    <div class="subtitle">Press each of the 4 physical buttons</div>
    <div class="buttons-row">
        <div class="btn-box waiting" id="btn-a"><span>A</span><div class="label">Button 1</div></div>
        <div class="btn-box waiting" id="btn-s"><span>S</span><div class="label">Button 2</div></div>
        <div class="btn-box waiting" id="btn-d"><span>D</span><div class="label">Button 3</div></div>
        <div class="btn-box waiting" id="btn-f"><span>F</span><div class="label">Button 4</div></div>
    </div>
    <div class="btn-status" id="btn-status">0/4 detected — waiting 60s...</div>
</div>

<!-- Phase 3: Results -->
<div class="phase" id="phase-results">
    <h1>Test Results</h1>
    <div class="results-card" id="results-card"></div>
    <div class="overall" id="overall-result"></div>
</div>

<script>
// ========== State ==========
const results = {
    display: { width: 0, height: 0, ratio: '', orientation: '', pass: false },
    touch: { points: [], avgError: 0, maxError: 0, pass: false },
    buttons: { detected: [], total: 0, pass: false },
};

const TOUCH_TARGETS = [
    {name: 'TL', xPct: 0.1,  yPct: 0.1},
    {name: 'TC', xPct: 0.5,  yPct: 0.1},
    {name: 'TR', xPct: 0.9,  yPct: 0.1},
    {name: 'ML', xPct: 0.1,  yPct: 0.5},
    {name: 'CC', xPct: 0.5,  yPct: 0.5},
    {name: 'MR', xPct: 0.9,  yPct: 0.5},
    {name: 'BL', xPct: 0.1,  yPct: 0.9},
    {name: 'BC', xPct: 0.5,  yPct: 0.9},
    {name: 'BR', xPct: 0.9,  yPct: 0.9},
];

let currentPhase = 0;
let touchIndex = 0;
let buttonsDone = false;
const buttonsPressed = new Set();

// ========== Phase management ==========
function showPhase(n) {
    document.querySelectorAll('.phase').forEach(p => p.classList.remove('active'));
    document.getElementById(['phase-display','phase-touch','phase-buttons','phase-results'][n]).classList.add('active');
    currentPhase = n;
}

// ========== Phase 0: Display ==========
function runDisplayTest() {
    const cssW = window.innerWidth;
    const cssH = window.innerHeight;
    const dpr = window.devicePixelRatio || 1;
    const physW = Math.round(cssW * dpr);
    const physH = Math.round(cssH * dpr);
    const isLandscape = cssW > cssH;
    const ratio = (cssW/cssH).toFixed(2);

    // Check physical resolution (CSS pixels * devicePixelRatio)
    const widthOk = Math.abs(physW - """ + str(EXPECTED_WIDTH) + r""") < 40;
    const heightOk = Math.abs(physH - """ + str(EXPECTED_HEIGHT) + r""") < 40;
    const orientOk = isLandscape;
    const pass = widthOk && heightOk && orientOk;

    results.display = {
        cssWidth: cssW, cssHeight: cssH,
        physWidth: physW, physHeight: physH,
        dpr: dpr, ratio: ratio,
        orientation: isLandscape ? 'landscape' : 'portrait',
        pass: pass,
    };

    const info = document.getElementById('display-info');
    const cls = (ok) => ok ? 'pass' : 'fail';
    const icon = (ok) => ok ? '✓' : '✗';
    info.innerHTML = `
        <div>Physical: <span class="val">${physW} × ${physH}</span>
             <span class="${cls(widthOk && heightOk)}">${icon(widthOk && heightOk)}
             (expected """ + str(EXPECTED_WIDTH) + " × " + str(EXPECTED_HEIGHT) + r""")</span></div>
        <div>CSS viewport: <span class="val">${cssW} × ${cssH}</span> (DPR: ${dpr.toFixed(2)})</div>
        <div>Orientation: <span class="val">${results.display.orientation}</span>
             <span class="${cls(orientOk)}">${icon(orientOk)} (expected landscape)</span></div>
        <div>Aspect ratio: <span class="val">${ratio}</span></div>
        <div style="margin-top: 12px; font-size: 18px;" class="${cls(pass)}">
            Display: ${pass ? 'PASS' : 'FAIL'}</div>
    `;

    // Auto-advance after 3 seconds
    setTimeout(() => {
        showPhase(1);
        startTouchTest();
    }, 3000);
}

// ========== Phase 1: Touch ==========
function startTouchTest() {
    const area = document.getElementById('touch-area');
    const w = window.innerWidth;
    const h = window.innerHeight;

    // Create all targets
    TOUCH_TARGETS.forEach((t, i) => {
        const el = document.createElement('div');
        el.className = 'target' + (i === 0 ? ' active' : '');
        el.id = 'target-' + i;
        el.style.left = (t.xPct * w) + 'px';
        el.style.top = (t.yPct * h) + 'px';
        el.innerHTML = '<div class="dot"></div>';
        area.appendChild(el);
    });

    area.addEventListener('touchstart', handleTouch, { passive: false });
    area.addEventListener('click', handleTouch);
}

function handleTouch(e) {
    if (touchIndex >= TOUCH_TARGETS.length) return;
    e.preventDefault();

    let touchX, touchY;
    if (e.touches && e.touches.length > 0) {
        touchX = e.touches[0].clientX;
        touchY = e.touches[0].clientY;
    } else {
        touchX = e.clientX;
        touchY = e.clientY;
    }

    const target = TOUCH_TARGETS[touchIndex];
    const expectedX = target.xPct * window.innerWidth;
    const expectedY = target.yPct * window.innerHeight;
    const dx = touchX - expectedX;
    const dy = touchY - expectedY;
    const distance = Math.sqrt(dx*dx + dy*dy);

    results.touch.points.push({
        name: target.name,
        expectedX: Math.round(expectedX),
        expectedY: Math.round(expectedY),
        actualX: Math.round(touchX),
        actualY: Math.round(touchY),
        error: Math.round(distance * 10) / 10,
    });

    // Mark done
    document.getElementById('target-' + touchIndex).className = 'target done';
    touchIndex++;

    document.getElementById('touch-progress').textContent =
        `Test 2/3: Touch Accuracy (${touchIndex}/9)`;

    if (touchIndex < TOUCH_TARGETS.length) {
        document.getElementById('target-' + touchIndex).className = 'target active';
    } else {
        // Calculate stats
        const errors = results.touch.points.map(p => p.error);
        results.touch.avgError = Math.round(errors.reduce((a,b) => a+b, 0) / errors.length * 10) / 10;
        results.touch.maxError = Math.round(Math.max(...errors) * 10) / 10;
        results.touch.pass = (
            results.touch.avgError < """ + str(TOUCH_MAX_AVG_ERROR_PX) + r""" &&
            results.touch.maxError < """ + str(TOUCH_MAX_SINGLE_ERROR_PX) + r"""
        );

        document.getElementById('touch-hint').textContent =
            `Touch: avg ${results.touch.avgError}px, max ${results.touch.maxError}px — ` +
            (results.touch.pass ? 'PASS' : 'FAIL');

        setTimeout(() => {
            showPhase(2);
            startButtonTest();
        }, 2000);
    }
}

// ========== Phase 2: Buttons ==========
function startButtonTest() {
    const start = Date.now();
    const TIMEOUT = 60000;

    function checkButtons() {
        const count = buttonsPressed.size;
        document.getElementById('btn-status').textContent =
            `${count}/4 detected` + (count < 4 ? ` — ${Math.round((TIMEOUT - (Date.now()-start))/1000)}s remaining` : '');

        if (count === 4 && !buttonsDone) {
            finishButtons();
        } else if (Date.now() - start > TIMEOUT && !buttonsDone) {
            finishButtons();
        }
    }

    setInterval(checkButtons, 500);

    document.addEventListener('keydown', function(e) {
        if (buttonsDone) return;
        const key = e.key.toLowerCase();
        if (['a','s','d','f'].includes(key)) {
            e.preventDefault();
            document.getElementById('btn-' + key).className = 'btn-box active';
            buttonsPressed.add(key);
        }
    });

    document.addEventListener('keyup', function(e) {
        const key = e.key.toLowerCase();
        if (['a','s','d','f'].includes(key)) {
            e.preventDefault();
            document.getElementById('btn-' + key).className =
                buttonsPressed.has(key) ? 'btn-box pressed' : 'btn-box waiting';
        }
    });
}

function finishButtons() {
    buttonsDone = true;
    results.buttons.detected = Array.from(buttonsPressed).sort();
    results.buttons.total = buttonsPressed.size;
    results.buttons.pass = buttonsPressed.size === 4;

    setTimeout(() => {
        showPhase(3);
        showResults();
    }, 1000);
}

// ========== Phase 3: Results ==========
function showResults() {
    const allPass = results.display.pass && results.touch.pass && results.buttons.pass;

    const card = document.getElementById('results-card');
    const cls = (ok) => ok ? 'pass' : 'fail';

    let touchDetail = `avg ${results.touch.avgError}px, max ${results.touch.maxError}px`;
    let btnDetail = `${results.buttons.total}/4`;
    if (!results.buttons.pass) {
        const missing = ['A','S','D','F'].filter(k => !buttonsPressed.has(k.toLowerCase()));
        btnDetail += ` (missing: ${missing.join(', ')})`;
    }

    card.innerHTML = `
        <div class="result-row">
            <span class="name">Display (${results.display.physWidth}×${results.display.physHeight} ${results.display.orientation})</span>
            <span class="${cls(results.display.pass)}">${results.display.pass ? 'PASS' : 'FAIL'}</span>
        </div>
        <div class="result-row">
            <span class="name">Touch (${touchDetail})</span>
            <span class="${cls(results.touch.pass)}">${results.touch.pass ? 'PASS' : 'FAIL'}</span>
        </div>
        <div class="result-row">
            <span class="name">Buttons (${btnDetail})</span>
            <span class="${cls(results.buttons.pass)}">${results.buttons.pass ? 'PASS' : 'FAIL'}</span>
        </div>
    `;

    const overall = document.getElementById('overall-result');
    overall.textContent = allPass ? 'ALL TESTS PASSED' : 'SOME TESTS FAILED';
    overall.className = 'overall ' + (allPass ? 'pass' : 'fail');

    // POST results to server
    fetch('/results', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(results),
    });
}

// Start!
runDisplayTest();
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
    """Print formatted test results with pass/fail verdicts."""
    print()
    print("=" * 60)
    print("  reTerminal Peripheral Test Results")
    print("=" * 60)

    # Display
    d = data.get("display", {})
    dp = d.get("pass", False)
    print(f"\n  1. DISPLAY: {'PASS' if dp else 'FAIL'}")
    print(f"     Physical: {d.get('physWidth')} x {d.get('physHeight')}"
          f" (expected {EXPECTED_WIDTH} x {EXPECTED_HEIGHT})")
    print(f"     CSS viewport: {d.get('cssWidth')} x {d.get('cssHeight')}"
          f" (DPR: {d.get('dpr', '?')})")
    print(f"     Orientation: {d.get('orientation')} (expected landscape)")

    # Touch
    t = data.get("touch", {})
    tp = t.get("pass", False)
    print(f"\n  2. TOUCH: {'PASS' if tp else 'FAIL'}")
    print(f"     Avg error: {t.get('avgError')}px"
          f" (threshold: <{TOUCH_MAX_AVG_ERROR_PX}px)")
    print(f"     Max error: {t.get('maxError')}px"
          f" (threshold: <{TOUCH_MAX_SINGLE_ERROR_PX}px)")
    for pt in t.get("points", []):
        print(f"       {pt['name']:2s}: expected ({pt['expectedX']},{pt['expectedY']})"
              f" got ({pt['actualX']},{pt['actualY']})"
              f" error={pt['error']}px")

    # Buttons
    b = data.get("buttons", {})
    bp = b.get("pass", False)
    print(f"\n  3. BUTTONS: {'PASS' if bp else 'FAIL'}")
    print(f"     Detected: {b.get('total')}/4"
          f" ({', '.join(k.upper() for k in b.get('detected', []))})")
    missing = set("asdf") - set(b.get("detected", []))
    if missing:
        print(f"     Missing: {', '.join(sorted(m.upper() for m in missing))}")

    # Overall
    all_pass = dp and tp and bp
    print()
    print("=" * 60)
    if all_pass:
        print("  OVERALL: ALL TESTS PASSED")
    else:
        failed = []
        if not dp:
            failed.append("display")
        if not tp:
            failed.append("touch")
        if not bp:
            failed.append("buttons")
        print(f"  OVERALL: FAILED ({', '.join(failed)})")
    print("=" * 60)
    print()
    sys.stdout.flush()


if __name__ == "__main__":
    print(f"Peripheral test server on http://localhost:{PORT}")
    print(f"Thresholds: touch avg <{TOUCH_MAX_AVG_ERROR_PX}px,"
          f" max <{TOUCH_MAX_SINGLE_ERROR_PX}px")
    print(f"Expected display: {EXPECTED_WIDTH}x{EXPECTED_HEIGHT} landscape")
    print()
    print("Tests run in order: Display (auto) -> Touch (9 taps) -> Buttons (4 presses)")
    print("Results are printed here automatically when complete.")
    print()
    sys.stdout.flush()

    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
