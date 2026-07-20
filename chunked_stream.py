#!/usr/bin/env python3
"""
Chunked download + stream pipeline.
Downloads 30-min chunks of a Twitch VOD via Streamlink,
streams each chunk via FFmpeg while pre-fetching the next.
Zero gaps between chunks.
"""

import os
import sys
import subprocess
import threading
import time
import json
import signal
from pathlib import Path
from collections import deque

CHUNK_MINUTES = 30
CHUNK_SECONDS = CHUNK_MINUTES * 60
CHUNK_DIR = Path("/tmp/kick-stream/chunks")
STOP_FILE = Path("/tmp/kick-stream.stop")
FOLLOWER_FILE = Path("/tmp/kick-stream/follower_count.txt")
CHAT_FILE = Path("/tmp/kick-stream/chat_messages.txt")
OUTPUT_URL = os.environ.get("OUTPUT_URL", "")
OVERLAY_FILTER = os.environ.get("OVERLAY_FILTER", "")
LOG_FILE = Path("/tmp/kick-stream/stream.log")

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
    log("Signal received, stopping...")
    running = False


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def get_hls_playlist_url(video_url):
    log(f"Getting HLS playlist via Streamlink for: {video_url}")
    try:
        result = subprocess.run(
            ["streamlink", "--stream-url", video_url, "best"],
            capture_output=True, text=True, timeout=30
        )
        url = result.stdout.strip()
        if url and url.startswith("http"):
            log(f"Got playlist URL: {url[:80]}...")
            return url
        log(f"Streamlink output: {result.stdout} {result.stderr}")
    except Exception as e:
        log(f"Streamlink failed: {e}")
    return None


def get_vod_segments(playlist_url):
    log("Fetching HLS playlist to list segments...")
    try:
        import urllib.request
        req = urllib.request.Request(playlist_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode()

        segments = []
        base_url = playlist_url.rsplit("/", 1)[0] + "/"
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("http"):
                segments.append(base_url + line)
            elif line.startswith("http") and ".ts" in line:
                segments.append(line)

        log(f"Found {len(segments)} segments in playlist")
        return segments
    except Exception as e:
        log(f"Failed to parse playlist: {e}")
    return []


def download_chunk(segments, start_idx, count, chunk_num):
    chunk_file = CHUNK_DIR / f"chunk_{chunk_num:04d}.ts"
    if chunk_file.exists() and chunk_file.stat().st_size > 0:
        log(f"Chunk {chunk_num} already exists, skipping download")
        return chunk_file

    end_idx = min(start_idx + count, len(segments))
    chunk_segs = segments[start_idx:end_idx]
    if not chunk_segs:
        return None

    log(f"Downloading chunk {chunk_num}: segments {start_idx}-{end_idx - 1} ({len(chunk_segs)} segments)")

    list_file = CHUNK_DIR / f"list_{chunk_num:04d}.txt"
    with open(list_file, "w") as f:
        for seg in chunk_segs:
            f.write(f"file '{seg}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(chunk_file)
    ]

    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=True)
        if chunk_file.exists() and chunk_file.stat().st_size > 0:
            log(f"Chunk {chunk_num} downloaded: {chunk_file.stat().st_size / 1024 / 1024:.1f} MB")
            return chunk_file
    except Exception as e:
        log(f"Chunk download failed: {e}")

    return None


def stream_chunk(chunk_file, is_first=False):
    global running
    log(f"Streaming: {chunk_file.name}")

    cmd = ["ffmpeg", "-re"]
    if not is_first:
        cmd.append("-reinit_filter")
        cmd.append("0")
    cmd.extend(["-i", str(chunk_file)])

    if OVERLAY_FILTER:
        cmd.extend(["-vf", OVERLAY_FILTER])

    cmd.extend([
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
        "-s", "1920x1080", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-f", "mpegts",
        OUTPUT_URL
    ])

    log(f"FFmpeg cmd: {' '.join(cmd[:6])}...")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    while proc.poll() is None and running:
        if STOP_FILE.exists():
            log("Stop file detected, killing FFmpeg...")
            proc.kill()
            return False
        time.sleep(1)

    exit_code = proc.returncode
    if exit_code == 0:
        log(f"Chunk {chunk_file.name} streamed OK")
        return True
    else:
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        log(f"FFmpeg exited {exit_code}: {stderr[-200:]}")
        return False


