# YouTube MP3 Bulk Downloader

A simple tool for downloading and converting multiple YouTube URLs to MP3 files. Supports batch operations and fallback to original audio formats if conversion is unavailable.

## Features
- Bulk download from a list of YouTube URLs
- Automatic conversion to MP3 (requires ffmpeg)
- GUI with progress bar and file list
- Fallback to original format if conversion fails
- Single-instance enforcement
- Customizable output directory

## Requirements
- Python 3.8+
- yt-dlp
- ffmpeg (auto-downloaded if missing)
- Pillow (for icon generation)
- tkinter (included with most Python installations)

## Installation
1. Install dependencies:
	```powershell
	python -m pip install -r requirements.txt
	```
2. (Optional) Generate pacman.ico for the GUI:
	```powershell
	python make_pacman_icon.py
	```
3. (Optional) Bundle ffmpeg for standalone exe builds.

## Usage
- Prepare a text file (`links.txt`) with one YouTube URL per line.
- Run the GUI:
	```powershell
	python gui_app.py
	```
- Or use the CLI:
	```powershell
	python bulk_mp3_downloader.py --links links.txt --output ./Music
	```
- For Windows exe, build with PyInstaller (see build_instructions_gui.txt).

## License
Copyright (c) 2025 ItzWolfy. All rights reserved.

```
Blank lines are ignored. Anything starting with `http://` or `https://` is treated as a URL.

## Notes
- Filenames are the video title + `.mp3`. If Windows complains about characters, yt-dlp will sanitize automatically.
- Duplicate titles will overwrite unless you change `outtmpl` pattern; you could include `%(id)s` to keep them unique.

To make filenames always unique, change inside the script:
```
'%(title)s.%(ext)s'
```
into for example:
```
'%(title)s-%(id)s.%(ext)s'
```

## Troubleshooting
- If conversion fails: ensure `ffmpeg` is installed and visible in PATH.
- If some URLs fail with 410/403: they may be region blocked or private.
- Update yt-dlp periodically:
```
pip install -U yt-dlp
```