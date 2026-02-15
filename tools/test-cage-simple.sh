#!/bin/bash
echo "=== Starting cage test at $(date) ===" >> /home/tim/cage-test.log
echo "USER=$(whoami) UID=$(id -u) GROUPS=$(groups)" >> /home/tim/cage-test.log
echo "WAYLAND_DISPLAY=$WAYLAND_DISPLAY" >> /home/tim/cage-test.log
echo "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR" >> /home/tim/cage-test.log
echo "DRI devices:" >> /home/tim/cage-test.log
ls -la /dev/dri/ >> /home/tim/cage-test.log 2>&1
echo "Running wlr-randr:" >> /home/tim/cage-test.log
wlr-randr >> /home/tim/cage-test.log 2>&1
echo "wlr-randr exit: $?" >> /home/tim/cage-test.log
echo "Sleeping 30s..." >> /home/tim/cage-test.log
sleep 30
echo "=== Done ===" >> /home/tim/cage-test.log
