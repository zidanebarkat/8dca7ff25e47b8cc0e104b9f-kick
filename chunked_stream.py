#!/usr/bin/env python3
"""
Streamlink -> FFmpeg pipe.
Downloads HLS segments on-the-fly via Streamlink,
pipes directly into FFmpeg which streams to Kick.
Auto-reconnects on failure. No disk space needed for video.
"""

import os
import sys
import subprocess
import signal
import time

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


def stream(video_url, output_url):
    global running
    attempt = 0

    while running:
        if os.path.exists(STOP_FILE):
            log("Stop file detected.")
            break

        attempt += 1
        log(f"Attempt {attempt}: Streamlink -> FFmpeg -> {output_url[:50]}...")

        sl_cmd = [
            "streamlink",
            video_url, "best",
            "--stdout",
            "--twitch-disable-ads",
            "--hls-live-restart",
        ]

        ff_cmd = [
            "ffmpeg",
            "-re",
            "-i", "pipe:0",
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
            "-s", "1920x1080", "-r", "30",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-f", "mpegts",
            output_url,
        ]

        overlay = os.environ.get("OVERLAY_FILTER", "")
        if overlay:
            ff_cmd.insert(2, "-vf")
            ff_cmd.insert(3, overlay)

        try:
            sl_proc = subprocess.Popen(
                sl_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            ff_proc = subprocess.Popen(
                ff_cmd,
                stdin=sl_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            sl_proc.stdout.close()

            while running:
                if os.path.exists(STOP_FILE):
                    log("Stop file detected, killing processes...")
                    sl_proc.kill()
                    ff_proc.kill()
                    return

                if ff_proc.poll() is not None:
                    stderr = ff_proc.stderr.read().decode(errors="replace")[-300:]
                    log(f"FFmpeg exited {ff_proc.returncode}: {stderr}")
                    sl_proc.kill()
                    break

                if sl_proc.poll() is not None:
                    stderr_out = sl_proc.stderr.read().decode(errors="replace")[-200:]
                    log(f"Streamlink exited {sl_proc.returncode}: {stderr_out}")
                    ff_proc.kill()
                    break

                time.sleep(2)

        except Exception as e:
            log(f"Error: {e}")
            try:
                sl_proc.kill()
            except Exception:
                pass
            try:
                ff_proc.kill()
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

    stream(video_url, output_url)
