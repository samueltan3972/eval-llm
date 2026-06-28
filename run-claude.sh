#!/bin/bash
PROMPT="$1"
claude --no-session-persistence --model claude-opus-4-8 -p "$PROMPT"
