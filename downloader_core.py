import io
import json
import os
import queue
import random
import shutil
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG_FILE = 'download_log.json'
OUTPUT_TEMPLATE = '%(title)s-%(id)s.%(ext)s'

# YouTube retires old player-API paths regularly. Builds older than this fail
# every URL with 'Precondition check failed' / 'Please sign in'.
MIN_YT_DLP = '2025.09.26'

# YouTube rate-limits bursts per IP. Pausing between videos keeps you under the
# limiter, which nets far more downloads per hour than hammering it and getting
# throttled down to a trickle.
SLEEP_MIN = 3
SLEEP_MAX = 8
SLEEP_REQUESTS = 1.0
# Videos fetched in parallel. The limiter cares about request rate, so this
# trades against SLEEP_*: a few workers with pacing beats one worker sprinting.
DEFAULT_WORKERS = 4
MAX_WORKERS = 8
# Fragments of a single video fetched in parallel. Cheap speedup: it does not
# raise the number of distinct videos requested per minute.
FRAGMENT_CONCURRENCY = 3
# How long to wait out a hard rate-limit before retrying the same URL.
BACKOFF_START = 120
BACKOFF_MAX = 900
BACKOFF_RETRIES = 3

_RATE_LIMIT_MARKERS = (
    '429', 'too many requests', 'sign in to confirm', 'not a bot',
    'rate limit', 'temporarily blocked', 'try again later',
)

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
    import importlib
    import subprocess

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
    # Absolute paths: with out_dir '.', a title starting with '-' would otherwise reach ffmpeg as an option
    for p in Path(out_dir).resolve().glob('*'):
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


def video_key(url: str) -> str:
    """Identity for dedupe: youtu.be/X and watch?v=X are the same video.

    Fetching both wastes a slot in YouTube's hourly budget and writes the same
    file twice.
    """
    from urllib.parse import parse_qs, urlparse
    try:
        parsed = urlparse(url)
        if 'youtu.be' in parsed.netloc:
            return parsed.path.strip('/') or url
        return parse_qs(parsed.query).get('v', [None])[0] or url
    except Exception:
        return url


def load_urls(file_path: str) -> List[str]:
    urls: List[str] = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            for part in line.split():
                if part.startswith(('http://', 'https://')):
                    urls.append(part)
    seen = set()
    unique = []
    for u in urls:
        key = video_key(u)
        if key not in seen:
            seen.add(key)
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


_LOG_LOCK = threading.Lock()


def mark_downloaded(url: str, filepath: str):
    # Workers run in parallel and this is read-modify-write on one file.
    with _LOG_LOCK:
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


def detect_js_runtime() -> Optional[str]:
    """Name of an available JS runtime for yt-dlp's signature challenges.

    yt-dlp only enables deno by default, so node/bun must be opted into. We only
    return names found on PATH, so yt-dlp can locate the binary itself.
    """
    for name in ('deno', 'node', 'bun'):
        if shutil.which(name):
            return name
    return None


def _is_rate_limited(text) -> bool:
    low = str(text).lower()
    return any(marker in low for marker in _RATE_LIMIT_MARKERS)


