#!/usr/bin/env python3
"""Run a command and report wall time, CPU time, and peak child RSS.

Usage: measure.py <label> -- <command> [args...]
Prints one JSON object to stdout (child stdout/stderr pass through to our
stderr so the JSON stays parseable).
"""
import json
import resource
import subprocess
import sys
import time

label = sys.argv[1]
assert sys.argv[2] == "--"
cmd = sys.argv[3:]

t0 = time.monotonic()
proc = subprocess.run(cmd, stdout=sys.stderr, stderr=sys.stderr)
wall = time.monotonic() - t0
ru = resource.getrusage(resource.RUSAGE_CHILDREN)

print(json.dumps({
    "label": label,
    "command": cmd,
    "exit": proc.returncode,
    "wall_s": round(wall, 2),
    "cpu_s": round(ru.ru_utime + ru.ru_stime, 2),
    "peak_rss_mb": round(ru.ru_maxrss / 1024, 1),
}))
