#!/usr/bin/env python3
"""
Direct FFmpeg m3u8 -> SRT/RTMP pipe.
No Streamlink, no yt-dlp at runtime.
Gets m3u8 URL via yt-dlp once, then FFmpeg reads it directly.
Auto-reconnects on failure.
"""

import os
import sys
import subprocess
import signal
import time
import json
import urllib.request

STOP_FILE = "/tmp/kick-stream.stop"
LOG_FILE = "/tmp/kick-stream/stream.log"

running = True


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def signal_handler(sig, frame):
    global running
    running = False
    log("Signal received, stopping...")


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def get_stream_url(video_url):
    log(f"Resolving stream URL for: {video_url}")

    result = subprocess.run(
        ["yt-dlp", "-g", "--no-warnings", video_url],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode == 0 and result.stdout.strip():
        url = result.stdout.strip().split("\n")[0]
        log(f"Got m3u8 URL: {url[:80]}...")
        return url

    log(f"yt-dlp failed: {result.stderr[:200]}")

    try:
        result = subprocess.run(
            ["streamlink", "--stream-url", video_url, "best"],
            capture_output=True, text=True, timeout=30
        )
        url = result.stdout.strip()
        if url.startswith("http"):
            log(f"Got URL via streamlink: {url[:80]}...")
            return url
    except Exception as e:
        log(f"streamlink failed: {e}")

    return None


def stream(m3u8_url, output_url):
    global running
    attempt = 0

    while running:
        if os.path.exists(STOP_FILE):
            log("Stop file detected.")
            break

        attempt += 1
        log(f"Attempt {attempt}: FFmpeg m3u8 -> {output_url[:60]}...")

        ff_cmd = [
            "ffmpeg", "-re",
            "-rw_timeout", "15000000",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", m3u8_url,
        ]

        overlay = os.environ.get("OVERLAY_FILTER", "")
        if overlay:
            ff_cmd.extend(["-vf", overlay])

        ff_cmd.extend([
            "-c:v", "libx264", "-preset", "veryfast",
            "-tune", "zerolatency",
            "-profile:v", "high", "-level", "4.1",
            "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
            "-s", "1920x1080", "-r", "30",
            "-pix_fmt", "yuv420p",
            "-g", "60",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-max_muxing_queue_size", "1024",
            "-flush_packets", "1",
        ])

        if "srt://" in output_url:
            ff_cmd.extend([
                "-f", "mpegts",
                "srt://" + output_url.split("srt://", 1)[1],
            ])
        else:
            ff_cmd.extend([
                "-f", "flv",
                output_url,
            ])

        try:
            proc = subprocess.Popen(
                ff_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            while running:
                if os.path.exists(STOP_FILE):
                    log("Stop file detected, killing FFmpeg...")
                    proc.kill()
                    return

                if proc.poll() is not None:
                    stderr = proc.stderr.read().decode(errors="replace")[-400:]
                    log(f"FFmpeg exited {proc.returncode}: {stderr}")
                    break

                time.sleep(3)

        except Exception as e:
            log(f"Error: {e}")
            try:
                proc.kill()
            except Exception:
                pass

        if not running or os.path.exists(STOP_FILE):
            break

        log("Reconnecting in 5s...")
        time.sleep(5)

    log("Stream ended.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: streamlink_pipe.py <video_url> <output_url>")
        sys.exit(1)

    video_url = sys.argv[1]
    output_url = sys.argv[2]

    log(f"Source: {video_url}")
    log(f"Output: {output_url}")

    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)

    m3u8_url = get_stream_url(video_url)
    if not m3u8_url:
        log("ERROR: Could not resolve stream URL")
        sys.exit(1)

    stream(m3u8_url, output_url)
