#!/usr/bin/env python3
"""Touch calibration server - serves test page and collects results.

Serves a touch calibration HTML page on port 8099. When the user completes
all targets, the page POSTs results back to the server, which saves them
to /tmp/touch_calibration_results.json and prints a summary.
"""
import http.server
import json
import os
import signal
import sys
import threading

PORT = 8099
RESULTS_FILE = "/tmp/touch_calibration_results.json"

HTML_PAGE = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<title>Touch Calibration Test</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { width: 100%; height: 100%; overflow: hidden; touch-action: none; }
body { background: #1a1a2e; color: #e0e0e0; font-family: monospace; font-size: 14px; }
canvas { display: block; width: 100%; height: 100%; }
#info {
  position: absolute; top: 10px; left: 10px;
  background: rgba(0,0,0,0.7); padding: 8px 12px; border-radius: 4px;
  z-index: 10; font-size: 12px; line-height: 1.5;
}
#reset-btn {
  position: absolute; top: 10px; right: 10px;
  background: #e94560; color: white; border: none;
  padding: 8px 16px; border-radius: 4px; font-size: 14px;
  z-index: 10; cursor: pointer;
}
</style>
</head>
<body>
<div id="info">
  Tap each yellow circle target in order.<br>
  <span id="status">Waiting for touch... (target 1/9: TL)</span>
</div>
<button id="reset-btn" ontouchstart="resetTest()">Reset</button>
<canvas id="canvas"></canvas>
<script>
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const statusEl = document.getElementById('status');

const targetFractions = [
  { x: 0.1, y: 0.1, label: 'TL' }, { x: 0.5, y: 0.1, label: 'TC' },
  { x: 0.9, y: 0.1, label: 'TR' }, { x: 0.1, y: 0.5, label: 'ML' },
  { x: 0.5, y: 0.5, label: 'CC' }, { x: 0.9, y: 0.5, label: 'MR' },
  { x: 0.1, y: 0.9, label: 'BL' }, { x: 0.5, y: 0.9, label: 'BC' },
  { x: 0.9, y: 0.9, label: 'BR' },
];

let targets = [];
let currentTarget = 0;
const TARGET_RADIUS = 25;

function resize() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  targets = targetFractions.map(t => ({
    x: Math.round(t.x * canvas.width), y: Math.round(t.y * canvas.height),
    label: t.label, hit: null
  }));
  draw();
}

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  // Grid
  ctx.strokeStyle = '#333'; ctx.lineWidth = 1;
  for (let i = 0; i <= 10; i++) {
    let x = (canvas.width / 10) * i, y = (canvas.height / 10) * i;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
  }
  // Targets
  targets.forEach((t, i) => {
    if (t.hit) { ctx.strokeStyle = '#00ff88'; ctx.fillStyle = 'rgba(0,255,136,0.2)'; }
    else if (i === currentTarget) { ctx.strokeStyle = '#ffdd00'; ctx.fillStyle = 'rgba(255,221,0,0.3)'; }
    else { ctx.strokeStyle = '#555'; ctx.fillStyle = 'rgba(85,85,85,0.1)'; }
    ctx.lineWidth = 3;
    ctx.beginPath(); ctx.arc(t.x, t.y, TARGET_RADIUS, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    // Crosshair
    ctx.beginPath();
    ctx.moveTo(t.x - TARGET_RADIUS - 5, t.y); ctx.lineTo(t.x + TARGET_RADIUS + 5, t.y);
    ctx.moveTo(t.x, t.y - TARGET_RADIUS - 5); ctx.lineTo(t.x, t.y + TARGET_RADIUS + 5);
    ctx.stroke();
    // Label
    ctx.fillStyle = ctx.strokeStyle; ctx.font = '12px monospace'; ctx.textAlign = 'center';
    ctx.fillText(t.label, t.x, t.y - TARGET_RADIUS - 8);
    // Hit marker
    if (t.hit) {
      ctx.fillStyle = '#ff4444';
      ctx.beginPath(); ctx.arc(t.hit.x, t.hit.y, 5, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = '#ff4444'; ctx.lineWidth = 1; ctx.setLineDash([3,3]);
      ctx.beginPath(); ctx.moveTo(t.x, t.y); ctx.lineTo(t.hit.x, t.hit.y); ctx.stroke();
      ctx.setLineDash([]);
      let dx = t.hit.x - t.x, dy = t.hit.y - t.y;
      ctx.fillStyle = '#ff8888'; ctx.font = '10px monospace';
      ctx.fillText('d=' + Math.sqrt(dx*dx+dy*dy).toFixed(0) + 'px', t.x, t.y + TARGET_RADIUS + 16);
    }
  });
  // Summary
  if (currentTarget >= targets.length) drawSummary();
}

function drawSummary() {
  let totalDist = 0, maxDist = 0, avgDx = 0, avgDy = 0;
  targets.forEach(t => {
    if (t.hit) {
      let dx = t.hit.x - t.x, dy = t.hit.y - t.y;
      let dist = Math.sqrt(dx*dx + dy*dy);
      totalDist += dist; maxDist = Math.max(maxDist, dist);
      avgDx += dx; avgDy += dy;
    }
  });
  let n = targets.length;
  avgDx /= n; avgDy /= n;
  let avgDist = totalDist / n;

  ctx.fillStyle = 'rgba(0,0,0,0.85)';
  ctx.fillRect(canvas.width/2 - 220, canvas.height/2 - 70, 440, 140);
  ctx.fillStyle = '#00ff88'; ctx.font = '16px monospace'; ctx.textAlign = 'center';
  ctx.fillText('CALIBRATION COMPLETE', canvas.width/2, canvas.height/2 - 45);
  ctx.font = '13px monospace'; ctx.fillStyle = '#e0e0e0';
  ctx.fillText('Avg offset: dx=' + avgDx.toFixed(1) + ' dy=' + avgDy.toFixed(1), canvas.width/2, canvas.height/2 - 20);
  ctx.fillText('Avg distance: ' + avgDist.toFixed(1) + 'px', canvas.width/2, canvas.height/2);
  ctx.fillText('Max distance: ' + maxDist.toFixed(1) + 'px', canvas.width/2, canvas.height/2 + 20);
  ctx.fillText('Screen: ' + canvas.width + 'x' + canvas.height, canvas.width/2, canvas.height/2 + 40);
  ctx.fillStyle = '#888';
  ctx.fillText('Results sent to server.', canvas.width/2, canvas.height/2 + 60);
}

function sendResults() {
  let results = {
    screen: { width: canvas.width, height: canvas.height },
    targets: targets.map(t => ({
      label: t.label,
      target: { x: t.x, y: t.y },
      touch: t.hit ? { x: t.hit.x, y: t.hit.y } : null,
      offset: t.hit ? { dx: t.hit.x - t.x, dy: t.hit.y - t.y } : null,
      distance: t.hit ? Math.sqrt((t.hit.x-t.x)**2 + (t.hit.y-t.y)**2) : null
    })),
    summary: {}
  };
  let totalDist = 0, maxDist = 0, avgDx = 0, avgDy = 0, n = 0;
  results.targets.forEach(t => {
    if (t.distance !== null) {
      totalDist += t.distance; maxDist = Math.max(maxDist, t.distance);
      avgDx += t.offset.dx; avgDy += t.offset.dy; n++;
    }
  });
  results.summary = {
    avg_dx: n ? avgDx / n : 0, avg_dy: n ? avgDy / n : 0,
    avg_distance: n ? totalDist / n : 0, max_distance: maxDist, count: n
  };
  fetch('/results', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(results)
  }).catch(e => console.error('Failed to send results:', e));
}

