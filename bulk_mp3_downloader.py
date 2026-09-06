import sys
import os
import shutil
import zipfile
import io
import urllib.request
from pathlib import Path

def ensure_dependencies():
    """Ensure yt-dlp is present AND recent enough for current YouTube."""
    from downloader_core import ensure_yt_dlp
    try:
        ensure_yt_dlp(status_cb=print)
    except RuntimeError as e:
        print(e)
        sys.exit(1)


def check_ffmpeg(explicit_path: str | None = None):
    """Return tuple (available: bool, location: str | None)."""
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    exe_name = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
    local_ffmpeg = Path('ffmpeg') / 'bin' / exe_name
    if local_ffmpeg.exists():
        candidates.append(str(local_ffmpeg))
    found_in_path = shutil.which('ffmpeg')
    if found_in_path:
        candidates.append(found_in_path)

    for c in candidates:
        p = Path(c)
        if p.is_file():
            return True, str(p)
        if p.is_dir():
            exe = p / exe_name
            if exe.exists():
                return True, str(exe)
    print("WARNING: 'ffmpeg' not found – MP3 conversion will be skipped.\n"
          "Install it (zip): 1) Download https://www.gyan.dev/ffmpeg/builds/ (release essentials zip) 2) Extract so you have ffmpeg\\bin\\ffmpeg.exe 3) Re-run with --ffmpeg <path-to-ffmpeg.exe> or put it under .\\ffmpeg\\bin\\ffmpeg.exe.")
    return False, None


def auto_download_ffmpeg(target_root: Path) -> str | None:
    """Attempt to download a prebuilt ffmpeg zip and extract ffmpeg.exe under target_root/ffmpeg/bin.
    Returns path to ffmpeg.exe or None on failure.
    Uses a GitHub latest build link that is relatively stable.
    """
    url = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
    print("Attempting to download ffmpeg (this may take a minute)...")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
        z = zipfile.ZipFile(io.BytesIO(data))
    except Exception as e: 
        print(f"Failed to download ffmpeg archive: {e}")
        return None
    try:
        out_dir = target_root / 'ffmpeg'
        bin_dir = out_dir / 'bin'
        bin_dir.mkdir(parents=True, exist_ok=True)
        member = None
        for name in z.namelist():
            if name.endswith('bin/ffmpeg.exe'):
                member = name
                break
        if not member:
            print("Could not locate ffmpeg.exe inside archive")
            return None
        with z.open(member) as src, open(bin_dir / 'ffmpeg.exe', 'wb') as dst:
            dst.write(src.read())
        for probe_name in [n for n in z.namelist() if n.endswith('bin/ffprobe.exe')]:
            with z.open(probe_name) as src, open(bin_dir / 'ffprobe.exe', 'wb') as dst:
                dst.write(src.read())
        ffmpeg_path = str(bin_dir / 'ffmpeg.exe')
        print(f"ffmpeg downloaded to {ffmpeg_path}")
        return ffmpeg_path
    except Exception as e: 
        print(f"Extraction failed: {e}")
        return None


def debug_environment():
    """Print diagnostic info to help user verify correct Python & yt-dlp installation."""
    import sys as _sys
    print("Python:", _sys.version)
    print("Executable:", _sys.executable)
    try:
        import yt_dlp as _y
        import importlib.metadata as _im
        print("yt_dlp module file:", getattr(_y, '__file__', 'UNKNOWN'))
        try:
            print("yt_dlp package version:", _im.version('yt-dlp'))
        except Exception: 
            print("Could not retrieve yt-dlp version via importlib.metadata")
        if hasattr(_y, '__version__'):
            print("yt_dlp.__version__:", getattr(_y, '__version__'))
    except Exception as e: 
        print("yt_dlp import failed:", e)


