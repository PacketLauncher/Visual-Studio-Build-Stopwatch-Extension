import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

import win32pipe
import win32file
import pywintypes

PIPE_NAME = r"\\.\pipe\VSBuildStopwatch"

APP_NAME = 'Visual Studio Build Stopwatch'
GITHUB_URL = 'https://github.com/packetlauncher'
CONFIG_DIR = Path(os.environ.get('APPDATA', str(Path.home()))) / 'VSBuildStopwatch'
CONFIG_PATH = CONFIG_DIR / 'settings.json'
SIZES = {'Small': (10, 6, 260), 'Medium': (13, 9, 340), 'Large': (17, 12, 440)}  # fonts in points; widths in 96-DPI units
POSITIONS = ('Top right', 'Top left', 'Bottom right', 'Bottom left')
APPEARANCES = ('Instant', 'Slide')
ANIMATION_SECONDS = 0.28
FONT_STYLES = ('Regular', 'Bold', 'Italic', 'Bold Italic')
FONT_KEYS = ('solution_font', 'project_font')
SOUND_KEYS = ('sound_success', 'sound_failure')
SUPPORTED_AUDIO = ('.wav', '.mp3', '.ogg', '.flac')
AUDIO_FILETYPES = [('Supported audio files', '*.wav *.mp3 *.ogg *.flac'),
                   ('WAV audio', '*.wav'), ('MP3 audio', '*.mp3'),
                   ('OGG Vorbis audio', '*.ogg'), ('FLAC audio', '*.flac'),
                   ('All files', '*.*')]




class UndoableEntry(ttk.Entry):
    """ttk Entry with Ctrl+Z undo and Ctrl+Y / Ctrl+Shift+Z redo.

    Tracks the StringVar so typing, paste, cut, Browse and Clear are undoable.
    """
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._variable = kwargs.get('textvariable')
        if self._variable is None:
            self._variable = tk.StringVar(master=self, value=self.get())
            self.configure(textvariable=self._variable)
        self._previous = self._variable.get()
        self._undo = []
        self._redo = []
        self._restoring = False
        self._trace = self._variable.trace_add('write', self._on_change)
        self.bind('<Control-z>', self._undo_action)
        self.bind('<Control-Z>', self._undo_action)
        self.bind('<Control-y>', self._redo_action)
        self.bind('<Control-Y>', self._redo_action)
        self.bind('<Control-Shift-z>', self._redo_action)
        self.bind('<Control-Shift-Z>', self._redo_action)

    def _on_change(self, *_):
        current = self._variable.get()
        if current == self._previous:
            return
        if not self._restoring:
            self._undo.append(self._previous)
            if len(self._undo) > 200:
                del self._undo[0]
            self._redo.clear()
        self._previous = current

    def _undo_action(self, event=None):
        if self._undo:
            self._redo.append(self._variable.get())
            self._restore(self._undo.pop())
        return 'break'

    def _redo_action(self, event=None):
        if self._redo:
            self._undo.append(self._variable.get())
            self._restore(self._redo.pop())
        return 'break'

    def _restore(self, value):
        self._restoring = True
        try:
            self._variable.set(value)
            self.icursor('end')
            self.xview_moveto(1)
        finally:
            self._restoring = False


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def load_settings():
    settings = {'size': 'Medium', 'delay': 3, 'position': 'Bottom right',
                'solution_font': {'family': 'Segoe UI', 'size': 15, 'style': 'Bold'},
                'project_font': {'family': 'Segoe UI', 'size': 13, 'style': 'Regular'},
                'appearance': 'Instant', 'sound_success': '', 'sound_failure': ''}
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        if isinstance(saved, dict):
            if saved.get('size') in SIZES:
                settings['size'] = saved['size']
            if saved.get('position') in POSITIONS:
                settings['position'] = saved['position']
            if saved.get('appearance') in APPEARANCES:
                settings['appearance'] = saved['appearance']
            for key in SOUND_KEYS:
                path = saved.get(key)
                if isinstance(path, str) and len(path) <= 2048:
                    settings[key] = os.path.normpath(path) if path else ''
            for key in FONT_KEYS:
                value = saved.get(key)
                if isinstance(value, dict):
                    family, size, style = value.get('family'), value.get('size'), value.get('style')
                    if isinstance(family, str) and 0 < len(family) <= 100 and isinstance(size, int) and 6 <= size <= 72 and style in FONT_STYLES:
                        settings[key] = {'family': family, 'size': size, 'style': style}
            delay = saved.get('delay')
            if type(delay) in (int, float) and 0 <= delay <= 60:
                settings['delay'] = int(delay)
    except (OSError, ValueError, TypeError):
        pass
    return settings