function handleTouch(e) {
  e.preventDefault();
  if (currentTarget >= targets.length) return;
  let touch = e.touches ? (e.touches[0] || e.changedTouches[0]) : e;
  let tx = touch.clientX, ty = touch.clientY;
  targets[currentTarget].hit = { x: Math.round(tx), y: Math.round(ty) };
  let dx = tx - targets[currentTarget].x, dy = ty - targets[currentTarget].y;
  let dist = Math.sqrt(dx*dx + dy*dy);
  currentTarget++;
  if (currentTarget < targets.length) {
    statusEl.textContent = 'Target ' + (currentTarget+1) + '/9: ' + targets[currentTarget].label +
      ' (last: d=' + dist.toFixed(0) + 'px)';
  } else {
    statusEl.textContent = 'Done! Sending results...';
    sendResults();
  }
  draw();
}

function resetTest() {
  currentTarget = 0;
  targets.forEach(t => t.hit = null);
  statusEl.textContent = 'Waiting for touch... (target 1/9: TL)';
  draw();
}

window.addEventListener('resize', resize);
canvas.addEventListener('touchstart', handleTouch);
canvas.addEventListener('touchmove', e => e.preventDefault());
canvas.addEventListener('click', handleTouch);
resize();
</script>
</body>
</html>"""


class CalibrationHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode())

    def do_POST(self):
        if self.path == "/results":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                with open(RESULTS_FILE, "w") as f:
                    json.dump(data, f, indent=2)
                print("\n" + "=" * 60)
                print("CALIBRATION RESULTS RECEIVED")
                print("=" * 60)
                print(f"Screen: {data['screen']['width']}x{data['screen']['height']}")
                print()
                for t in data["targets"]:
                    if t["touch"]:
                        print(f"  {t['label']}: target=({t['target']['x']},{t['target']['y']}) "
                              f"touch=({t['touch']['x']},{t['touch']['y']}) "
                              f"offset=({t['offset']['dx']},{t['offset']['dy']}) "
                              f"dist={t['distance']:.1f}px")
                s = data["summary"]
                print()
                print(f"  Avg offset: dx={s['avg_dx']:.1f} dy={s['avg_dy']:.1f}")
                print(f"  Avg distance: {s['avg_distance']:.1f}px")
                print(f"  Max distance: {s['max_distance']:.1f}px")
                print(f"  Points: {s['count']}")
                print("=" * 60)
                print(f"Results saved to {RESULTS_FILE}")
                sys.stdout.flush()
            except Exception as e:
                print(f"Error processing results: {e}", file=sys.stderr)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress request logging


def main():
    server = http.server.HTTPServer(("0.0.0.0", PORT), CalibrationHandler)
    print(f"Calibration server running on http://localhost:{PORT}")
    print("Open this URL in the kiosk browser, then tap each target.")
    print("Results will be printed here and saved to " + RESULTS_FILE)
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()