def read_links(path: str) -> list[str]:
    from downloader_core import video_key
    with open(path, 'r', encoding='utf-8') as f:
        lines = [ln.strip() for ln in f.readlines()]
    urls = []
    seen = set()
    for ln in lines:
        if not ln or ln.startswith('#'):
            continue
        for part in ln.split():
            if part.startswith('http://') or part.startswith('https://'):
                key = video_key(part)
                if key in seen:
                    continue
                seen.add(key)
                urls.append(part)
    return urls


def download_audio(urls: list[str], out_dir: str = 'downloads', ffmpeg_available: bool = True,
                   ffmpeg_path: str | None = None, sleep_min: float = None, sleep_max: float = None,
                   skip_existing: bool = True, workers: int | None = None):
    """Delegate to downloader_core so the CLI gets the same throttle handling,
    backoff and resume support as the GUI."""
    from downloader_core import (
        download_urls, DownloadController, SLEEP_MIN, SLEEP_MAX, DEFAULT_WORKERS,
    )

    if not ffmpeg_available:
        print("NOTE: ffmpeg not detected. Original audio container (webm/m4a) will be "
              "saved instead of MP3. Install ffmpeg and re-run to convert.")

    controller = DownloadController(ffmpeg_path=ffmpeg_path)
    download_urls(
        urls,
        out_dir,
        controller,
        require_mp3=ffmpeg_available,
        status_cb=print,
        progress_cb=None,
        file_cb=None,
        sleep_min=SLEEP_MIN if sleep_min is None else sleep_min,
        sleep_max=SLEEP_MAX if sleep_max is None else sleep_max,
        skip_existing=skip_existing,
        workers=DEFAULT_WORKERS if workers is None else workers,
    )


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Bulk YouTube -> MP3 downloader')
    parser.add_argument('-i', '--input', default='links.txt', help='Path to text file with YouTube links')
    parser.add_argument('-o', '--output', default=str(Path.home() / 'Music' / 'YouTube MP3'),
                        help='Output directory for MP3 files')
    parser.add_argument('--max', type=int, default=None, help='Maximum number of links to process')
    parser.add_argument('--skip-existing', action='store_true',
                        help='(default) Skip links already downloaded and still on disk')
    parser.add_argument('--redownload', action='store_true',
                        help='Ignore the download log and fetch everything again')
    parser.add_argument('--sleep', type=float, default=None,
                        help='Minimum seconds to wait between videos (default 3)')
    parser.add_argument('--max-sleep', type=float, default=None,
                        help='Maximum seconds to wait between videos (default 8)')
    parser.add_argument('--workers', type=int, default=None,
                        help='Videos to download in parallel (default 4, max 8)')
    parser.add_argument('--ffmpeg', default=None, help='Explicit path to ffmpeg executable or its containing folder')
    parser.add_argument('--auto-ffmpeg', action='store_true', help='If ffmpeg missing, attempt to auto-download a Windows build locally')
    parser.add_argument('--require-mp3', action='store_true', help='Abort if mp3 conversion not possible (ffmpeg missing)')
    args = parser.parse_args()

    ensure_dependencies()
    has_ffmpeg, ffmpeg_loc = check_ffmpeg(args.ffmpeg)
    if not has_ffmpeg and args.auto_ffmpeg:
        dl_path = auto_download_ffmpeg(Path('.'))
        if dl_path:
            has_ffmpeg, ffmpeg_loc = True, dl_path
        else:
            print("Auto download failed.")
    if args.require_mp3 and not has_ffmpeg:
        print("ffmpeg required but not available. Install it or use --auto-ffmpeg.")
        return
    debug_environment()

    urls = read_links(args.input)
    if args.max:
        urls = urls[:args.max]
    if not urls:
        print('No URLs found in input file.')
        return

    download_audio(urls, args.output, ffmpeg_available=has_ffmpeg, ffmpeg_path=ffmpeg_loc,
                   sleep_min=args.sleep, sleep_max=args.max_sleep,
                   skip_existing=not args.redownload, workers=args.workers)


if __name__ == '__main__':
    main()
