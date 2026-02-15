#!/bin/bash
# Test cage with just wlr-randr, no chromium
wlr-randr --output DSI-1 --transform 270 2>&1 || true
wlr-randr 2>&1 || true
sleep 30