def download_chunk_async(segments, start_idx, count, chunk_num, result_dict):
    chunk_file = download_chunk(segments, start_idx, count, chunk_num)
    result_dict["file"] = chunk_file


def main():
    global running

    video_url = sys.argv[1] if len(sys.argv) > 1 else ""
    output_url = sys.argv[2] if len(sys.argv) > 2 else ""

    if not video_url or not output_url:
        print("Usage: chunked_stream.py <video_url> <srt_or_rtmp_url>")
        sys.exit(1)

    os.environ["OUTPUT_URL"] = output_url
    global OUTPUT_URL
    OUTPUT_URL = output_url

    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    log("Cleaning old chunks...")
    for f in CHUNK_DIR.glob("chunk_*.ts"):
        f.unlink()
    for f in CHUNK_DIR.glob("list_*.txt"):
        f.unlink()

    playlist_url = get_hls_playlist_url(video_url)
    if not playlist_url:
        log("ERROR: Could not get HLS playlist URL")
        sys.exit(1)

    segments = get_vod_segments(playlist_url)
    if not segments:
        log("ERROR: No segments found, falling back to direct Streamlink pipe")
        stream_direct(video_url, output_url)
        return

    segs_per_chunk = len(segments) // max(1, len(segments) // (CHUNK_SECONDS // 2)) if len(segments) > 10 else len(segments)
    if segs_per_chunk < 1:
        segs_per_chunk = 1

    total_chunks = (len(segments) + segs_per_chunk - 1) // segs_per_chunk
    log(f"Total segments: {len(segments)}, per chunk: {segs_per_chunk}, total chunks: ~{total_chunks}")

    downloaded_chunks = deque()
    current_seg = 0
    chunk_num = 0
    first_chunk = True

    prefetch = {}
    t = threading.Thread(
        target=download_chunk_async,
        args=(segments, current_seg, segs_per_chunk, chunk_num, prefetch),
        daemon=True
    )
    t.start()
    t.join()
    current_seg += segs_per_chunk
    chunk_num += 1

    while running and not STOP_FILE.exists():
        chunk_file = prefetch.get("file")
        if not chunk_file:
            log("Chunk download failed, retrying...")
            time.sleep(5)
            prefetch = {}
            t = threading.Thread(
                target=download_chunk_async,
                args=(segments, current_seg - segs_per_chunk, segs_per_chunk, chunk_num - 1, prefetch),
                daemon=True
            )
            t.start()
            t.join()
            continue

        next_prefetch = {}
        if current_seg < len(segments):
            t = threading.Thread(
                target=download_chunk_async,
                args=(segments, current_seg, segs_per_chunk, chunk_num, next_prefetch),
                daemon=True
            )
            t.start()
            current_seg += segs_per_chunk
            chunk_num += 1

        success = stream_chunk(chunk_file, is_first=first_chunk)
        first_chunk = False

        if chunk_file.exists():
            chunk_file.unlink()
            list_file = CHUNK_DIR / chunk_file.name.replace("chunk_", "list_").replace(".ts", ".txt")
            if list_file.exists():
                list_file.unlink()

        if not success and not running:
            break

        if not next_prefetch.get("file") and current_seg >= len(segments):
            log("All chunks streamed!")
            break

        t = threading.Thread(target=lambda: next_prefetch.get("file"), daemon=True)
        prefetch = next_prefetch

    log("Stream ended.")


def stream_direct(video_url, output_url):
    log("Direct Streamlink -> FFmpeg pipe mode")
    cmd = [
        "streamlink", video_url, "best",
        "--stdout", "--twitch-disable-ads"
    ]
    log("Starting Streamlink pipe...")
    source = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    ff_cmd = [
        "ffmpeg", "-re", "-i", "pipe:0",
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
        "-s", "1920x1080", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-f", "mpegts",
        output_url
    ]
    proc = subprocess.Popen(ff_cmd, stdin=source.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    while running and not STOP_FILE.exists():
        if proc.poll() is not None:
            log(f"FFmpeg exited {proc.returncode}, restarting...")
            time.sleep(5)
            source = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ff_cmd[2] = "pipe:0"
            proc = subprocess.Popen(ff_cmd, stdin=source.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(1)

    source.kill()
    proc.kill()
    log("Direct stream ended.")


if __name__ == "__main__":
    main()
