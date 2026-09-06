# YouTube MP3 Bulk Downloader

A tool for downloading and converting many YouTube URLs to MP3 in one batch.
Handles YouTube's rate limiting, resumes where it left off, and falls back to the
original audio format when conversion isn't available.

## Features
- Bulk download from a list of YouTube URLs
- Automatic conversion to MP3 (requires ffmpeg)
- **Rate-limit aware** — paces requests so YouTube doesn't throttle you
- **Resumable** — re-running skips anything already downloaded
- Automatic backoff and retry when YouTube rate-limits you
- Deduplicates links that point at the same video
- GUI with progress bar and file list
- Single-instance enforcement

## Requirements
- Python 3.8+
- yt-dlp (2025.09.26 or newer — older builds fail with "Please sign in")
- ffmpeg
- Pillow (for icon generation)
- tkinter (included with most Python installations)
- Optional: `deno` or `node`, used for YouTube's JS challenges

## Installation
```powershell
python -m pip install -r requirements.txt
```

The tool checks yt-dlp's version on every run and upgrades it automatically if
it's too old, so you shouldn't hit the "Please sign in" failure. If you run a
PyInstaller `.exe`, yt-dlp is frozen at build time and cannot self-update —
rebuild the exe periodically.

## Usage

Put one YouTube URL per line in `links.txt`. Blank lines and lines starting with
`#` are ignored.

GUI:
```powershell
python gui_app.py
```

CLI:
```powershell
python bulk_mp3_downloader.py --input links.txt --output ./Music
```

Useful CLI flags:

| Flag | Meaning |
| --- | --- |
| `--input` | Links file (default `links.txt`) |
| `--output` | Output folder (default `~/Music/YouTube MP3`) |
| `--max N` | Only process the first N links |
| `--sleep` / `--max-sleep` | Seconds to wait between videos (default 3–8) |
| `--redownload` | Ignore the log and fetch everything again |
| `--ffmpeg` | Explicit path to ffmpeg |
| `--auto-ffmpeg` | Download a Windows ffmpeg build if missing |
| `--require-mp3` | Abort if MP3 conversion isn't possible |

## About rate limiting

YouTube limits how much one IP can pull in an hour. Requesting as fast as
possible trips that limiter, after which throughput collapses to a trickle. This
tool deliberately waits 3–8 seconds between videos, which sounds slower but
finishes a large batch faster because it stays under the limit.

If you do get rate-limited, the downloader waits (2 minutes, then 4, then 8) and
retries rather than burning through the rest of your list and marking everything
failed.

For very large batches, expect it to take a while and just leave it running.
Progress is saved to `download_log.json` after every track, so you can stop it
at any point and re-run to pick up where you left off.

## Notes
- Filenames are `title-videoID.mp3`. The video ID keeps same-titled tracks from
  overwriting each other.
- A `watch?v=X&list=Y` link downloads just video X, not the whole playlist. A
  plain `/playlist?list=Y` link still downloads the full playlist.

## Troubleshooting
- **"Please sign in" / HTTP 400** — yt-dlp is too old. Run
  `pip install -U yt-dlp`. If you're on a built `.exe`, rebuild it.
- **Only a handful download per hour** — you're rate-limited. Raise `--sleep`
  and `--max-sleep` and leave it running.
- **Conversion fails / WebM files left over** — ffmpeg isn't on PATH. Install it
  or pass `--ffmpeg`. The GUI has a "Convert Leftovers" button.
- **Some URLs fail with 403/410** — those videos are private, removed, or region
  blocked.

## Legal

Downloading content you don't own may violate YouTube's Terms of Service and
copyright law in your country. Use this on content you have the rights to, such
as your own uploads or material under a permissive licence. You are responsible
for how you use it.

## License
Copyright (c) 2025 ItzWolfy. All rights reserved.
