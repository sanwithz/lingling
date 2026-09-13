#!/bin/sh
# Locate a usable Python, then hand off to secretary.py.
#
# Why this exists: a hook entry in hooks.json cannot branch per platform, and no
# single interpreter name works everywhere. macOS and most Linux distros ship
# python3 with no python; a python.org install on Windows ships python.exe and
# py.exe but no python3.exe.
#
# The WindowsApps skip matters. Windows 10/11 preinstall zero-byte "app execution
# alias" stubs at ~/AppData/Local/Microsoft/WindowsApps/python3.exe. They resolve
# on PATH, so a naive `command -v python3` finds one, but running it only opens
# the Microsoft Store. Anything under that directory is skipped for that reason.
#
# Hooks must never break a session, so a missing interpreter still exits 0 and
# only writes a line to stderr, which Claude Code surfaces as a hook warning.

set -u

for candidate in python3 python py; do
    resolved=$(command -v "$candidate" 2>/dev/null) || continue
    case "$resolved" in
        *WindowsApps*) continue ;;
    esac
    PYTHONUTF8=1
    export PYTHONUTF8
    exec "$candidate" "${CLAUDE_PLUGIN_ROOT:-$(dirname "$0")/..}/scripts/secretary.py" "$@"
done

echo "lingling: no usable python found on PATH (tried python3, python, py)." >&2
echo "lingling: install Python 3.9+ from python.org, then reopen your terminal." >&2
exit 0