def _interruptible_sleep(seconds: float, controller: 'DownloadController') -> bool:
    """Sleep in slices so Stop stays responsive. False if stopped."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if controller.stop_event.is_set():
            return False
        controller.wait_if_paused()
        time.sleep(min(0.5, max(0.0, deadline - time.time())))
    return not controller.stop_event.is_set()


class _Pacer:
    """Adaptive delay shared by all workers.

    A fixed sleep is either too slow (wasting hours) or too fast (tripping the
    limiter). This starts at full speed and only backs off when YouTube actually
    pushes back, then creeps forward again once things are healthy, so a batch
    runs at whatever rate the limiter really allows.
    """

    def __init__(self, sleep_min: float, sleep_max: float):
        self._lock = threading.Lock()
        self._min = max(0.0, sleep_min)
        self._max = max(self._min, sleep_max)
        self._factor = 1.0
        self._cooldown_until = 0.0
        self._ok_streak = 0

    def next_delay(self) -> float:
        with self._lock:
            return random.uniform(self._min * self._factor, self._max * self._factor)

    def cooldown_remaining(self) -> float:
        with self._lock:
            return max(0.0, self._cooldown_until - time.time())

    def penalize(self, seconds: float) -> float:
        """Rate-limited: slow everyone down and park the whole pool briefly."""
        with self._lock:
            self._factor = min(self._factor * 2, 12.0)
            self._ok_streak = 0
            self._cooldown_until = max(self._cooldown_until, time.time() + seconds)
            return self._factor

    def reward(self):
        """Sustained success: give some speed back."""
        with self._lock:
            self._ok_streak += 1
            if self._ok_streak >= 5 and self._factor > 1.0:
                self._factor = max(1.0, self._factor / 1.5)
                self._ok_streak = 0


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
                  status_cb=None, progress_cb=None, file_cb=None,
                  sleep_min: float = SLEEP_MIN, sleep_max: float = SLEEP_MAX,
                  skip_existing: bool = True, workers: int = DEFAULT_WORKERS):
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

    if not ffmpeg and not require_mp3 and status_cb:
        status_cb("ffmpeg not found; will save original audio format")

    workers = max(1, min(int(workers or 1), MAX_WORKERS))
    logger = _YdlLogger(status_cb)
    js_runtime = detect_js_runtime()
    if status_cb and not js_runtime:
        status_cb('No JS runtime (deno/node) found; some formats may be unavailable.')

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
        # Pacing is handled by _Pacer so it can adapt; yt-dlp's own per-video
        # sleep would stack on top of it.
        'sleep_interval': 0,
        'max_sleep_interval': 0,
        'sleep_interval_requests': SLEEP_REQUESTS,
        'concurrent_fragment_downloads': FRAGMENT_CONCURRENCY,
        'retries': 10,
        'extractor_retries': 5,
    }
    if js_runtime:
        # API shape is {runtime: config}. The config must be a dict, not None:
        # None passes validation and then fails with AttributeError downstream.
        ydl_opts['js_runtimes'] = {js_runtime: {}}
    if ffmpeg:
        ydl_opts['ffmpeg_location'] = str(Path(ffmpeg).parent)
        ydl_opts['postprocessors'] = [
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'},
            {'key': 'FFmpegMetadata'}
        ]

    Path(out_dir).mkdir(parents=True, exist_ok=True)

    # Resume: a long run will be interrupted, and re-fetching what is already on
    # disk wastes the hourly budget.
    skipped = 0
    pending: List[str] = []
    done_log = load_log() if skip_existing else {}
    for url in urls:
        entry_log = done_log.get(url)
        if entry_log and Path(entry_log.get('file', '')).exists():
            skipped += 1
        else:
            pending.append(url)
    if skipped and status_cb:
        status_cb(f"Skipping {skipped} already downloaded; {len(pending)} to fetch.")

    work: queue.Queue = queue.Queue()
    for item in enumerate(pending, start=1):
        work.put(item)

    pacer = _Pacer(sleep_min, sleep_max)
    lock = threading.Lock()
    stats = {'succeeded': 0, 'failed': [], 'done': 0}
    n_pending = len(pending)

    # With several videos in flight, a single percentage is meaningless; let the
    # bar advance on completions instead so it moves forward monotonically.
    per_file_progress = progress_cb if workers == 1 else None

    def wait_turn() -> bool:
        cooldown = pacer.cooldown_remaining()
        if cooldown > 0 and not _interruptible_sleep(cooldown, controller):
            return False
        return _interruptible_sleep(pacer.next_delay(), controller)

    def handle(ydl, url: str):
        backoff = BACKOFF_START
        for attempt in range(BACKOFF_RETRIES + 1):
            if controller.stop_event.is_set():
                return
            try:
                res = ydl.extract_info(url, download=True)
                if not res:
                    with lock:
                        stats['failed'].append(url)
                    return
                is_playlist = res.get('_type') == 'playlist'
                for entry in _iter_entries(res):
                    path = _resolve_output_path(ydl, entry, out_dir, ffmpeg)
                    # Log single videos under the URL the caller gave us, or
                    # get_missing_urls can't find them (youtu.be/X vs watch?v=X).
                    # Only playlist entries need their own key to avoid collisions.
                    key = (entry.get('webpage_url') or url) if is_playlist else url
                    mark_downloaded(key, path)
                    with lock:
                        stats['succeeded'] += 1
                    if file_cb:
                        try:
                            file_cb(stats['succeeded'], n_pending, path,
                                    entry.get('title') or 'file', Path(path).suffix.lstrip('.'))
                        except Exception:
                            pass
                pacer.reward()
                return
            except Exception as e:
                if controller.stop_event.is_set():
                    return
                # A hard rate-limit is worth waiting out; anything else is a
                # per-video problem and retrying it just burns quota.
                if _is_rate_limited(e) and attempt < BACKOFF_RETRIES:
                    factor = pacer.penalize(backoff)
                    if status_cb:
                        status_cb(f"Rate limited; slowing down (x{factor:.0f}) and "
                                  f"waiting {backoff}s before retry "
                                  f"{attempt + 1}/{BACKOFF_RETRIES}...")
                    if not _interruptible_sleep(backoff, controller):
                        return
                    backoff = min(backoff * 2, BACKOFF_MAX)
                    continue
                with lock:
                    stats['failed'].append(url)
                if status_cb:
                    status_cb(f"Failed: {url} -- {e}")
                return

    def worker():
        opts = dict(ydl_opts)
        if per_file_progress:
            opts['progress_hooks'] = [
                lambda d: _progress_hook(d, controller, status_cb, per_file_progress)
            ]
        else:
            opts['progress_hooks'] = [lambda d: _progress_hook(d, controller, None, None)]
        # One YoutubeDL per thread: a shared instance is not thread-safe.
        with yt_dlp.YoutubeDL(opts) as ydl:
            while not controller.stop_event.is_set():
                try:
                    _, url = work.get_nowait()
                except queue.Empty:
                    return
                try:
                    controller.wait_if_paused()
                    if controller.stop_event.is_set():
                        return
                    if not wait_turn():
                        return
                    controller.current_url = url
                    with lock:
                        stats['done'] += 1
                        position = stats['done']
                    if status_cb:
                        status_cb(f"Downloading ({position}/{n_pending}): {url}")
                    handle(ydl, url)
                finally:
                    work.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    if status_cb and n_pending:
        status_cb(f"Starting {n_pending} downloads with {workers} parallel workers...")
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    controller.current_url = None
    if status_cb and not controller.stop_event.is_set():
        summary = f"{stats['succeeded']} downloaded"
        if skipped:
            summary += f", {skipped} already had"
        if stats['failed']:
            status_cb(f"Done with errors: {summary}, {len(stats['failed'])} failed. "
                      f"First failure: {stats['failed'][0]}")
        else:
            status_cb(f"Done. {summary}.")


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
        raise Exception('Stopped by user')  # noqa: TRY002 - yt-dlp aborts the download on any exception
    controller.wait_if_paused()
    if d.get('status') == 'downloading' and progress_cb:
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
        downloaded = d.get('downloaded_bytes') or 0
        pct = (downloaded / total * 100) if total else 0
        speed = d.get('speed') or 0
        progress_cb(pct, downloaded, total, speed)
    elif d.get('status') == 'finished' and status_cb:
        status_cb('Processing...')
