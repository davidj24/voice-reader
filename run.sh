#!/usr/bin/env bash
# Starts the reader with the Python environment that holds Kokoro.
cd "$(dirname "$0")"
DATA="${VOICE_READER_DATA:-$HOME/.local/share/claude-read-aloud}"
exec "$DATA/venv/bin/python" -u reader.py
