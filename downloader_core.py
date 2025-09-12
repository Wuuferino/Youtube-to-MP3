import os
import json
import threading
import time
import shutil
from pathlib import Path
from typing import List, Optional, Dict, Any
import urllib.request
import zipfile
import io
import sys

LOG_FILE = 'download_log.json'
OUTPUT_TEMPLATE = '%(title)s-%(id)s.%(ext)s'

class DownloadController:
    def __init__(self, ffmpeg_path: Optional[str] = None):
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.current_url: Optional[str] = None
        self.ffmpeg_path = ffmpeg_path

    def pause(self):
        self.pause_event.set()

    def resume(self):
        self.pause_event.clear()

    def stop(self):
        self.stop_event.set()

    def reset(self):
        self.stop_event.clear()
        self.pause_event.clear()

    def wait_if_paused(self):
        while self.pause_event.is_set() and not self.stop_event.is_set():
            time.sleep(0.3)


def ensure_yt_dlp():
    try:
        import yt_dlp 
    except ImportError:
        import subprocess, sys
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yt-dlp'])


def auto_download_ffmpeg(target_root: Path) -> Optional[str]:
    url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
    try:
        with urllib.request.urlopen(url, timeout=180) as resp:
            data = resp.read()
        z = zipfile.ZipFile(io.BytesIO(data))
        out_dir = target_root / 'ffmpeg'
        bin_dir = out_dir / 'bin'
        bin_dir.mkdir(parents=True, exist_ok=True)
        ff_path = None
        for name in z.namelist():
            if name.endswith('bin/ffmpeg.exe'):
                with z.open(name) as src, open(bin_dir / 'ffmpeg.exe', 'wb') as dst:
                    dst.write(src.read())
                ff_path = str(bin_dir / 'ffmpeg.exe')
            elif name.endswith('bin/ffprobe.exe'):
                with z.open(name) as src, open(bin_dir / 'ffprobe.exe', 'wb') as dst:
                    dst.write(src.read())
        return ff_path
    except Exception: 
        return None


def detect_ffmpeg(explicit: Optional[str] = None) -> Optional[str]:
    # ...existing code...
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p)
        if p.is_dir():
            exe = p / 'ffmpeg.exe'
            if exe.exists():
                return str(exe)

    base = getattr(sys, '_MEIPASS', None)
    if base:
        pyi_ff = Path(base) / 'ffmpeg' / 'bin' / 'ffmpeg.exe'
        if pyi_ff.exists():
            return str(pyi_ff)
        pyi_root_ff = Path(base) / 'ffmpeg.exe'
        if pyi_root_ff.exists():
            return str(pyi_root_ff)

    local = Path('ffmpeg') / 'bin' / 'ffmpeg.exe'
    if local.exists():
        return str(local)

    try:
        exe_dir = Path(getattr(sys, 'frozen', False) and sys.executable or __file__).resolve().parent
        direct = exe_dir / 'ffmpeg.exe'
        if direct.exists():
            return str(direct)
    except Exception: 
        pass

    path_lookup = shutil.which('ffmpeg')
    if path_lookup:
        return path_lookup
    return None


def convert_leftovers(out_dir: str, ffmpeg_path: Optional[str] = None, status_cb=None) -> int:
    """Convert existing .webm/.m4a audio files in out_dir to mp3 using ffmpeg.
    Returns number of files converted.
    """
    ffmpeg = detect_ffmpeg(ffmpeg_path)
    if not ffmpeg:
        if status_cb:
            status_cb('ffmpeg not found for conversion')
        return 0
    exts = {'.webm', '.m4a', '.opus'}
    count = 0
    for p in Path(out_dir).glob('*'):
        if p.suffix.lower() in exts:
            mp3_target = p.with_suffix('.mp3')
            if mp3_target.exists():
                continue
            cmd = [ffmpeg, '-y', '-i', str(p), '-vn', '-ar', '44100', '-ac', '2', '-b:a', '192k', str(mp3_target)]
            try:
                import subprocess
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                count += 1
            except Exception:
                if status_cb:
                    status_cb(f'Failed converting {p.name}')
    if status_cb:
        status_cb(f'Converted {count} leftover files to mp3')
    return count


