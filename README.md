# StreamflixAA Local Media Server

A self-hosted media server that lets you stream your personal movie and TV show collection through the **StreamflixAA** Android app — on your phone, tablet, or Android Auto.

Join the betatest here: [streamflixaa.online](https://www.streamflixaa.online)

---

## Features

- Stream local video files (MP4, MKV, AVI, M4V, WebM, MOV) to the StreamflixAA app
- Automatic detection of movies, TV shows, episodes, and seasons
- Subtitle support (SRT, VTT, ASS, SSA, SUB)
- Cover image detection (poster, cover, folder art)
- Web-based admin UI for configuration and library management
- Folder browser for easy setup
- Auto-scan on a timer
- Works over LAN, Wi-Fi, or Tailscale/VPN

---

## Requirements

- **Python 3.10+**
- **Flask 3.0+**
- **StreamflixAA** app installed on your Android device

---

## Installation

### Option A: Run with Python

#### 1. Install Python

Download and install Python 3.10 or newer from [python.org](https://www.python.org/downloads/).  
Make sure to check **"Add Python to PATH"** during installation.

#### 2. Install dependencies

Open a terminal in the `streamflix-server` folder and run:

```bash
pip install -r requirements.txt
```

#### 3. Start the server

```bash
python server.py
```

The server will start on **port 8642** by default.  
Open `http://localhost:8642` in your browser to access the admin UI.

### Option B: Run with Docker

#### Quick start

```bash
docker build -t streamflix-server .
docker run -d \
  --name streamflix-server \
  -p 8642:8642 \
  -v /path/to/your/movies:/media/movies \
  -v /path/to/your/tvshows:/media/tvshows \
  -v ./config.json:/app/config.json \
  -e STREAMFLIX_MOVIES_DIR=/media/movies \
  -e STREAMFLIX_TVSHOWS_DIR=/media/tvshows \
  streamflix-server
```

> **Important:** Create `config.json` on the host before starting the container.  
> If the file does not exist, Docker will create a **directory** instead and the server will fail to write config.  
> You can copy `config.default.json` as your starting point:
>
> ```bash
> cp config.default.json config.json
> ```

#### Docker Compose

Create a `docker-compose.yml`:

```yaml
services:
  streamflix-server:
    build: .
    container_name: streamflix-server
    ports:
      - "8642:8642"
    volumes:
      - ./config.json:/app/config.json
      - /path/to/your/movies:/media/movies
      - /path/to/your/tvshows:/media/tvshows
    environment:
      - STREAMFLIX_MOVIES_DIR=/media/movies
      - STREAMFLIX_TVSHOWS_DIR=/media/tvshows
    restart: unless-stopped
```

Then run:

```bash
cp config.default.json config.json   # only needed on first run
docker compose up -d
```

#### Environment Variables

| Variable | Description | Example |
|---|---|---|
| `STREAMFLIX_MOVIES_DIR` | Movie folder(s) inside the container (comma-separated) | `/media/movies` |
| `STREAMFLIX_TVSHOWS_DIR` | TV show folder(s) inside the container (comma-separated) | `/media/tvshows` |
| `STREAMFLIX_PORT` | Override the server port | `8642` |
| `STREAMFLIX_SERVER_NAME` | Override the server display name | `My Media Server` |

Environment variables take precedence over `config.json` values.

---

## Configuration

All settings can be changed from the **web UI** at `http://localhost:8642` under the **Settings** tab.

| Setting | Description | Default |
|---|---|---|
| Server Name | Display name shown in the app | `My Media Server` |
| Port | HTTP port the server listens on | `8642` |
| Language | 2-letter language code (en, de, es, fr, it, etc.) | `en` |
| Auto-Scan Interval | Minutes between automatic library rescans (0 = disabled) | `0` |
| Movie Folders | Folders containing your movie files | _(empty)_ |
| TV Show Folders | Folders containing your TV show directories | _(empty)_ |
| Video Extensions | File extensions treated as video | `.mp4, .mkv, .avi, .m4v, .webm, .mov` |
| Subtitle Extensions | File extensions treated as subtitles | `.srt, .vtt, .ass, .ssa, .sub` |

Configuration is saved to `config.json` next to `server.py`.  
A `config.default.json` template is included in the repository — copy it to `config.json` on first run if one doesn't exist yet.  
When running in Docker, environment variables (`STREAMFLIX_MOVIES_DIR`, etc.) override the corresponding `config.json` values.

---

## File Structure

### Movies

Each movie should be a **video file** inside one of your configured movie folders.  
Movies can be in the root of the folder or inside individual subfolders.

**Recommended structure (subfolder per movie):**

```
Movies/
├── The Dark Knight (2008) [1080p]/
│   ├── The.Dark.Knight.2008.1080p.BluRay.x265.mkv
│   ├── The.Dark.Knight.2008.1080p.BluRay.x265.srt
│   ├── The.Dark.Knight.2008.1080p.BluRay.x265.en.srt
│   └── poster.jpg
├── Inception (2010) [4K]/
│   ├── Inception.2010.2160p.WEB-DL.x265.mp4
│   ├── Inception.2010.2160p.WEB-DL.x265.srt
│   └── poster.jpg
└── Interstellar (2014)/
    └── Interstellar.2014.1080p.BluRay.mkv
```

**Also works (flat structure):**

```
Movies/
├── The.Dark.Knight.2008.1080p.BluRay.mkv
├── Inception.2010.2160p.WEB-DL.mp4
└── Interstellar.2014.1080p.BluRay.mkv
```

### TV Shows

TV shows must be organized with **one subfolder per show** inside your configured TV show folders.  
Episodes should be in season subfolders (recommended) or directly in the show folder.

**Recommended structure:**

```
TVShows/
├── Breaking Bad/
│   ├── poster.jpg
│   ├── Season 01/
│   │   ├── Breaking.Bad.S01E01.1080p.BluRay.mkv
│   │   ├── Breaking.Bad.S01E01.srt
│   │   ├── Breaking.Bad.S01E02.1080p.BluRay.mkv
│   │   └── Breaking.Bad.S01E02.srt
│   └── Season 02/
│       ├── Breaking.Bad.S02E01.1080p.BluRay.mkv
│       └── Breaking.Bad.S02E02.1080p.BluRay.mkv
├── The Office/
│   ├── Season 1/
│   │   ├── The.Office.S01E01.720p.mkv
│   │   └── The.Office.S01E02.720p.mkv
│   └── Season 2/
│       ├── The.Office.S02E01.720p.mkv
│       └── The.Office.S02E02.720p.mkv
```

### Episode Detection

The scanner recognizes these episode naming patterns:

| Pattern | Example |
|---|---|
| `S01E01` | `Breaking.Bad.S01E01.1080p.mkv` |
| `s1e1` | `show.s1e1.mkv` |
| `1x01` | `show.1x01.mkv` |
| `Episode 01` | `Show Episode 01.mkv` |

Season folders are detected by names like `Season 01`, `Season 1`, `S01`, `S1`.

If no season/episode info is found in the filename, the server assigns Season 1 and auto-numbers episodes.

### Cover Images

The server automatically detects cover images. Place them alongside your video files:

**For movies** (checked in this order):
1. `<video-filename>.jpg` / `.png` / `.webp` (e.g., `Inception.2010.jpg`)
2. `poster.jpg` / `poster.png` / `poster.webp`
3. `cover.jpg` / `folder.jpg` / `thumb.jpg`
4. Any image file in the same folder

**For TV shows:**
1. `poster.jpg` in the show's root folder
2. `cover.jpg` / `folder.jpg` / `thumb.jpg`
3. Any image file in the show folder

### Subtitles

Subtitles are auto-detected when placed next to the video file:

| File | Detected As |
|---|---|
| `movie.srt` | SRT |
| `movie.en.srt` | EN |
| `movie.de.srt` | DE |
| `movie.vtt` | VTT |

### Server Logo

To set a custom provider icon in the app, place a file named `logo.jpg`, `logo.png`, or `logo.webp` in the same folder as `server.py`.

---

## Connecting the App

### 1. Find your server IP

The server must be reachable from your phone. You can use:

- **LAN IP** (e.g., `192.168.1.100`) — phone must be on the same Wi-Fi network
- **Tailscale IP** (e.g., `100.x.x.x`) — works from anywhere with Tailscale installed on both devices

> **Note:** `localhost` / `127.0.0.1` will NOT work — that refers to the phone itself, not your PC.

### 2. Import the provider in the app

In the StreamflixAA app, go to **Settings → Import Providers** and paste:

```json
[{"id": "localserver", "baseUrl": "http://YOUR_IP:8642", "language": "en"}]
```

Replace `YOUR_IP` with your actual server IP address and `en` with your preferred language code.

### 3. Firewall

Make sure port **8642** is allowed through your firewall.

**Windows (run as Administrator):**
```powershell
netsh advfirewall firewall add rule name="StreamflixAA Server" dir=in action=allow protocol=TCP localport=8642
```

**Linux:**
```bash
sudo ufw allow 8642/tcp
```

---

## Web Admin UI

Access the admin panel at `http://localhost:8642` with three tabs:

### Settings
- Configure server name, port, language
- Add/remove movie and TV show folders using the folder browser
- Set video and subtitle file extensions
- Save settings and trigger a library scan

### Library
- View all detected movies and TV shows
- See title, quality, and ID for each item

### Provider JSON
- Copy-paste the import JSON for the StreamflixAA app
- Shows the current server address and language

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/home` | GET | Home screen categories (featured + movies + TV shows) |
| `/api/movies?page=1` | GET | Paginated movie list |
| `/api/tvshows?page=1` | GET | Paginated TV show list |
| `/api/movie/<id>` | GET | Movie details |
| `/api/tvshow/<id>` | GET | TV show details with seasons |
| `/api/season/<id>/episodes` | GET | Episodes for a season |
| `/api/search?query=text` | GET | Search movies and TV shows |
| `/api/servers/<id>` | GET | Streaming servers for an item |
| `/api/video/<id>` | GET | Video URL for playback |
| `/api/stream/<id>` | GET | Stream video file (supports byte-range) |
| `/api/poster/<id>` | GET | Poster image for an item |
| `/api/subtitle/<id>` | GET | Subtitle file |
| `/api/scan` | POST | Trigger library scan |
| `/api/config` | GET/POST | Read/update server configuration |
| `/api/info` | GET | Server info (name, version, counts) |
| `/api/browse?path=...` | GET | Browse directories (for folder picker) |
| `/api/provider-json` | GET | Provider import JSON for the app |

---

## Troubleshooting

### App shows "Failed to connect"
- Verify the server is running (`http://localhost:8642` should load in a browser)
- Check you're using the correct IP (not `localhost`)
- Ensure the phone and PC are on the same network (or connected via Tailscale)
- Check the Windows Firewall allows port 8642

### No movies/shows found after scan
- Verify the folders are added in Settings and **saved** before scanning
- Check that your video files have supported extensions (`.mp4`, `.mkv`, etc.)
- Make sure the folder paths are correct and accessible

### Images not loading in the app
- The server must be reachable from the phone on the same IP used in the import JSON
- Try opening `http://YOUR_IP:8642/api/poster/server` in your phone's browser

### Video won't play
- Ensure the video format is supported by Android (MP4/H.264 is most compatible)
- MKV with H.265/HEVC may not play on all devices — re-encode to MP4/H.264 if needed

---

## Running as a Background Service (Optional)

### Docker (Recommended)

Using Docker with `restart: unless-stopped` (as shown in the Docker Compose example above) is the simplest way to keep the server running in the background and auto-start on boot.

### Windows

Create a batch file `start-server.bat`:
```batch
@echo off
cd /d "%~dp0"
python server.py
```

To run at startup, create a shortcut to this batch file in:  
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`

### Linux

Create a systemd service file `/etc/systemd/system/streamflixaa.service`:
```ini
[Unit]
Description=StreamflixAA Media Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/streamflix-server
ExecStart=/usr/bin/python3 server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Then enable and start:
```bash
sudo systemctl enable streamflixaa
sudo systemctl start streamflixaa
```

---

## License

This project is provided as-is for personal use.
