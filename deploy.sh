#!/usr/bin/env bash
set -e

ensure_dir() { ampy ls "$1" > /dev/null 2>&1 || ampy mkdir "$1"; }

if [ -n "$1" ]; then
    d="$(dirname "$1")"
    [ "$d" != "." ] && ensure_dir "$d"
    ampy put "$1" "$1" && echo "  $1"
else
    ampy put config.py && echo "  config.py"
    ampy put boot.py   && echo "  boot.py"
    ampy put main.py   && echo "  main.py"
fi
echo "Done."
