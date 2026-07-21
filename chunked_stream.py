#!/usr/bin/env python3
"""
Download video → loop stream to Kick SRT.
No m3u8 piping. Download once, loop forever.
"""

import os
import sys
import subprocess
import signal
import time
import glob

STOP_FILE = "/tmp/kick-stream.stop"
LOG_FILE = "/tmp/kick-stream/stream.log"
DOWNLOAD_DIR = "/tmp/kick-stream/download"

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


def download_video(video_url):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    existing = glob.glob(os.path.join(DOWNLOAD_DIR, "*.mp4"))
    if existing:
        log(f"Video already downloaded: {existing[0]}")
        return existing[0]

    log(f"Downloading video: {video_url}")
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]/best",
        "--merge-output-format", "mp4",
        "-o", os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s"),
        video_url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if proc.returncode != 0:
        log(f"Download failed: {proc.stderr[:500]}")
        return None

    files = glob.glob(os.path.join(DOWNLOAD_DIR, "*.mp4"))
    if not files:
        files = glob.glob(os.path.join(DOWNLOAD_DIR, "*.*"))
    if files:
        log(f"Downloaded: {files[0]}")
        return files[0]

    log("Download completed but no file found")
    return None


def stream_video(video_file, output_url):
    global running

    while running:
        if os.path.exists(STOP_FILE):
            log("Stop file detected.")
            break

        log(f"Streaming: {video_file} -> {output_url[:60]}...")

        ff_cmd = [
            "ffmpeg", "-re",
            "-stream_loop", "-1",
            "-i", video_file,
        ]

        overlay = os.environ.get("OVERLAY_FILTER", "")
        if overlay:
            ff_cmd.extend(["-vf", overlay])

        ff_cmd.extend([
            "-c:v", "libx264", "-preset", "veryfast",
            "-tune", "zerolatency",
            "-profile:v", "high", "-level", "4.1",
            "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
            "-pix_fmt", "yuv420p",
            "-g", "60",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-max_muxing_queue_size", "1024",
            "-flush_packets", "1",
            "-y",
        ])

        if "srt://" in output_url:
            ff_cmd.extend([
                "-f", "mpegts",
                output_url,
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
                    stderr = proc.stderr.read().decode(errors="replace")[-500:]
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

        log("Restarting in 5s...")
        time.sleep(5)

    log("Stream ended.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: chunked_stream.py <video_url> <output_url>")
        sys.exit(1)

    video_url = sys.argv[1]
    output_url = sys.argv[2]

    log(f"Source: {video_url}")
    log(f"Output: {output_url}")

    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)

    video_file = download_video(video_url)
    if not video_file:
        log("ERROR: Could not download video")
        sys.exit(1)

    stream_video(video_file, output_url)
