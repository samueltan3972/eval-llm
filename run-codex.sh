#!/bin/bash
PROMPT="$1"
codex exec --model gpt-5.5 --ignore-user-config --output-last-message .last-msg.txt "$PROMPT" > /dev/null 2>&1
cat .last-msg.txt
