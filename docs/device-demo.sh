#!/usr/bin/env bash
set -euo pipefail

APK="${1:-}"
OUT="${2:-device-demo.mp4}"
DURATION="${DURATION:-60}"
ADB="${ADB:-adb}"
AAPT2="${AAPT2:-}"
SERIAL="${ANDROID_SERIAL:-}"

if [[ -z "$APK" || ! -f "$APK" ]]; then
  echo "Usage: bash docs/device-demo.sh /path/to/Game-WebView.apk [output.mp4]" >&2
  exit 2
fi

if [[ -z "$AAPT2" ]]; then
  AAPT2="$(find "${ANDROID_HOME:-$HOME/Library/Android/sdk}/build-tools" -name aapt2 2>/dev/null | sort -V | tail -1 || true)"
fi
if [[ -z "$AAPT2" || ! -x "$AAPT2" ]]; then
  echo "aapt2 not found; set AAPT2=/path/to/aapt2" >&2
  exit 1
fi

ADB_CMD=("$ADB")
if [[ -n "$SERIAL" ]]; then
  ADB_CMD=(-s "$SERIAL" "$ADB")
fi

if ! "${ADB_CMD[@]}" get-state >/dev/null 2>&1; then
  echo "No authorized Android device; enable USB debugging and run adb devices." >&2
  exit 1
fi

BADGING="$("$AAPT2" dump badging "$APK")"
PKG="$(printf '%s\n' "$BADGING" | sed -n "s/^package: name='\([^']*\)'.*/\1/p" | head -1)"
ACTIVITY="$(printf '%s\n' "$BADGING" | sed -n "s/^launchable-activity: name='\([^']*\)'.*/\1/p" | head -1)"
if [[ -z "$PKG" || -z "$ACTIVITY" ]]; then
  echo "Could not read package or launchable activity from $APK" >&2
  exit 1
fi
if [[ "$ACTIVITY" == .* ]]; then
  ACTIVITY="$PKG$ACTIVITY"
fi

echo "Installing $APK"
"${ADB_CMD[@]}" install -r "$APK" >/dev/null
echo "Starting $PKG/$ACTIVITY"
"${ADB_CMD[@]}" shell am start -n "$PKG/$ACTIVITY" >/dev/null
sleep 2

REMOTE="/sdcard/pc2a-device-demo.mp4"
echo "Recording ${DURATION}s. Operate the game on the phone now..."
"${ADB_CMD[@]}" shell screenrecord --time-limit "$DURATION" --bit-rate 8000000 "$REMOTE"
"${ADB_CMD[@]}" pull "$REMOTE" "$OUT" >/dev/null
"${ADB_CMD[@]}" shell rm -f "$REMOTE"
echo "Saved $OUT"