def save_settings(settings):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(settings, indent=2), encoding='utf-8')
    except OSError as exc:
        print('Could not save settings:', exc, file=sys.stderr)


def resource_path(filename):
    # Works both when running the .py and when packaged using PyInstaller.
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / filename



def get_app_version():
    """Read the version_info.txt source metadata or the EXE's embedded version."""
    if getattr(sys, 'frozen', False) and os.name == 'nt':
        # PyInstaller embeds version_info.txt as a Windows VERSIONINFO resource.
        try:
            from ctypes import wintypes

            class VS_FIXEDFILEINFO(ctypes.Structure):
                _fields_ = [
                    ('dwSignature', wintypes.DWORD),
                    ('dwStrucVersion', wintypes.DWORD),
                    ('dwFileVersionMS', wintypes.DWORD),
                    ('dwFileVersionLS', wintypes.DWORD),
                    ('dwProductVersionMS', wintypes.DWORD),
                    ('dwProductVersionLS', wintypes.DWORD),
                    ('dwFileFlagsMask', wintypes.DWORD),
                    ('dwFileFlags', wintypes.DWORD),
                    ('dwFileOS', wintypes.DWORD),
                    ('dwFileType', wintypes.DWORD),
                    ('dwFileSubtype', wintypes.DWORD),
                    ('dwFileDateMS', wintypes.DWORD),
                    ('dwFileDateLS', wintypes.DWORD),
                ]

            version = ctypes.windll.version
            version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
            version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
            version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
            version.GetFileVersionInfoW.restype = wintypes.BOOL
            version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                               ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
            version.VerQueryValueW.restype = wintypes.BOOL
            dummy = wintypes.DWORD(0)
            size = version.GetFileVersionInfoSizeW(sys.executable, ctypes.byref(dummy))
            if size:
                data = ctypes.create_string_buffer(size)
                if version.GetFileVersionInfoW(sys.executable, 0, size, data):
                    pointer = ctypes.c_void_p()
                    length = wintypes.UINT(0)
                    if version.VerQueryValueW(data, '\\', ctypes.byref(pointer), ctypes.byref(length)) and length.value >= ctypes.sizeof(VS_FIXEDFILEINFO):
                        info = ctypes.cast(pointer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
                        if info.dwSignature == 0xFEEF04BD:
                            return '{}.{}.{}.{}'.format(info.dwFileVersionMS >> 16,
                                                          info.dwFileVersionMS & 0xFFFF,
                                                          info.dwFileVersionLS >> 16,
                                                          info.dwFileVersionLS & 0xFFFF)
        except (OSError, AttributeError, ValueError):
            pass
    else:
        # The PyInstaller version file is Python syntax; extract its version tuple
        # without executing the file.
        try:
            source = (Path(__file__).resolve().parent / 'version_info.txt').read_text(encoding='utf-8-sig')
            match = re.search(r'\bfilevers\s*=\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', source)
            if match:
                return '.'.join(match.groups())
        except OSError:
            pass
    return 'Unknown'


def tray_image():
    # Select the embedded 64px image rather than resampling another ICO frame.
    with Image.open(resource_path('VS_Build_Stopwatch.ico')) as icon:
        icon.size = (64, 64)
        return icon.convert('RGBA').copy()


# Keep Windows icon handles alive while their dialog windows exist.
_window_icon_handles = []


def apply_window_icon(window):
    """Use an exact embedded ICO size for Windows' small title-bar icon."""
    path = str(resource_path('VS_Build_Stopwatch.ico'))
    window.iconbitmap(default=path)
    if os.name != 'nt':
        return
    try:
        window.update_idletasks()
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        dpi = user32.GetDpiForWindow(hwnd) if hasattr(user32, 'GetDpiForWindow') else 96
        # SM_CXSMICON = 49. At 200% DPI this is normally 32 physical pixels.
        if hasattr(user32, 'GetSystemMetricsForDpi'):
            size = user32.GetSystemMetricsForDpi(49, dpi)
        else:
            size = round(16 * dpi / 96)
        # These are the exact sizes embedded in our multi-resolution .ico.
        available = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
        size = min(available, key=lambda n: (abs(n - size), n))
        load_image = user32.LoadImageW
        load_image.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                               ctypes.c_int, ctypes.c_int, ctypes.c_uint)
        load_image.restype = ctypes.c_void_p
        # IMAGE_ICON=1, LR_LOADFROMFILE=0x10: request the exact icon resource.
        small = load_image(None, path, 1, size, size, 0x10)
        if small:
            user32.SendMessageW(hwnd, 0x0080, 0, small)  # WM_SETICON, ICON_SMALL
            _window_icon_handles.append(small)
    except (OSError, AttributeError, ValueError) as exc:
        print('Could not set native-size title-bar icon:', exc, file=sys.stderr)


