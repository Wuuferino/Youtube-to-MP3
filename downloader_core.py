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

# YouTube retires old player-API paths regularly. Builds older than this fail
# every URL with 'Precondition check failed' / 'Please sign in'.
MIN_YT_DLP = '2025.09.26'

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


def _version_tuple(value: str):
    parts = []
    for chunk in str(value).split('.'):
        digits = ''.join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def ensure_yt_dlp(status_cb=None, min_version: str = MIN_YT_DLP):
    """Import yt-dlp, installing or upgrading it when missing or too old.

    Returns the imported module. Raises RuntimeError if it cannot be made usable.
    """
    import subprocess
    import importlib

    def report(msg):
        if status_cb:
            status_cb(msg)

    try:
        import yt_dlp
        current = yt_dlp_version(yt_dlp)
        if _version_tuple(current) >= _version_tuple(min_version):
            return yt_dlp
        report(f'yt-dlp {current} is too old (need {min_version}+); upgrading...')
    except ImportError:
        report('yt-dlp not installed; installing...')

    if getattr(sys, 'frozen', False):
        # A PyInstaller build freezes yt-dlp at build time and cannot self-update.
        raise RuntimeError(
            'yt-dlp bundled in this build is outdated and cannot update itself. '
            'Rebuild the exe after running: python -m pip install --upgrade yt-dlp'
        )

    installed = False
    for cmd in ([sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp'],
                ['py', '-m', 'pip', 'install', '--upgrade', 'yt-dlp']):
        try:
            subprocess.check_call(cmd)
            installed = True
            break
        except Exception:
            continue
    if not installed:
        raise RuntimeError(
            'Could not install yt-dlp. Run manually: python -m pip install --upgrade yt-dlp'
        )

    # Drop cached submodules so the freshly installed version is the one imported.
    importlib.invalidate_caches()
    for name in [n for n in list(sys.modules) if n == 'yt_dlp' or n.startswith('yt_dlp.')]:
        del sys.modules[name]
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError(
            'yt-dlp still unavailable after install. Run: python -m pip install --upgrade yt-dlp'
        ) from exc
    report(f'yt-dlp ready ({yt_dlp_version(yt_dlp)})')
    return yt_dlp


def yt_dlp_version(module) -> str:
    return getattr(getattr(module, 'version', None), '__version__', '0')


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
    # Search order: explicit path, PyInstaller bundle, local ./ffmpeg/bin, exe dir, PATH.
    exe_name = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p)
        if p.is_dir():
            exe = p / exe_name
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

    local = Path('ffmpeg') / 'bin' / exe_name
    if local.exists():
        return str(local)

    try:
        exe_dir = Path(getattr(sys, 'frozen', False) and sys.executable or __file__).resolve().parent
        direct = exe_dir / exe_name
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


class _YdlLogger:
    """Routes yt-dlp's own errors to the UI instead of letting them vanish."""

    def __init__(self, status_cb=None):
        self.status_cb = status_cb
        self.errors: List[str] = []

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        text = str(msg)
        if 'Please sign in' in text or 'Precondition' in text or 'not a bot' in text:
            self.errors.append(text)
            if self.status_cb:
                self.status_cb(text)

    def error(self, msg):
        text = str(msg)
        self.errors.append(text)
        if self.status_cb:
            self.status_cb(text)


def download_urls(urls: List[str], out_dir: str, controller: DownloadController, require_mp3: bool = True,
                  status_cb=None, progress_cb=None, file_cb=None):
    try:
        yt_dlp = ensure_yt_dlp(status_cb=status_cb)
    except RuntimeError as exc:
        if status_cb:
            status_cb(str(exc))
        return

    ffmpeg = detect_ffmpeg(controller.ffmpeg_path)
    if not ffmpeg and require_mp3:
        if status_cb:
            status_cb("ffmpeg missing; cannot proceed (MP3 required)")
        return

    if not ffmpeg and not require_mp3:
        if status_cb:
            status_cb("ffmpeg not found; will save original audio format")

    logger = _YdlLogger(status_cb)
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(Path(out_dir) / OUTPUT_TEMPLATE),
        # Errors must surface: silently returning None made every failure look like success.
        'ignoreerrors': False,
        # 'watch?v=X&list=Y' should yield one track, not the whole mix.
        'noplaylist': True,
        'quiet': True,
        'no_warnings': False,
        'logger': logger,
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
    failed: List[str] = []
    succeeded = 0
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
                status_cb(f"Downloading ({idx}/{total}): {url}")
            try:
                res = ydl.extract_info(url, download=True)
                if not res:
                    failed.append(url)
                    continue
                is_playlist = res.get('_type') == 'playlist'
                for entry in _iter_entries(res):
                    path = _resolve_output_path(ydl, entry, out_dir, ffmpeg)
                    # Log single videos under the URL the caller gave us, or
                    # get_missing_urls can't find them (youtu.be/X vs watch?v=X).
                    # Only playlist entries need their own key to avoid collisions.
                    key = (entry.get('webpage_url') or url) if is_playlist else url
                    mark_downloaded(key, path)
                    succeeded += 1
                    if file_cb:
                        try:
                            file_cb(idx, total, path, entry.get('title') or 'file',
                                    Path(path).suffix.lstrip('.'))
                        except Exception:
                            pass
            except Exception as e:
                if controller.stop_event.is_set():
                    break
                failed.append(url)
                if status_cb:
                    status_cb(f"Failed: {url} -- {e}")

    controller.current_url = None
    if status_cb and not controller.stop_event.is_set():
        if failed:
            status_cb(f"Done with errors: {succeeded} downloaded, {len(failed)} failed. "
                      f"First failure: {failed[0]}")
        else:
            status_cb(f"Done. {succeeded} downloaded.")


def _iter_entries(res: Dict[str, Any]):
    """Yield the individual video dicts from a single-video or playlist result."""
    if res.get('_type') == 'playlist':
        for entry in (res.get('entries') or []):
            if entry:
                yield entry
    else:
        yield res


def _resolve_output_path(ydl, entry: Dict[str, Any], out_dir: str, ffmpeg: Optional[str]) -> str:
    """Real on-disk path for an entry.

    yt-dlp sanitizes titles ('AC/DC' -> 'AC\u29f8DC'), so rebuilding the name from the
    raw title produced log paths that never matched the file that was written.
    """
    try:
        name = ydl.prepare_filename(entry)
    except Exception:
        title = entry.get('title') or 'file'
        video_id = entry.get('id') or ''
        name = str(Path(out_dir) / (f"{title}-{video_id}" if video_id else title))
    path = Path(name)
    if ffmpeg:
        path = path.with_suffix('.mp3')
    if not path.is_absolute() and path.parent == Path('.'):
        path = Path(out_dir) / path.name
    return str(path)


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
