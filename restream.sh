#!/usr/bin/env bash
set -euo pipefail

KICK_RTMP="${KICK_RTMP:-rtmp://push.kick.com/live}"
KICK_STREAM_KEY="${1:?Usage: $0 <stream_key> <video_url> [kick_username]}"
VIDEO_URL="${2:?Usage: $0 <stream_key> <video_url> [kick_username]}"
KICK_USERNAME="${3:-}"
VIDEO_DIR="/tmp/kick-stream"
VIDEO_FILE="$VIDEO_DIR/video.mp4"
STOP_FILE="/tmp/kick-stream.stop"
FOLLOWER_FILE="$VIDEO_DIR/follower_count.txt"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAX_RESTARTS=0
RESTART_DELAY=5

mkdir -p "$VIDEO_DIR"
echo "LIVE" > "$FOLLOWER_FILE"
rm -f "$STOP_FILE"

cleanup() {
    echo "[kick] Shutting down..."
    touch "$STOP_FILE"
    kill "$FF_PID" 2>/dev/null || true
    kill "$FOLLOWER_PID" 2>/dev/null || true
    wait "$FF_PID" 2>/dev/null || true
    wait "$FOLLOWER_PID" 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT SIGHUP

download_video() {
    echo "[kick] Downloading video: $VIDEO_URL"
    rm -f "$VIDEO_FILE" "$VIDEO_FILE.part"
    if [[ "$VIDEO_URL" =~ \.m3u8 ]]; then
        ffmpeg -y -i "$VIDEO_URL" -c copy "$VIDEO_FILE" 2>&1
    else
        curl -L -o "$VIDEO_FILE.part" "$VIDEO_URL" 2>&1
        mv "$VIDEO_FILE.part" "$VIDEO_FILE"
    fi
    echo "[kick] Video downloaded to $VIDEO_FILE"
}

start_follower_tracker() {
    if [[ -n "$KICK_USERNAME" ]]; then
        echo "[kick] Starting follower tracker for: $KICK_USERNAME"
        python3 "$SCRIPT_DIR/get_followers.py" "$KICK_USERNAME" "$FOLLOWER_FILE" &
        FOLLOWER_PID=$!
    else
        echo "0" > "$FOLLOWER_FILE"
        FOLLOWER_PID=""
    fi
}

build_drawtext_filter() {
    local bg="rgba(0,0,0,0.6)"
    local fg="white"
    local fontfile=""

    echo "drawtext=textfile=${FOLLOWER_FILE}:reload=30:fontcolor=${fg}:fontsize=28:font=Sans:x=20:y=20:box=1:boxcolor=${bg}:boxborderw=12"
}

stream_loop() {
    local restarts=0
    local drawtext_filter
    drawtext_filter=$(build_drawtext_filter)

    while true; do
        if [[ -f "$STOP_FILE" ]]; then
            echo "[kick] Stop file found, exiting."
            break
        fi

        echo "[kick] Starting FFmpeg stream (attempt $((restarts + 1)))..."
        ffmpeg -re -stream_loop -1 \
            -i "$VIDEO_FILE" \
            -vf "$drawtext_filter" \
            -c:v libx264 -preset veryfast -b:v 4500k -maxrate 4500k -bufsize 9000k \
            -s 1920x1080 -r 30 \
            -c:a aac -b:a 128k -ar 44100 \
            -f flv "${KICK_RTMP}/${KICK_STREAM_KEY}" &
        FF_PID=$!

        wait "$FF_PID" || true
        EXIT_CODE=$?

        if [[ -f "$STOP_FILE" ]]; then
            echo "[kick] Stop file detected after FFmpeg exit."
            break
        fi

        restarts=$((restarts + 1))
        if [[ $MAX_RESTARTS -gt 0 && $restarts -ge $MAX_RESTARTS ]]; then
            echo "[kick] Max restarts ($MAX_RESTARTS) reached. Exiting."
            break
        fi

        echo "[kick] FFmpeg exited (code $EXIT_CODE). Restarting in ${RESTART_DELAY}s..."
        sleep "$RESTART_DELAY"
    done
}

echo "========================================="
echo " Kick.com Loop Streamer"
echo " Stream:  ${KICK_RTMP}/<key>"
echo " Video:   ${VIDEO_URL}"
echo " Channel: ${KICK_USERNAME:-none}"
echo "========================================="

download_video
start_follower_tracker
stream_loop

echo "[kick] Stream ended."
kill "$FOLLOWER_PID" 2>/dev/null || true
