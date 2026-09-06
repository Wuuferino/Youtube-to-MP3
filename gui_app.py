
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
import sys, os, socket
from downloader_core import (
    DEFAULT_WORKERS,
    MAX_WORKERS,
    load_urls,
    download_urls,
    get_missing_urls,
    DownloadController,
    convert_leftovers,
    detect_ffmpeg,
    auto_download_ffmpeg,
)


def _adjust_color(hex_color: str, delta: float) -> str:
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        return '#' + hex_color
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    def adj(c):
        return int(c + (255 - c) * min(delta, 1.0)) if delta >= 0 else int(c * (1 + max(delta, -1.0)))
    return f"#{adj(r):02x}{adj(g):02x}{adj(b):02x}"


def resource_path(name: str) -> str:
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, name)
    return os.path.join(os.path.abspath('.'), name)


_SINGLE_INSTANCE_PORT = 43127


def acquire_single_instance():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('127.0.0.1', _SINGLE_INSTANCE_PORT))
        s.listen(1)
        return s
    except OSError:
        try:
            s.close()
        except Exception:
            pass
        return None


class App:
    def __init__(self, root: tk.Tk, instance_lock=None): 
        self.root = root
        root.title('YouTube MP3 Downloader')
        root.geometry('820x560')
        root.minsize(760, 520)
        self._instance_lock = instance_lock
        self.txt_file_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(Path.home() / 'Music' / 'Isto Laulud'))
        self.status_var = tk.StringVar(value='Idle')
        self.count_var = tk.StringVar(value='0/0')
        self.ffmpeg_status_var = tk.StringVar(value='FFmpeg: Checking...')
        self.allow_original_var = tk.BooleanVar(value=True)
        self.auto_ffmpeg_var = tk.BooleanVar(value=True)
        self.workers_var = tk.IntVar(value=DEFAULT_WORKERS)
        self.controller = DownloadController()
        self.worker_thread: threading.Thread | None = None
        self.total_urls: list[str] = []
        self.remaining_urls: list[str] = []
        self.batch_total = 0
        self.completed = 0

        self.progress_pct = 0.0
        self.display_progress = 0.0
        self._pacman_initialized = False
        self._pacman_id = None
        self._pct_text_id = None
        self._pacman_mouth_open = True
        self._pacman_animating = False
        self._food_ids: list[int] = []
        self._food_total = 42
        self._pac_radius = 10
        self._animate_after_id: int | None = None
        self._closed = False

        self._build_ui()
        self.root.after(150, self._refresh_ffmpeg_status)
        self._set_window_icon()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    def _set_window_icon(self):
        try:
            ico = resource_path('pacman.ico')
            if Path(ico).exists():
                self.root.iconbitmap(ico)
                try:
                    png = resource_path('pacman.png')
                    if Path(png).exists():
                        img = tk.PhotoImage(file=png)
                        self.root.iconphoto(True, img)
                        self._icon_image_ref = img 
                except Exception:
                    pass
        except Exception:
            pass

    def _safe_after(self, ms: int, func):
        if self._closed:
            return None
        return self.root.after(ms, func)

    def _on_close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.controller.stop()
        except Exception: 
            pass
        if self._animate_after_id is not None:
            try:
                self.root.after_cancel(self._animate_after_id)
            except Exception: 
                pass
        self.root.after(80, self.root.destroy)

    def _build_ui(self):
        frm = tk.Frame(self.root, padx=10, pady=10)
        frm.pack(fill='both', expand=True)

        tk.Label(frm, text='Links file (.txt):').grid(row=0, column=0, sticky='w')
        tk.Entry(frm, textvariable=self.txt_file_var).grid(row=0, column=1, sticky='we')
        tk.Button(frm, text='Browse', command=self.browse_file).grid(row=0, column=2, padx=5)

        tk.Label(frm, text='Output folder:').grid(row=1, column=0, sticky='w', pady=(6, 0))
        tk.Entry(frm, textvariable=self.output_dir_var).grid(row=1, column=1, sticky='we', pady=(6, 0))
        tk.Button(frm, text='Browse', command=self.browse_output).grid(row=1, column=2, padx=5, pady=(6, 0))

        btn_frame = tk.Frame(frm)
        btn_frame.grid(row=2, column=0, columnspan=3, pady=10, sticky='we')
        for i in range(8):
            btn_frame.grid_columnconfigure(i, weight=1)

        def make_btn(text, cmd, base, col):
            hov = _adjust_color(base, 0.18)
            act = _adjust_color(base, -0.25)
            b = tk.Button(btn_frame, text=text, command=cmd, bg=base, fg='white', activebackground=act,
                          activeforeground='white', relief='raised', bd=2, font=('Segoe UI', 9, 'bold'), cursor='hand2')
            b.grid(row=0, column=col, padx=3, sticky='we')
            b.bind('<Enter>', lambda e, bb=b, c=hov: bb.configure(bg=c))
            b.bind('<Leave>', lambda e, bb=b, c=base: bb.configure(bg=c))
            return b

        self.btn_execute = make_btn('Execute', self.start_download, '#2e7d32', 0)
        self.btn_pause = make_btn('Pause', self.pause, '#ef6c00', 1)
        self.btn_resume = make_btn('Resume', self.resume, '#0277bd', 2)
        self.btn_stop = make_btn('Stop', self.stop, '#c62828', 3)
        self.btn_restart_missing = make_btn('Restart Missing', self.restart_missing, '#6a1b9a', 4)
        self.btn_restart_all = make_btn('Restart All', self.restart_all, '#424242', 5)
        self.btn_convert_left = make_btn('Convert Leftovers', self.convert_leftovers_btn, '#455a64', 6)
        self.btn_clear = make_btn('Clear List', self.clear_list, '#546e7a', 7)

        opt_frame = tk.Frame(frm)
        opt_frame.grid(row=3, column=0, columnspan=3, sticky='we')
        tk.Checkbutton(opt_frame, text='Allow non-MP3 fallback', variable=self.allow_original_var, command=self._option_changed).pack(side='left')
        tk.Checkbutton(opt_frame, text='Auto-download ffmpeg', variable=self.auto_ffmpeg_var, command=self._option_changed).pack(side='left', padx=(12, 0))
        tk.Label(opt_frame, text='Parallel:').pack(side='left', padx=(12, 2))
        tk.Spinbox(opt_frame, from_=1, to=MAX_WORKERS, width=3,
                   textvariable=self.workers_var).pack(side='left')
        tk.Label(opt_frame, textvariable=self.ffmpeg_status_var, fg='#555').pack(side='right')

        self.progress_canvas = tk.Canvas(frm, height=38, bg='#263238', highlightthickness=0)
        self.progress_canvas.grid(row=4, column=0, columnspan=3, sticky='we', pady=(6, 0))
        self.progress_canvas.bind('<Configure>', lambda e: self._reinit_on_resize())
        self._init_pacman_bar()

        info = tk.Frame(frm)
        info.grid(row=5, column=0, columnspan=3, sticky='we', pady=(6, 0))
        tk.Label(info, textvariable=self.count_var, font=('Segoe UI', 9, 'bold')).pack(side='left')
        tk.Label(info, text=' | ').pack(side='left')
        tk.Label(info, textvariable=self.status_var, fg='#1565c0').pack(side='left')

        list_frame = tk.Frame(frm, bd=1, relief='sunken')
        list_frame.grid(row=6, column=0, columnspan=3, sticky='nsew', pady=(8, 0))
        frm.grid_rowconfigure(6, weight=1)
        frm.grid_columnconfigure(1, weight=1)
        self.listbox = tk.Listbox(list_frame, activestyle='none')
        self.listbox.pack(side='left', fill='both', expand=True)
        sb = tk.Scrollbar(list_frame, command=self.listbox.yview)
        sb.pack(side='right', fill='y')
        self.listbox.config(yscrollcommand=sb.set)

        self._set_running_state(False)

    def _set_running_state(self, running: bool):
        self.btn_execute.configure(state='disabled' if running else 'normal')
        self.btn_pause.configure(state='normal' if running else 'disabled')
        self.btn_resume.configure(state='disabled')
        self.btn_stop.configure(state='normal' if running else 'disabled')
        self.btn_restart_missing.configure(state='disabled' if running else 'normal')
        self.btn_restart_all.configure(state='disabled' if running else 'normal')
        self.btn_convert_left.configure(state='disabled' if running else 'normal')
        self.btn_clear.configure(state='disabled' if running else 'normal')
        if running and not self._pacman_animating:
            self._pacman_animating = True
            self._animate_pacman()
        if not running:
            self._pacman_animating = False

    def _option_changed(self): 
        self._refresh_ffmpeg_status()

    def _refresh_ffmpeg_status(self):
        ff = detect_ffmpeg(None)
        self.ffmpeg_status_var.set('FFmpeg: Found' if ff else 'FFmpeg: Missing')

    def browse_file(self):
        f = filedialog.askopenfilename(filetypes=[('Text Files', '*.txt')])
        if f:
            self.txt_file_var.set(f)

    def browse_output(self):
        d = filedialog.askdirectory()
        if d:
            self.output_dir_var.set(d)

    def pause(self):
        if not self.worker_thread or not self.worker_thread.is_alive():
            return
        self.controller.pause()
        self.status_var.set('Paused')
        self.btn_pause.configure(state='disabled')
        self.btn_resume.configure(state='normal')

    def resume(self):
        if not self.worker_thread or not self.worker_thread.is_alive():
            return
        self.controller.resume()
        self.status_var.set('Resuming...')
        self.btn_resume.configure(state='disabled')
        self.btn_pause.configure(state='normal')

    def stop(self):
        if not self.worker_thread or not self.worker_thread.is_alive():
            return
        self.controller.stop()
        self.status_var.set('Stopping...')

    def restart_missing(self):
        if not self.total_urls:
            messagebox.showinfo('Info', 'No previous run yet.')
            return
        missing = get_missing_urls(self.total_urls, self.output_dir_var.get())
        if not missing:
            messagebox.showinfo('Info', 'No missing files.')
            return
        self._start_with_urls(missing)

    def restart_all(self):
        if not self.total_urls:
            self.start_download()
            return
        self._start_with_urls(self.total_urls)

    def clear_list(self):
        self.listbox.delete(0, tk.END)
        self.status_var.set('List cleared')

    def convert_leftovers_btn(self):
        out_dir = self.output_dir_var.get()
        self.status_var.set('Converting leftovers...')

        def worker():
            convert_leftovers(out_dir, status_cb=lambda m: self._safe_after(0, lambda: self.status_var.set(m)))
            self._refresh_ffmpeg_status()

        threading.Thread(target=worker, daemon=True).start()

    def start_download(self):
        links_file = self.txt_file_var.get()
        if not links_file:
            messagebox.showerror('Error', 'Select a links file.')
            return
        if not Path(links_file).exists():
            messagebox.showerror('Error', 'Links file does not exist.')
            return
        urls = load_urls(links_file)
        if not urls:
            messagebox.showerror('Error', 'No URLs found in file.')
            return
        self.total_urls = urls
        self._start_with_urls(urls)

    def _start_with_urls(self, urls: list[str]):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showerror('Error', 'Download already running.')
            return
        require_mp3 = not self.allow_original_var.get()
        if require_mp3:
            ff = detect_ffmpeg(None)
            if not ff and self.auto_ffmpeg_var.get():
                self.status_var.set('Downloading ffmpeg...')
                ff = auto_download_ffmpeg(Path('.'))
            if not ff:
                if messagebox.askyesno('FFmpeg missing', 'FFmpeg not found. Download now?'):
                    self.status_var.set('Downloading ffmpeg...')
                    ff = auto_download_ffmpeg(Path('.'))
                if not ff:
                    messagebox.showerror('Error', 'FFmpeg still missing; cannot enforce MP3.')
                    return
            self.controller.ffmpeg_path = ff
        else:
            ff = detect_ffmpeg(None)
            if ff:
                self.controller.ffmpeg_path = ff
        self._refresh_ffmpeg_status()
        self.controller.reset()
        self.remaining_urls = list(urls)
        self.batch_total = len(urls)
        self.completed = 0
        self.count_var.set(f'0/{self.batch_total}')
        self._update_progress_bar(0)
        self.status_var.set('Starting...')
        self.listbox.delete(0, tk.END)
        self._set_running_state(True)
        self.worker_thread = threading.Thread(target=self._run_worker, args=(urls,), daemon=True)
        self.worker_thread.start()

    def _run_worker(self, urls: list[str]):
        out_dir = self.output_dir_var.get()
        require_mp3 = not self.allow_original_var.get()
        try:
            workers = max(1, min(int(self.workers_var.get()), MAX_WORKERS))
        except Exception:
            workers = DEFAULT_WORKERS

        def status_cb(msg):
            self._safe_after(0, lambda: self.status_var.set(msg))

        def progress_cb(pct, downloaded, total, speed):  # noqa: D401
            self._safe_after(0, lambda: self._update_progress_bar(pct))

        def file_cb(idx, total, path, title, ext):  # noqa: D401
            self._safe_after(0, lambda: self._file_done(path, total))

        download_urls(
            urls,
            out_dir,
            self.controller,
            require_mp3=require_mp3,
            status_cb=status_cb,
            progress_cb=progress_cb,
            file_cb=file_cb,
            workers=workers,
        )

        def finalize():
            if self._closed:
                return
            self._set_running_state(False)
            if self.controller.stop_event.is_set():
                self.status_var.set('Stopped')
            elif self.completed >= self.batch_total:
                if require_mp3:
                    convert_leftovers(out_dir, status_cb=lambda m: self._safe_after(0, lambda: self.status_var.set(m)))
                self.status_var.set('Done.')
            elif 'ffmpeg missing' in self.status_var.get():
                pass
            else:
                self.status_var.set('Idle')
            self._refresh_ffmpeg_status()

        self._safe_after(0, finalize)

    def _file_done(self, path: str, total: int):
        self.completed += 1
        self.count_var.set(f'{self.completed}/{total}')
        self.listbox.insert(tk.END, f'{self.completed}. {Path(path).name}')
        self.listbox.yview_moveto(1)
        pct = (self.completed / total * 100) if total else 0
        self._update_progress_bar(pct)

    def _init_pacman_bar(self):
        self.progress_canvas.delete('all')
        self._food_ids.clear()
        w = self.progress_canvas.winfo_width() or self.progress_canvas.winfo_reqwidth() or 600
        pad = 12
        track_y = 20
        spacing = (w - 2 * pad - self._pac_radius) / self._food_total
        for i in range(self._food_total):
            x = pad + self._pac_radius + i * spacing
            dot = self.progress_canvas.create_oval(x - 3, track_y - 3, x + 3, track_y + 3, fill='#ffd54f', outline='')
            self._food_ids.append(dot)
        self._pacman_id = self.progress_canvas.create_arc(
            pad, track_y - self._pac_radius, pad + 2 * self._pac_radius, track_y + self._pac_radius,
            start=30, extent=300, fill='#ffeb3b', outline='#ffeb3b'
        )
        self._pct_text_id = self.progress_canvas.create_text(w - pad, 10, anchor='e', fill='#eceff1', font=('Segoe UI', 9, 'bold'), text='0.0%')
        self._pacman_initialized = True
        self.progress_canvas.update_idletasks()

    def _animate_pacman(self):
        if not self._pacman_animating or self._closed:
            return
        target = self.progress_pct
        self.display_progress += (target - self.display_progress) * 0.25
        if abs(self.display_progress - target) < 0.05:
            self.display_progress = target
        self._pacman_mouth_open = not self._pacman_mouth_open
        self._redraw_pacman()
        self._animate_after_id = self._safe_after(90, self._animate_pacman)

    def _redraw_pacman(self):
        if not self._pacman_initialized:
            self._init_pacman_bar()
        w = self.progress_canvas.winfo_width() or 1
        pad = 12
        track_y = 20
        radius = self._pac_radius
        max_x = w - pad - 2 * radius
        frac = max(0.0, min(1.0, self.display_progress / 100.0))
        x = pad + frac * max_x
        self.progress_canvas.delete(self._pacman_id)
        start_angle = 38 if self._pacman_mouth_open else 5
        extent_angle = 284 if self._pacman_mouth_open else 350
        self._pacman_id = self.progress_canvas.create_arc(
            x, track_y - radius, x + 2 * radius, track_y + radius,
            start=start_angle, extent=extent_angle, fill='#ffeb3b', outline='#ffeb3b'
        )
        eaten_count = int(frac * self._food_total + 0.001)
        for idx, dot_id in enumerate(self._food_ids):
            self.progress_canvas.itemconfigure(dot_id, fill='#37474f' if idx < eaten_count else '#ffd54f')
        self.progress_canvas.itemconfigure(self._pct_text_id, text=f'{self.display_progress:4.1f}%')

    def _update_progress_bar(self, pct: float):
        self.progress_pct = pct
        if not self._pacman_animating:
            self.display_progress = pct
            self._redraw_pacman()

    def _reinit_on_resize(self):
        tgt = self.progress_pct
        self._init_pacman_bar()
        self.display_progress = tgt
        self._redraw_pacman()


def main():
    lock = acquire_single_instance()
    if lock is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo('Already running', 'The downloader is already open.')
        root.destroy()
        return
    root = tk.Tk()
    App(root, instance_lock=lock)
    root.mainloop()


if __name__ == '__main__':
    main()
