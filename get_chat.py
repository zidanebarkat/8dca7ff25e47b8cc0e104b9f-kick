#!/usr/bin/env python3
"""
Fetch live Kick.com chat messages via Pusher WebSocket.
Writes the last N messages to a file for FFmpeg to overlay on the stream.
"""

import json
import sys
import time
import threading
import urllib.request
import urllib.error

try:
    import websocket
except ImportError:
    print("[chat] websocket-client not installed, falling back to HTTP polling")
    websocket = None

PUSHER_KEY = "cbdc36f3b87bbe573c1d"
PUSHER_CLUSTER = "us2"
MAX_MESSAGES = 8
UPDATE_INTERVAL = 2
KICK_API = "https://kick.com/api/v2/channels/{slug}"
FALLBACK_FILE = "/tmp/kick-stream/chat_messages.txt"

messages_lock = threading.Lock()
messages = []


def fetch_chatroom_id(slug: str) -> int | None:
    url = KICK_API.format(slug=slug)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            chatroom = data.get("chatroom", {})
            return chatroom.get("id")
    except Exception as e:
        print(f"[chat] Failed to fetch chatroom ID: {e}", file=sys.stderr)
    return None


def fetch_recent_messages(slug: str) -> list:
    url = f"https://kick.com/api/v2/channels/{slug}/messages"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            result = data.get("data", [])
            msgs = []
            for m in reversed(result):
                sender = m.get("sender", {}).get("username", "???")
                content = m.get("content", "")
                if content:
                    msgs.append(f"{sender}: {content}")
            return msgs
    except Exception as e:
        print(f"[chat] Failed to fetch recent messages: {e}", file=sys.stderr)
    return []


def write_messages(filepath: str):
    with messages_lock:
        lines = messages[-MAX_MESSAGES:]
    with open(filepath, "w") as f:
        f.write("\n".join(lines))


def on_ws_message(ws, message):
    try:
        data = json.loads(message)
        event = data.get("event", "")
        if event == "App\\Events\\ChatMessageEvent":
            payload = json.loads(data.get("data", "{}"))
            sender = payload.get("sender", {}).get("username", "???")
            content = payload.get("content", "")
            if content:
                with messages_lock:
                    messages.append(f"{sender}: {content}")
                    if len(messages) > MAX_MESSAGES * 3:
                        messages[:] = messages[-MAX_MESSAGES * 2:]
    except Exception:
        pass


def on_ws_error(ws, error):
    print(f"[chat] WebSocket error: {error}", file=sys.stderr)


def on_ws_close(ws, code, msg):
    print(f"[chat] WebSocket closed: {code} {msg}")


def on_ws_open(ws):
    print("[chat] WebSocket connected")


def connect_websocket(chatroom_id: int, outfile: str):
    import hashlib
    import hmac

    channel_name = f"chatrooms.{chatroom_id}.v2"
    ws_url = f"wss://{PUSHER_CLUSTER}-{PUSHER_KEY}.pusher.com/app/{PUSHER_KEY}?client=js&version=7.0.6&protocol=7"

    print(f"[chat] Connecting to chatroom {chatroom_id}...")
    ws = websocket.WebSocketApp(
        ws_url,
        on_message=on_ws_message,
        on_error=on_ws_error,
        on_close=on_ws_close,
        on_open=on_ws_open,
    )

    def run():
        while True:
            try:
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                print(f"[chat] WebSocket crashed: {e}", file=sys.stderr)
            time.sleep(3)
            print("[chat] Reconnecting...")

    t = threading.Thread(target=run, daemon=True)
    t.start()

    while True:
        write_messages(outfile)
        time.sleep(UPDATE_INTERVAL)


def poll_http(slug: str, outfile: str):
    print(f"[chat] HTTP polling mode for: {slug}")
    seen = set()
    while True:
        try:
            new_msgs = fetch_recent_messages(slug)
            with messages_lock:
                for m in new_msgs:
                    if m not in seen:
                        messages.append(m)
                        seen.add(m)
                        if len(messages) > MAX_MESSAGES * 3:
                            messages.pop(0)
                            if len(seen) > MAX_MESSAGES * 5:
                                seen = set(messages[-MAX_MESSAGES:])
        except Exception as e:
            print(f"[chat] Poll error: {e}", file=sys.stderr)
        write_messages(outfile)
        time.sleep(UPDATE_INTERVAL)


def main():
    if len(sys.argv) < 2:
        print("Usage: get_chat.py <kick_username> [output_file]", file=sys.stderr)
        sys.exit(1)

    slug = sys.argv[1]
    outfile = sys.argv[2] if len(sys.argv) > 2 else FALLBACK_FILE

    with open(outfile, "w") as f:
        f.write("")

    chatroom_id = fetch_chatroom_id(slug)

    if websocket and chatroom_id:
        connect_websocket(chatroom_id, outfile)
    elif chatroom_id:
        print("[chat] websocket-client not available, using HTTP polling")
        poll_http(slug, outfile)
    else:
        print("[chat] Could not get chatroom ID, using HTTP polling")
        poll_http(slug, outfile)


if __name__ == "__main__":
    main()
