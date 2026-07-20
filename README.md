# Kick.com Loop Streamer

24/7 looping video stream to Kick.com with auto-restart and GitHub Actions control.

## How It Works

1. You provide a video URL (mp4/m3u8) and your Kick stream key
2. The video is downloaded once, then looped endlessly to Kick via FFmpeg
3. If FFmpeg crashes, it auto-restarts after 5 seconds
4. Stream runs until you stop it from GitHub Actions or the web panel

## Option 1: GitHub Actions (Recommended)

### Setup

1. Push this repo to GitHub
2. Go to **Actions** > **Kick.com Loop Streamer** > **Run workflow**
3. Fill in:
   - **action**: `start`
   - **stream_key**: Your Kick stream key
   - **video_url**: Direct link to mp4 or m3u8
   - **retrigger_hours**: `5` (auto-retriggers before 6h timeout)

### Stop the stream

Go to **Actions** > click the running workflow > **Cancel run**

Or trigger the workflow with action = `stop`.

### Self-Retrigger

GitHub Actions jobs have a 6-hour max. The workflow auto-retriggers itself every N hours (default 5) to keep streaming indefinitely. Set `retrigger_hours` to `0` to disable.

## Option 2: Docker

```bash
cp .env.example .env
# Edit .env with your settings

docker build -t kick-streamer .
docker run -d --name kick-streamer \
  -p 8080:8080 \
  -e PANEL_PASSWORD=yourpassword \
  --restart unless-stopped \
  kick-streamer
```

Open `http://localhost:8080` for the web panel.

## Option 3: Direct

```bash
pip install -r requirements.txt
export KICK_STREAM_KEY=your-key
export PANEL_PASSWORD=admin
python app.py
```

Or just run the shell script directly:

```bash
bash restream.sh YOUR_STREAM_KEY https://example.com/video.mp4
```

## FFmpeg Settings

| Setting | Value |
|---------|-------|
| Codec | H.264 (libx264) |
| Preset | veryfast |
| Video bitrate | 4500 kbps |
| Resolution | 1920x1080 |
| FPS | 30 |
| Audio | AAC 128kbps |
| Container | FLV (RTMP) |

## Files

| File | Purpose |
|------|---------|
| `restream.sh` | Core FFmpeg loop script with auto-restart |
| `app.py` | Flask web panel with start/stop control |
| `Dockerfile` | Container image |
| `.github/workflows/kick-stream.yml` | GitHub Actions workflow |
