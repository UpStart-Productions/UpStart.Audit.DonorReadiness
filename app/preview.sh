#!/bin/bash
# Quick render loop — edit template.html, run this, see the result.
# Usage: ./preview.sh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/renderer.py" \
  "$SCRIPT_DIR/sample_report.json" \
  "$SCRIPT_DIR/preview.pdf" 2>&1 | grep -v "HTML written"
echo "→ preview.pdf updated"