def load_urls(file_path: str) -> List[str]:
    urls: List[str] = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            for part in line.split():
                if part.startswith('http://') or part.startswith('https://'):
                    urls.append(part)
    seen = set()
    unique = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def load_log() -> Dict[str, Any]:
    if not Path(LOG_FILE).exists():
        return {}
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_log(data: Dict[str, Any]):
    with open(LOG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def mark_downloaded(url: str, filepath: str):
    log = load_log()
    log[url] = {'file': filepath, 'timestamp': time.time()}
    save_log(log)


def get_missing_urls(urls: List[str], out_dir: str) -> List[str]:
    log = load_log()
    missing = []
    for u in urls:
        entry = log.get(u)
        if entry:
            if not Path(entry['file']).exists():
                missing.append(u)
        else:
            missing.append(u)
    return missing


def download_urls(urls: List[str], out_dir: str, controller: DownloadController, require_mp3: bool = True,
                  status_cb=None, progress_cb=None, file_cb=None):
    ensure_yt_dlp()
    import yt_dlp

    ffmpeg = detect_ffmpeg(controller.ffmpeg_path)
    if not ffmpeg and require_mp3:
        if status_cb:
            status_cb("ffmpeg missing; cannot proceed (MP3 required)")
        return

    if not ffmpeg and not require_mp3:
        if status_cb:
            status_cb("ffmpeg not found; will save original audio format")

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(Path(out_dir) / OUTPUT_TEMPLATE),
        'ignoreerrors': True,
        'noplaylist': False,
        'quiet': True,
        'progress_hooks': [
            lambda d: _progress_hook(d, controller, status_cb, progress_cb)
        ],
    }
    if ffmpeg:
        ydl_opts['ffmpeg_location'] = str(Path(ffmpeg).parent)
        ydl_opts['postprocessors'] = [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'},
            {'key': 'FFmpegMetadata'}
        ]

    Path(out_dir).mkdir(parents=True, exist_ok=True)

    total = len(urls)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for idx, url in enumerate(urls, start=1):
            if controller.stop_event.is_set():
                if status_cb:
                    status_cb("Stopped.")
                break
            controller.current_url = url
            controller.wait_if_paused()
            if controller.stop_event.is_set():
                break
            if status_cb:
                status_cb(f"Downloading ({idx}/{len(urls)}): {url}")
            try:
                res = ydl.extract_info(url, download=True)
                if res:
                    ext = 'mp3' if ffmpeg else res.get('ext', 'm4a')
                    title = res.get('title') or 'file'
                    video_id = res.get('id') or ''
                    filename = f"{title}-{video_id}.{ext}" if video_id else f"{title}.{ext}"
                    full_path = str(Path(out_dir) / filename)
                    mark_downloaded(url, full_path)
                    if file_cb:
                        try:
                            file_cb(idx, total, full_path, title, ext)
                        except Exception:
                            pass
            except Exception as e:
                if status_cb:
                    status_cb(f"Error: {e}")
                if controller.stop_event.is_set():
                    break

    controller.current_url = None
    if status_cb and not controller.stop_event.is_set():
        status_cb("Done.")


def _progress_hook(d, controller: DownloadController, status_cb, progress_cb):
    if controller.stop_event.is_set():
        raise Exception('Stopped by user') 
    controller.wait_if_paused()
    if d.get('status') == 'downloading' and progress_cb:
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
        downloaded = d.get('downloaded_bytes') or 0
        pct = (downloaded / total * 100) if total else 0
        speed = d.get('speed') or 0
        progress_cb(pct, downloaded, total, speed)
    elif d.get('status') == 'finished' and status_cb:
        status_cb('Processing...')
