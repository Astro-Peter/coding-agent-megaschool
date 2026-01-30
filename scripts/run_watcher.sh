#!/bin/bash
# Unified watcher script - handles all agent events
# Events: new issues (auto-plan), PR updates (auto-review)

set -e
cd "$(dirname "$0")/.."
python -m scripts.watcher
