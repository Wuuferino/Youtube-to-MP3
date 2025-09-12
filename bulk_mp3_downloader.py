import subprocess
import sys
import os
import shutil
import zipfile
import io
import urllib.request
from pathlib import Path

def ensure_dependencies():
    """Ensure yt-dlp is installed. Falls back to invoking via 'py' launcher if needed."""
    try:
        import yt_dlp 
        return
    except ImportError:
        print("yt-dlp not installed – attempting installation...")

    cmd_base = [sys.executable, '-m', 'pip']
    try:
        subprocess.check_call(cmd_base + ['install', '--upgrade', 'pip'])
    except Exception:
        pass
    try:
        subprocess.check_call(cmd_base + ['install', 'yt-dlp'])
    except Exception as e:
        print(f"Primary install attempt failed: {e}\nTrying with 'py' launcher...")
        try:
            subprocess.check_call(['py', '-m', 'pip', 'install', 'yt-dlp'])
        except Exception as e2:
            print("Failed to install yt-dlp. Please install manually: py -m pip install yt-dlp")
            print(f"Error details: {e2}")


def check_ffmpeg(explicit_path: str | None = None):
    """Return tuple (available: bool, location: str | None)."""
    candidates = []
    if explicit_path:
        candidates.append(explicit_path)
    local_ffmpeg = Path('ffmpeg') / 'bin' / 'ffmpeg.exe'
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
            exe = p / 'ffmpeg.exe'
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
    with open(path, 'r', encoding='utf-8') as f:
        lines = [ln.strip() for ln in f.readlines()]
    urls = []
    for ln in lines:
        if not ln or ln.startswith('#'):
            continue
        for part in ln.split():
            if part.startswith('http://') or part.startswith('https://'):
                urls.append(part)
    return urls


def download_audio(urls: list[str], out_dir: str = 'downloads', ffmpeg_available: bool = True, ffmpeg_path: str | None = None):
    from yt_dlp import YoutubeDL

    Path(out_dir).mkdir(parents=True, exist_ok=True)

    postprocessors = []
    if ffmpeg_available:
        postprocessors = [
            {
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            },
            {'key': 'FFmpegMetadata'}
        ]

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(out_dir, '%(title)s.%(ext)s' if not ffmpeg_available else '%(title)s.%(ext)s'),
        'postprocessors': postprocessors,
        'ignoreerrors': True,
        'noplaylist': False,
        'quiet': False,
        'progress_hooks': [progress_hook]
    }

    if ffmpeg_available and ffmpeg_path:
        ffdir = ffmpeg_path
        if ffdir.lower().endswith('ffmpeg.exe'):
            ffdir = str(Path(ffmpeg_path).parent)
        ydl_opts['ffmpeg_location'] = ffdir

    if not ffmpeg_available:
        print("NOTE: ffmpeg not detected. Original audio container (webm/m4a) will be saved instead of MP3. Install ffmpeg and re-run to convert.")

    failures = []
    with YoutubeDL(ydl_opts) as ydl:
        for url in urls:
            print(f"\n=== Downloading: {url} ===")
            try:
                ydl.download([url])
            except Exception as e: 
                print(f"Error downloading {url}: {e}")
                failures.append(url)
    if failures:
        print("\nThe following URLs failed:")
        for f in failures:
            print(f"  {f}")
    else:
        print("\nAll downloads completed.")


def progress_hook(d):
    if d.get('status') == 'downloading':
        total = d.get('total_bytes') or d.get('total_bytes_estimate')
        downloaded = d.get('downloaded_bytes')
        if total and downloaded:
            pct = downloaded / total * 100
            sys.stdout.write(f"\r  Downloading: {pct:5.1f}%")
            sys.stdout.flush()
    elif d.get('status') == 'finished':
        print("\n  Converting to mp3...")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Bulk YouTube -> MP3 downloader')
    parser.add_argument('-i', '--input', default='links.txt', help='Path to text file with YouTube links')
    parser.add_argument('-o', '--output', default=r'C:\\Users\\rasmu\\Music\\Isto Laulud', help='Output directory for MP3 files')
    parser.add_argument('--max', type=int, default=None, help='Maximum number of links to process')
    parser.add_argument('--skip-existing', action='store_true', help='Skip if MP3 with same title already exists')
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

    if args.skip_existing:
        pass

    download_audio(urls, args.output, ffmpeg_available=has_ffmpeg, ffmpeg_path=ffmpeg_loc)


if __name__ == '__main__':
    main()
