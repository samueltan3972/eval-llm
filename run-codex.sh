#!/bin/bash
PROMPT="$1"
# Stream Codex's final message straight to our stdout via a spare fd — no temp file at all.
# `3>&1` saves the captured stdout as fd 3; codex writes the last message there while all its
# chatter goes to /dev/null. This is race-free (each process has its own fd 3) and keeps file
# I/O out of the timed path, so it can't bias the speed measurement.
codex exec --model gpt-5.5 --ignore-user-config --output-last-message /dev/fd/3 "$PROMPT" 3>&1 >/dev/null 2>&1