class StopwatchApp:
    def __init__(self):
        self.settings = load_settings()
        self.root = tk.Tk()
        self.root.withdraw()
        apply_window_icon(self.root)
        self.events = queue.Queue()
        self.builds = {}
        self.groups = {}
        self.counter = 0
        self.running = True
        self.tray = None
        self.font_dialog = None
        self.sound_dialog = None
        self.about_dialog = None
        self._audio_lock = threading.Lock()
        self._audio_cache = {}
        threading.Thread(target=self.pipe_server, daemon=True).start()
        self.root.after(50, self.poll_events)
        self.root.after(100, self.tick)

    def pipe_server(self):
        """Accept local build notifications without touching Tk from this thread."""
        while self.running:
            handle = None
            try:
                handle = win32pipe.CreateNamedPipe(
                    PIPE_NAME, win32pipe.PIPE_ACCESS_INBOUND,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE |
                    win32pipe.PIPE_WAIT, win32pipe.PIPE_UNLIMITED_INSTANCES,
                    4096, 4096, 0, None)
                win32pipe.ConnectNamedPipe(handle, None)
                _, data = win32file.ReadFile(handle, 4096)
                message = json.loads(data.decode('utf-8-sig').strip())
                event = message.get('event')
                build_id = message.get('id')
                if isinstance(build_id, str) and 0 < len(build_id) <= 128:
                    if event == 'start':
                        name = message.get('project', 'Unknown Solution')
                        if isinstance(name, str):
                            self.enqueue('start', build_id, name[:300], message.get('kind', 'solution'), message.get('parent'))
                    elif event == 'finish':
                        self.enqueue('finish', build_id, message.get('success') is True)
            except (pywintypes.error, OSError, ValueError, UnicodeError) as exc:
                if self.running:
                    print('Named pipe error:', exc, file=sys.stderr)
                    time.sleep(0.1)
            finally:
                if handle is not None:
                    try:
                        win32file.CloseHandle(handle)
                    except pywintypes.error:
                        pass

    def enqueue(self, name, *args):
        self.events.put((name, args))

    def poll_events(self):
        if not self.running:
            return
        try:
            while True:
                name, args = self.events.get_nowait()
                if name == 'start':
                    self.start_build(*args)
                elif name == 'finish':
                    self.finish_build(*args)
                elif name == 'setting':
                    key, value = args
                    self.settings[key] = value
                    save_settings(self.settings)
                    self.refresh_layout()
                elif name == 'font_dialog':
                    self.show_font_dialog()
                elif name == 'sound_dialog':
                    self.show_sound_dialog()
                elif name == 'about_dialog':
                    self.show_about_dialog()
                elif name == 'sound_error':
                    message, preview = args
                    if preview and self.sound_dialog is not None and self.sound_dialog.winfo_exists():
                        messagebox.showerror('Cannot play sound', message, parent=self.sound_dialog)
                    else:
                        print('Could not play build sound:', message, file=sys.stderr)
                elif name == 'quit':
                    self.shutdown()
                    return
        except queue.Empty:
            pass
        self.root.after(50, self.poll_events)

    def start_build(self, build_id, project, kind='solution', parent=None):
        if build_id in self.builds:
            return
        if kind != 'project' or not isinstance(parent, str) or parent not in self.groups:
            kind, parent = 'solution', None
            group_id = build_id
            window = tk.Toplevel(self.root)
            window.withdraw()
            window.overrideredirect(True)
            window.attributes('-topmost', True)
            window.configure(bg='#20252d')
            outer = tk.Frame(window, bg='#20252d', highlightbackground='#46515e',
                             highlightthickness=1)
            outer.pack(fill='both', expand=True)
            self.groups[group_id] = {'window': window, 'outer': outer, 'rows': [],
                                     'solution': build_id, 'divider': None,
                                     'shown': False, 'anim': None, 'exit_pending': False, 'x': None,
                                     'target_x': None, 'height': 0, 'width': 0, 'y': 0}
            window.update_idletasks()
            try:
                hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
                get_long = ctypes.windll.user32.GetWindowLongW
                set_long = ctypes.windll.user32.SetWindowLongW
                style = get_long(hwnd, -20)
                set_long(hwnd, -20, style | 0x08000000 | 0x00000080)
            except Exception:
                pass
        else:
            group_id = parent
        group = self.groups[group_id]
        if kind == 'project' and group['divider'] is None:
            divider = tk.Frame(group['outer'], bg='#46515e', height=1)
            divider.pack(fill='x', padx=10, pady=(3, 3))
            group['divider'] = divider
        row = tk.Frame(group['outer'], bg='#20252d')
        row.pack(fill='x')
        status = tk.Label(row, text='●', fg='#78bfff', bg='#20252d')
        status.pack(side='left')
        clock = tk.Label(row, text='00:00:00', fg='#ffffff', bg='#20252d', anchor='e')
        clock.pack(side='right')
        label = tk.Label(row, text=project, anchor='w', fg='#ffffff', bg='#20252d')
        label.pack(side='left', fill='x', expand=True)
        self.builds[build_id] = {
            'project': project, 'kind': kind, 'group': group_id,
            'start': time.perf_counter(), 'elapsed': 0,
            'finished_at': None, 'success': None,
            'row': row, 'label': label, 'status': status, 'clock': clock,
        }
        group['rows'].append(build_id)
        self.refresh_layout()

    def finish_build(self, build_id, success=True):
        build = self.builds.get(build_id)
        if not build or build['finished_at'] is not None:
            return
        build['elapsed'] = time.perf_counter() - build['start']
        build['finished_at'] = time.monotonic()
        build['success'] = success
        self.refresh_build(build)
        # Play one notification per solution build, never once per project.
        if build['kind'] == 'solution' and not build_id.startswith('test-'):
            self.play_completion_sound(success)

    def play_completion_sound(self, success):
        key = 'sound_success' if success else 'sound_failure'
        path = self.settings.get(key, '')
        if path:
            self.play_audio(path)

    def play_audio(self, path, preview=False):
        """Play supported audio without blocking Tk or the build-event listener."""
        path = os.path.normpath(path.strip().strip('"'))

        def worker():
            try:
                import pygame
                if not os.path.isfile(path):
                    raise FileNotFoundError(path)
                if Path(path).suffix.lower() not in SUPPORTED_AUDIO:
                    raise ValueError('Unsupported audio format: ' + Path(path).suffix)
                with self._audio_lock:
                    if not pygame.mixer.get_init():
                        pygame.mixer.init()
                    # Retain Sound objects so playback is not tied to local scope.
                    key = (path, os.path.getmtime(path))
                    sound = self._audio_cache.get(key)
                    if sound is None:
                        sound = pygame.mixer.Sound(path)
                        self._audio_cache = {k: v for k, v in self._audio_cache.items()
                                             if k[0] != path}
                        self._audio_cache[key] = sound
                    channel = sound.play()
                    if channel is None:
                        raise RuntimeError('No free audio playback channels.')
            except Exception as exc:
                self.enqueue('sound_error', str(exc), preview)

        threading.Thread(target=worker, daemon=True).start()

    def refresh_build(self, build):
        if build['finished_at'] is None:
            elapsed = time.perf_counter() - build['start']
            build['status'].config(text='●', fg='#78bfff')
            build['clock'].config(fg='#ffffff')
        else:
            elapsed = build['elapsed']
            color = '#75dfa4' if build['success'] else '#ff7777'
            build['status'].config(text='✓' if build['success'] else '✕', fg=color)
            build['clock'].config(fg=color)
        seconds = int(elapsed)
        build['clock'].config(text=f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}')

    def work_area(self):
        """Primary monitor usable rectangle, excluding the taskbar and docked bars."""
        if os.name == 'nt':
            class RECT(ctypes.Structure):
                _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                            ('right', ctypes.c_long), ('bottom', ctypes.c_long)]
            rect = RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def refresh_layout(self):
        font_points, _, base_width = SIZES[self.settings['size']]
        position = self.settings['position']
        scale = float(self.root.winfo_fpixels('1p')) * 72.0 / 96.0
        margin, gap = round(16 * scale), round(7 * scale)
        pad_x, pad_y = round(12 * scale), round(7 * scale)
        left, top, right, bottom = self.work_area()
        width = min(max(1, right - left - 2 * margin), round(base_width * scale))
        def make_font(key):
            spec = self.settings[key]
            style = spec['style']
            return tkfont.Font(root=self.root, family=spec['family'], size=spec['size'],
                               weight='bold' if 'Bold' in style else 'normal',
                               slant='italic' if 'Italic' in style else 'roman')
        regular = make_font('project_font')
        heading = make_font('solution_font')
        clock_font = tkfont.Font(root=self.root, family='Segoe UI', size=font_points, weight='bold')
        heights = []
        for group_id, group in self.groups.items():
            total_height = 0
            for build_id in group['rows']:
                build = self.builds.get(build_id)
                if not build:
                    continue
                main = build['kind'] == 'solution'
                font = heading if main else regular
                row_height = max(round(35 * scale), font.metrics('linespace') + 2 * pad_y)
                build['row'].config(height=row_height, padx=pad_x, pady=pad_y)
                build['row'].pack_propagate(False)
                build['label'].config(font=font)
                build['status'].config(font=font, padx=0)
                build['clock'].config(font=clock_font, padx=0)
                clock_width = clock_font.measure('00:00:00')
                remaining = max(20, width - 2 * pad_x - clock_width -
                                font.measure('●') - round(28 * scale))
                label = build['project']
                while label and font.measure(label) > remaining:
                    label = label[:-1]
                    if not label.endswith('…'):
                        label = label.rstrip('…')
                    if font.measure(label + '…') <= remaining:
                        label += '…'
                        break
                build['label'].config(text=label)
                build['status'].pack_configure(padx=(0, round(9 * scale)))
                build['clock'].pack_configure(padx=(round(10 * scale), 0))
                total_height += row_height
            if group['divider'] is not None:
                total_height += round(7 * scale)
            total_height += 2
            heights.append((group_id, total_height))
        offset = 0
        for group_id, height in heights:
            group = self.groups[group_id]
            x = left + margin if position.endswith('left') else right - width - margin
            if position.startswith('Top'):
                y = top + margin + offset
            else:
                y = bottom - margin - height - offset
            offset += height + gap
            win = group['window']
            x, y = max(left, x), max(top, y)
            group.update(target_x=x, y=y, width=width, height=height)
            if not group['shown']:
                group['shown'] = True
                if self.settings['appearance'] == 'Slide':
                    start_x = self.offscreen_x(width)
                    group['x'] = start_x
                    self.begin_animation(group, start_x, x, 'enter')
                else:
                    group['x'] = x
            elif group['anim'] is None:
                group['x'] = x
            elif group['anim']['phase'] == 'enter':
                group['anim']['end'] = x
            if not win.winfo_viewable():
                win.geometry(f'{width}x{height}+{max(0,x)}+{y}')
                win.deiconify()
            self.place_group(group)
            win.lift()

    def offscreen_x(self, width):
        """Place the whole panel beyond the edge matching its alignment."""
        if self.settings['position'].endswith('right'):
            return self.root.winfo_screenwidth() + 2
        return -width - 2

    def place_group(self, group):
        win = group['window']
        x, y = round(group['x']), round(group['y'])
        if os.name == 'nt':
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
            ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y,
                                              group['width'], group['height'],
                                              0x0010 | 0x0004)  # NOACTIVATE | NOZORDER
        else:
            win.geometry(f'{group["width"]}x{group["height"]}+{max(0,x)}+{max(0,y)}')

    def begin_animation(self, group, start, end, phase):
        group['anim'] = {'start': start, 'end': end, 'phase': phase,
                         'since': time.monotonic()}

    def animate_groups(self, now):
        for group_id, group in list(self.groups.items()):
            anim = group['anim']
            if anim is None:
                continue
            progress = min(1.0, (now - anim['since']) / ANIMATION_SECONDS)
            eased = 1.0 - (1.0 - progress) ** 3
            group['x'] = anim['start'] + (anim['end'] - anim['start']) * eased
            self.place_group(group)
            if progress >= 1.0:
                group['anim'] = None
                if anim['phase'] == 'exit':
                    for build_id in group['rows']:
                        self.builds.pop(build_id, None)
                    group['window'].destroy()
                    del self.groups[group_id]
                    self.refresh_layout()

    def tick(self):
        if not self.running:
            return
        now = time.monotonic()
        for build in list(self.builds.values()):
            self.refresh_build(build)

        # A grouped panel is one complete build. Keep every finished project
        # visible until the solution AND all its projects have finished.
        # The disappearance delay begins at the latest completion time.
        for group_id, group in list(self.groups.items()):
            rows = [self.builds[build_id] for build_id in group['rows']
                    if build_id in self.builds]
            if not rows or any(build['finished_at'] is None for build in rows):
                continue
            completed_at = max(build['finished_at'] for build in rows)
            if now - completed_at < self.settings['delay']:
                continue
            if self.settings['appearance'] == 'Slide':
                if not group['exit_pending']:
                    group['exit_pending'] = True
                    self.begin_animation(group, group['x'],
                                         self.offscreen_x(group['width']), 'exit')
            else:
                for build_id in group['rows']:
                    self.builds.pop(build_id, None)
                group['window'].destroy()
                del self.groups[group_id]
                self.refresh_layout()

        self.animate_groups(now)
        self.root.after(16, self.tick)

    def start_test(self):
        self.counter += 1
        self.enqueue('start', f'test-{self.counter}', f'Test Project {self.counter}')

    def finish_tests(self):
        for build_id, build in list(self.builds.items()):
            if build['finished_at'] is None:
                self.enqueue('finish', build_id, True)

    def menu(self):
        item = pystray.MenuItem

        def radio(key, value):
            return lambda menu_item: self.settings[key] == value

        def setting_action(key, value):
            def action(icon, menu_item):
                self.enqueue('setting', key, value)
            return action

        size_menu = pystray.Menu(*(item(name, setting_action('size', name),
                                        checked=radio('size', name), radio=True)
                                   for name in SIZES))
        position_menu = pystray.Menu(*(item(name, setting_action('position', name),
                                            checked=radio('position', name), radio=True)
                                       for name in POSITIONS))
        appearance_menu = pystray.Menu(*(item(name, setting_action('appearance', name),
                                             checked=radio('appearance', name), radio=True)
                                         for name in APPEARANCES))
        delay_menu = pystray.Menu(*(item(f'{sec} seconds', setting_action('delay', sec),
                                         checked=radio('delay', sec), radio=True)
                                    for sec in (0, 1, 2, 3, 5, 10, 15, 30, 60)))
        return pystray.Menu(
            item('Start test stopwatch', lambda icon, entry: self.start_test()),
            item('Finish all test stopwatches', lambda icon, entry: self.finish_tests()),
            pystray.Menu.SEPARATOR,
            item('Overlay size', size_menu),
            item('Customize fonts...', lambda icon, entry: self.enqueue('font_dialog')),
            item('Build completion sounds...', lambda icon, entry: self.enqueue('sound_dialog')),
            item('Disappear after', delay_menu),
            item('Overlay position', position_menu),
            item('Appearance animation', appearance_menu),
            pystray.Menu.SEPARATOR,
            item('About...', lambda icon, entry: self.enqueue('about_dialog')),
            item('Exit', lambda icon, entry: self.enqueue('quit')),
        )

    def show_font_dialog(self):
        if self.font_dialog is not None and self.font_dialog.winfo_exists():
            self.font_dialog.lift()
            self.font_dialog.focus_force()
            return
        dialog = tk.Toplevel(self.root)
        apply_window_icon(dialog)
        self.font_dialog = dialog
        dialog.title('Visual Studio Build Stopwatch - Fonts')
        dialog.resizable(False, False)
        dialog.attributes('-topmost', True)
        dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
        container = ttk.Frame(dialog, padding=16)
        container.pack(fill='both', expand=True)
        families = sorted(set(tkfont.families(self.root)), key=str.casefold)
        values = {}
        for row, (key, title) in enumerate((('solution_font', 'Solution heading'),
                                             ('project_font', 'Project names'))):
            ttk.Label(container, text=title, font=('Segoe UI', 10, 'bold')).grid(
                row=row * 3, column=0, columnspan=3, sticky='w', pady=(0, 5))
            spec = self.settings[key]
            family = tk.StringVar(value=spec['family'])
            size = tk.StringVar(value=str(spec['size']))
            style = tk.StringVar(value=spec['style'])
            ttk.Label(container, text='Font family').grid(row=row * 3 + 1, column=0, sticky='w')
            ttk.Label(container, text='Size (pt)').grid(row=row * 3 + 1, column=1, sticky='w', padx=(12, 0))
            ttk.Label(container, text='Style').grid(row=row * 3 + 1, column=2, sticky='w', padx=(12, 0))
            ttk.Combobox(container, textvariable=family, values=families, width=25,
                         state='readonly').grid(row=row * 3 + 2, column=0, sticky='ew', pady=(0, 16))
            ttk.Spinbox(container, textvariable=size, from_=6, to=72, width=8).grid(
                row=row * 3 + 2, column=1, padx=(12, 0), sticky='w', pady=(0, 16))
            ttk.Combobox(container, textvariable=style, values=FONT_STYLES, width=13,
                         state='readonly').grid(row=row * 3 + 2, column=2, padx=(12, 0), pady=(0, 16))
            values[key] = (family, size, style)
        buttons = ttk.Frame(container)
        buttons.grid(row=6, column=0, columnspan=3, sticky='e')

        def save():
            result = {}
            for key, (family, size, style) in values.items():
                try:
                    point_size = int(size.get())
                except ValueError:
                    point_size = -1
                if not 6 <= point_size <= 72:
                    messagebox.showerror('Invalid font size', 'Font size must be between 6 and 72 points.', parent=dialog)
                    return
                result[key] = {'family': family.get(), 'size': point_size, 'style': style.get()}
            self.settings.update(result)
            save_settings(self.settings)
            self.refresh_layout()
            dialog.destroy()

        ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='right', padx=(8, 0))
        ttk.Button(buttons, text='Save', command=save).pack(side='right')
        dialog.update_idletasks()
        dialog.geometry(f'+{max(0, (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2)}+'
                        f'{max(0, (dialog.winfo_screenheight() - dialog.winfo_height()) // 2)}')
        dialog.lift()
        dialog.focus_force()

    def show_sound_dialog(self):
        if self.sound_dialog is not None and self.sound_dialog.winfo_exists():
            self.sound_dialog.lift()
            self.sound_dialog.focus_force()
            return
        dialog = tk.Toplevel(self.root)
        apply_window_icon(dialog)
        self.sound_dialog = dialog
        dialog.title('Visual Studio Build Stopwatch - Sounds')
        dialog.resizable(False, False)
        dialog.attributes('-topmost', True)
        dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
        container = ttk.Frame(dialog, padding=16)
        container.pack(fill='both', expand=True)
        ttk.Label(container, text='Choose WAV, MP3, OGG or FLAC files. Leave blank for no sound.').grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 12))
        values = {}
        for row, (key, title) in enumerate((('sound_success', 'Build succeeded'),
                                             ('sound_failure', 'Build failed')), start=1):
            ttk.Label(container, text=title).grid(row=row, column=0, sticky='w', padx=(0, 10), pady=6)
            var = tk.StringVar(value=os.path.normpath(self.settings.get(key, '')) if self.settings.get(key) else '')
            values[key] = var
            UndoableEntry(container, textvariable=var, width=54).grid(row=row, column=1, sticky='ew', pady=6)
            controls = ttk.Frame(container)
            controls.grid(row=row, column=2, padx=(8, 0))
            def browse(v=var):
                path = filedialog.askopenfilename(
                    parent=dialog,
                    title='Choose a build completion sound',
                    filetypes=[
                        ('All supported audio', '*.wav *.mp3 *.ogg *.flac'),
                        ('WAV audio', '*.wav'),
                        ('MP3 audio', '*.mp3'),
                        ('OGG Vorbis audio', '*.ogg'),
                        ('FLAC audio', '*.flac'),
                        ('All files', '*.*'),
                    ])
                if path:
                    v.set(os.path.normpath(path))
            def preview(v=var):
                path = v.get().strip().strip('"')
                if not path:
                    messagebox.showinfo('No sound selected', 'Choose an audio file first.', parent=dialog)
                    return
                self.play_audio(path, preview=True)
            ttk.Button(controls, text='Browse...', command=browse).pack(side='left')
            ttk.Button(controls, text='Test', command=preview).pack(side='left', padx=(6, 0))
            ttk.Button(controls, text='Clear', command=lambda v=var: v.set('')).pack(side='left', padx=(6, 0))
        buttons = ttk.Frame(container)
        buttons.grid(row=3, column=0, columnspan=3, sticky='e', pady=(14, 0))
        def save():
            result = {key: os.path.normpath(var.get().strip().strip('"')) if var.get().strip().strip('"') else ''
                      for key, var in values.items()}
            for key, path in result.items():
                if path and (not os.path.isfile(path) or Path(path).suffix.lower() not in SUPPORTED_AUDIO):
                    messagebox.showerror('Invalid sound file',
                                         'Please select an existing WAV, MP3, OGG or FLAC file for ' +
                                         ('Build succeeded' if key == 'sound_success' else 'Build failed') + '.',
                                         parent=dialog)
                    return
            self.settings.update(result)
            save_settings(self.settings)
            dialog.destroy()
        ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='right', padx=(8, 0))
        ttk.Button(buttons, text='Save', command=save).pack(side='right')
        dialog.update_idletasks()
        dialog.geometry(f'+{max(0, (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2)}+'
                        f'{max(0, (dialog.winfo_screenheight() - dialog.winfo_height()) // 2)}')
        dialog.lift()
        dialog.focus_force()

    def show_about_dialog(self):
        if self.about_dialog is not None and self.about_dialog.winfo_exists():
            self.about_dialog.lift()
            self.about_dialog.focus_force()
            return

        dialog = tk.Toplevel(self.root)
        apply_window_icon(dialog)
        self.about_dialog = dialog
        dialog.title(f'About {APP_NAME}')
        dialog.resizable(False, False)
        dialog.attributes('-topmost', True)
        dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)

        container = ttk.Frame(dialog, padding=20)
        container.pack(fill='both', expand=True)
        ttk.Label(container, text=APP_NAME, font=('Segoe UI', 12, 'bold')).pack(pady=(0, 8))
        ttk.Label(container, text=f'Version {get_app_version()}').pack(pady=(0, 8))
        ttk.Label(container, text='Written by Gal Shemesh (PacketLauncher)').pack(pady=(0, 12))
        link = tk.Label(container, text='github.com/packetlauncher', fg='#0066cc',
                        cursor='hand2', font=('Segoe UI', 10, 'underline'))
        link.pack(pady=(0, 16))
        link.bind('<Button-1>', lambda event: webbrowser.open(GITHUB_URL))
        ttk.Button(container, text='OK', command=dialog.destroy, width=12).pack()

        dialog.update_idletasks()
        dialog.geometry(f'+{max(0, (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2)}+'
                        f'{max(0, (dialog.winfo_screenheight() - dialog.winfo_height()) // 2)}')
        dialog.lift()
        dialog.focus_force()

    def shutdown(self):
        self.running = False
        if self.tray:
            self.tray.stop()
        for group in self.groups.values():
            group['window'].destroy()
        self.root.quit()

    def run(self):
        self.tray = pystray.Icon('VSBuildStopwatch', tray_image(), APP_NAME, self.menu())
        thread = threading.Thread(target=self.tray.run, daemon=True)
        thread.start()
        self.root.mainloop()


if __name__ == '__main__':
    if os.name == 'nt':
        enable_dpi_awareness()
    StopwatchApp().run()
