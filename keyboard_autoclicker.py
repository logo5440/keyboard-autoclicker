"""
Keyboard Autoclicker (like OP Auto Clicker, but for keyboard keys)

Modes:    Click (press a key repeatedly) and Hold key (keep a key pressed)
Install:  pip install pynput
Run:      python keyboard_autoclicker.py
Options:  --minimized   start minimized (used by the autostart entry)
"""
import atexit
import ctypes
import json
import math
import os
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, colorchooser, filedialog

try:
    from pynput.keyboard import Controller, Key, KeyCode, Listener
except ImportError:
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror("Keyboard Autoclicker",
                         "The package 'pynput' is missing.\n\nInstall it with:  pip install pynput")
    sys.exit(1)

try:
    import winreg
except ImportError:  # not Windows
    winreg = None

try:
    from PIL import Image          # optional: needed for background images
except Exception:
    Image = None

APP_NAME = "KeyboardAutoclicker"
IS_WIN = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MAX_SECONDS = 30 * 24 * 3600   # longest allowed time value: 30 days
UI_FONT = "Segoe UI"
REPEAT_DELAY = 0.5             # hold mode auto-repeat: first repeat after ...
REPEAT_INTERVAL = 0.033        # ... then about 30 repeats per second

# Sharp rendering on high-DPI screens (Windows)
if IS_WIN:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

# Own taskbar identity so Windows shows the app icon instead of the Python icon
if IS_WIN:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KeyboardAutoclicker.App")
    except Exception:
        pass


def resource_path(name):
    """Path to a bundled file (also works inside the built EXE)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def set_window_icon(window):
    """App icon for window, title bar and taskbar (silently ignored if not possible)."""
    if not IS_WIN:
        return
    try:
        path = resource_path("icon.ico")
        if os.path.exists(path):
            window.iconbitmap(default=path)
    except Exception:
        pass


kb = Controller()

# --------------------------------------------- pressing / safely releasing keys
# Every key pressed by the program is remembered, so it is guaranteed to be
# released again, even after a crash or when the program exits (no stuck keys).
_pressed = set()
_pressed_lock = threading.Lock()


def press_key(key):
    kb.press(key)
    with _pressed_lock:
        _pressed.add(key)


def release_key(key):
    try:
        kb.release(key)
    finally:
        with _pressed_lock:
            _pressed.discard(key)


def release_all():
    with _pressed_lock:
        keys = list(_pressed)
        _pressed.clear()
    for k in keys:
        try:
            kb.release(k)
        except Exception:
            pass


def press_combo(keys):
    """Press modifiers first, then the main key (e.g. ctrl, shift, a)."""
    done = []
    try:
        for k in keys:
            press_key(k)
            done.append(k)
    except Exception:
        release_combo(done)
        raise


def release_combo(keys):
    """Release in reverse order. Every key is released even if one of them fails."""
    error = None
    for k in reversed(keys):
        try:
            release_key(k)
        except Exception as e:
            error = error or e
    if error:
        raise error


def key_name_of(k):
    """Name of a key object as used in combinations ('a', 'ctrl', 'vk96' ...)."""
    if isinstance(k, str):
        return k.lower()
    return key_to_str(k)


atexit.register(release_all)

# ------------------------------------------------------------------- keys

ALIASES = {
    "spacebar": "space", "return": "enter", "escape": "esc", "control": "ctrl",
    "del": "delete", "pgup": "page_up", "pgdn": "page_down", "win": "cmd",
    "capslock": "caps_lock",
}

KEY_LABELS = {
    "space": "SPACE", "enter": "ENTER", "esc": "ESC", "tab": "TAB", "backspace": "BKSP",
    "delete": "DEL", "up": "↑", "down": "↓", "left": "←", "right": "→",
    "shift": "SHIFT", "ctrl": "CTRL", "alt": "ALT", "cmd": "WIN", "caps_lock": "CAPS",
    "page_up": "PGUP", "page_down": "PGDN",
}


def normalize(name):
    """Comparison form of a key name (lower case, aliases resolved)."""
    n = name.strip().lower()
    return ALIASES.get(n, n)


def single_label(name):
    """Text for one key on the on-screen key cap."""
    n = normalize(name)
    if not n:
        return "?"
    if n in KEY_LABELS:
        return KEY_LABELS[n]
    if len(n) == 1:
        return n.upper()
    return n.upper().replace("_", " ")


def parse_key(name):
    """Text -> pynput key (e.g. 'a', 'space', 'enter', 'f5', 'vk96')."""
    name = name.strip()
    if not name:
        raise ValueError("Please enter a key.")
    if len(name) == 1:
        return name.lower() if len(name.lower()) == 1 else name
    n = normalize(name)
    if n.startswith("vk") and n[2:].isdigit():
        return KeyCode.from_vk(int(n[2:]))
    key = Key.__members__.get(n)
    if key is None:
        raise ValueError(f"Unknown key: {name}")
    return key


def key_to_str(key):
    """pynput key -> text (or None if it cannot be identified)."""
    if isinstance(key, KeyCode):
        vk = key.vk
        # letters and digits by their key code, so that ctrl+c is 'c' and shift+1 is '1'
        if vk is not None and (65 <= vk <= 90 or 48 <= vk <= 57):
            return chr(vk).lower()
        if key.char and key.char.isprintable():
            return key.char.lower()
        return f"vk{vk}" if vk is not None else None
    return getattr(key, "name", None)


MODIFIERS = ("ctrl", "alt", "shift", "cmd")
MOD_ALIASES = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift_l": "shift", "shift_r": "shift", "cmd_l": "cmd", "cmd_r": "cmd",
}


def modifier_of(name):
    """'ctrl', 'alt', 'shift' or 'cmd' if the key is a modifier, else None."""
    n = MOD_ALIASES.get(name, name) if name else None
    return n if n in MODIFIERS else None


def split_combo(text):
    """'Ctrl + Shift + A' -> ['ctrl', 'shift', 'a'] (modifiers sorted, main key last)."""
    t = (text or "").strip()
    if not t:
        raise ValueError("Please enter a key.")
    if t == "+":
        parts = ["+"]
    elif t.endswith("++"):
        parts = t[:-2].split("+") + ["+"]
    else:
        parts = t.split("+")
    names = []
    for p in parts:
        p = p.strip()
        if not p:
            raise ValueError(f"Invalid key combination: {text}")
        n = normalize(p)
        names.append(MOD_ALIASES.get(n, n))
    mods, main = names[:-1], names[-1]
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"“{m}” is not a modifier key. Only ctrl, alt, shift or win "
                             "can come before a +.")
    if len(set(mods)) != len(mods) or main in mods:
        raise ValueError("A key appears twice in the combination.")
    return sorted(mods, key=MODIFIERS.index) + [main]


def combo_id(text):
    """Comparable form of a key or combination ('ctrl+shift+a')."""
    try:
        return "+".join(split_combo(text))
    except ValueError:
        return normalize(text or "")


def parse_combo(text):
    """Text -> list of pynput keys, modifiers first."""
    names = split_combo(text)
    keys = [parse_key(n) for n in names]
    main = names[-1]
    # In a shortcut (ctrl+c ...) send letters and digits as Windows key codes,
    # so that every program recognises the shortcut.
    if IS_WIN and len(names) > 1 and len(main) == 1 and main.isascii() and main.isalnum():
        keys[-1] = KeyCode.from_vk(ord(main.upper()))
    return keys


def key_label(text):
    """Text shown on the on-screen key cap ('CTRL + C')."""
    try:
        return " + ".join(single_label(p) for p in split_combo(text))
    except ValueError:
        return single_label(text or "")


def format_duration(secs):
    secs = max(0, int(secs))
    h, rest = divmod(secs, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def screen_rect(root):
    """(left, top, width, height) of the whole desktop (all monitors on Windows)."""
    if IS_WIN:
        try:
            gsm = ctypes.windll.user32.GetSystemMetrics
            w, h = gsm(78), gsm(79)          # SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN
            if w > 0 and h > 0:
                return gsm(76), gsm(77), w, h  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN
        except Exception:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def format_ms(ms):
    """Short, readable interval: 100 ms, 1.5 s, 2 min."""
    if ms < 1000:
        return f"{round(ms, 1):g} ms"
    if ms < 60000:
        return f"{round(ms / 1000, 2):g} s"
    if ms < 3600000:
        return f"{round(ms / 60000, 2):g} min"
    return f"{round(ms / 3600000, 2):g} h"


# ----------------------------------------------------------------- colors

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def is_hex(value):
    return isinstance(value, str) and bool(HEX_RE.match(value))


def hex_to_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix(a, b, t):
    """Blend color a towards color b (t = 0 gives a, t = 1 gives b)."""
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex([ra[i] + (rb[i] - ra[i]) * t for i in range(3)])


def luminance(h):
    def f(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = hex_to_rgb(h)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast_fg(bg):
    """White or near-black, whichever is easier to read on the color bg."""
    return "#ffffff" if luminance(bg) < 0.179 else "#0b1020"


PALETTES = {
    "light": dict(bg="#f3f4f6", card="#ffffff", fg="#1f2328", muted="#6b7280",
                  field="#f3f4f6", border="#d8dce2",
                  btn="#eceef1", btn_hover="#e0e3e8", btn_active="#d3d7de",
                  accent="#2563eb", accent_hover="#1d4fd8", accent_dim="#93b4f5",
                  accent_fg="#ffffff", success="#15803d",
                  danger="#dc2626", danger_hover="#b91c1c", danger_fg="#ffffff"),
    "dark": dict(bg="#14161a", card="#1e2127", fg="#e6e8eb", muted="#8b93a1",
                 field="#2a2e36", border="#323843",
                 btn="#2c313a", btn_hover="#373d48", btn_active="#424956",
                 accent="#4f8cff", accent_hover="#6b9fff", accent_dim="#2f528f",
                 accent_fg="#0b1020", success="#3fb950",
                 danger="#f85149", danger_hover="#ff6b63", danger_fg="#ffffff"),
}

ACCENT_PRESETS = (
    ("Blue", "#2563eb"), ("Indigo", "#4f46e5"), ("Purple", "#7c3aed"), ("Pink", "#db2777"),
    ("Red", "#dc2626"), ("Orange", "#ea580c"), ("Green", "#16a34a"), ("Teal", "#0d9488"),
)


def build_palette(dark, accent="", overrides=None):
    """Color palette for the light or dark theme.

    accent    -- optional '#rrggbb' accent color (empty = theme default)
    overrides -- optional dict with '#rrggbb' values for 'bg', 'card' and 'fg'
    """
    p = dict(PALETTES["dark" if dark else "light"])
    ov = {k: v.lower() for k, v in (overrides or {}).items() if k in ("bg", "card", "fg") and is_hex(v)}
    if ov:
        p.update(ov)
        p["muted"] = mix(p["fg"], p["card"], 0.45)
        p["border"] = mix(p["card"], p["fg"], 0.16)
        p["field"] = mix(p["card"], p["fg"], 0.07)
        p["btn"] = mix(p["card"], p["fg"], 0.09)
        p["btn_hover"] = mix(p["card"], p["fg"], 0.15)
        p["btn_active"] = mix(p["card"], p["fg"], 0.22)
    if is_hex(accent):
        accent = accent.lower()
        p["accent"] = accent
        p["accent_hover"] = mix(accent, "#ffffff" if dark else "#000000", 0.16)
        p["accent_dim"] = mix(accent, p["card"], 0.55)
        p["accent_fg"] = contrast_fg(accent)
    return p


def system_is_dark():
    """True if Windows uses dark mode for apps."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def set_titlebar_dark(window, dark):
    """Dark title bar on Windows 10/11 (silently ignored if not possible)."""
    if not IS_WIN:
        return
    try:
        from ctypes import wintypes
        window.update_idletasks()
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
        value = ctypes.c_int(1 if dark else 0)
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                              ctypes.c_void_p, wintypes.DWORD]
        for attr in (20, 19):  # 20 = Windows 10 20H1+/11, 19 = older builds
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value),
                                         ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


# --------------------------------------------------------------- settings

SCALES = ("80", "90", "100", "125", "150")
BG_MODES = ("color", "transparent", "image")
BG_STRENGTHS = ("25", "50", "75", "100")
OPACITIES = ("100", "90", "80", "70", "60")
GEOM_RE = re.compile(r"^(\d{2,5})x(\d{2,5})(?:\+(-?\d{1,6})\+(-?\d{1,6}))?$")

DEFAULTS = {
    "action": "click", "h": "0", "m": "0", "s": "0", "ms": "100", "jitter": "0",
    "key": "space", "hold": "10", "mode": "inf", "count": "100",
    "hold_for": "0", "autorepeat": "1",
    "hotkey": "f6", "theme": "system", "animations": "1",
    "scale": "100", "topmost": "0", "compact": "0", "geometry": "",
    "delay": "3", "limit": "0", "panic_esc": "1", "panic_corner": "1",
    "bg_mode": "color", "bg_strength": "50", "opacity": "100",
    "accent": "", "light_bg": "", "light_card": "", "light_fg": "",
    "dark_bg": "", "dark_card": "", "dark_fg": "",
}
CHOICES = {
    "action": ("click", "hold"), "mode": ("inf", "n"), "autorepeat": ("0", "1"),
    "theme": ("system", "light", "dark"), "animations": ("0", "1"),
    "scale": SCALES, "topmost": ("0", "1"), "compact": ("0", "1"),
    "panic_esc": ("0", "1"), "panic_corner": ("0", "1"),
    "bg_mode": BG_MODES, "bg_strength": BG_STRENGTHS, "opacity": OPACITIES,
}
HEX_KEYS = ("accent", "light_bg", "light_card", "light_fg", "dark_bg", "dark_card", "dark_fg")


def settings_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_NAME, "settings.json")


def load_settings():
    data = dict(DEFAULTS)
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for k in DEFAULTS:
                v = raw.get(k)
                good = isinstance(v, str) and len(v) <= 40
                if good and k in CHOICES:
                    good = v in CHOICES[k]
                if good and k in HEX_KEYS:
                    good = v == "" or is_hex(v)
                if good and k == "geometry":
                    good = v == "" or bool(GEOM_RE.match(v))
                if good:
                    data[k] = v
    except (OSError, ValueError):
        pass
    return data


def log_error(text):
    """Append an error to %APPDATA%\\KeyboardAutoclicker\\error.log."""
    try:
        path = os.path.join(os.path.dirname(settings_path()), "error.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + text.rstrip() + "\n\n")
    except OSError:
        pass


def background_path():
    """The program keeps its own copy of the chosen background picture here."""
    return os.path.join(os.path.dirname(settings_path()), "background.png")


def transparent_key(color):
    """A color that is almost the background color, used as the 'see-through' color."""
    r, g, b = hex_to_rgb(color)
    return rgb_to_hex((r + 1 if r < 255 else r - 1, g, b + 1 if b < 255 else b - 1))


def save_settings(data):
    try:
        path = settings_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def sanitize_geometry(geom, screen_w, screen_h, min_w=200, min_h=150):
    """Saved window geometry made safe: size fits the screen, position is on screen.

    Returns 'WxH+X+Y', 'WxH' (position dropped) or None (unusable).
    """
    m = GEOM_RE.match(geom or "")
    if not m:
        return None
    w = max(min_w, min(int(m.group(1)), screen_w))
    h = max(min_h, min(int(m.group(2)), screen_h))
    if m.group(3) is None:
        return f"{w}x{h}"
    x, y = int(m.group(3)), int(m.group(4))
    if -100 <= x <= screen_w - 80 and -50 <= y <= screen_h - 80:
        return f"{w}x{h}+{x}+{y}"
    return f"{w}x{h}"     # window would be off screen (e.g. monitor removed)


# --------------------------------------------------------------- autostart

def autostart_command():
    """Command that is run when you sign in to Windows."""
    if getattr(sys, "frozen", False):                  # built EXE
        return f'"{sys.executable}" --minimized'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")  # no console window
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.abspath(__file__)}" --minimized'


def autostart_get():
    """Current autostart entry or None."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, APP_NAME)[0]
    except OSError:
        return None


def set_autostart(enabled):
    if winreg is None:
        raise OSError("Autostart is only available on Windows.")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if enabled:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


# ------------------------------------------------------- single instance only

_mutex_handle = None


def already_running():
    global _mutex_handle
    if not IS_WIN:
        return False
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        _mutex_handle = k32.CreateMutexW(None, False, "Local\\KeyboardAutoclickerMutex")
        return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return False


# -------------------------------------------------------------- animations

def ease(t):
    """Smooth start and stop (smoothstep)."""
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


class Tween:
    """A value that moves towards a target at a constant speed (1.0 per 'duration' seconds)."""

    def __init__(self, value=0.0, duration=0.15):
        self.value = float(value)
        self.target = float(value)
        self.duration = duration

    def set(self, target, immediate=False):
        self.target = float(target)
        if immediate:
            self.value = self.target

    @property
    def eased(self):
        return ease(self.value)

    @property
    def active(self):
        return self.value != self.target

    def step(self, dt):
        """Advance by dt seconds. Returns True while the value is still moving."""
        if self.value == self.target:
            return False
        d = dt / self.duration if self.duration > 0 else 1.0
        if self.value < self.target:
            self.value = min(self.target, self.value + d)
        else:
            self.value = max(self.target, self.value - d)
        return self.value != self.target


class Animator:
    """One shared timer that drives all running animations (and sleeps when idle)."""
    FRAME_MS = 16

    def __init__(self, root):
        self.root = root
        self.items = []
        self.scheduled = False
        self.last = 0.0

    def add(self, widget):
        if widget not in self.items:
            self.items.append(widget)
        if not self.scheduled:
            self.scheduled = True
            self.last = time.monotonic()
            self.root.after(self.FRAME_MS, self.tick)

    def tick(self):
        self.scheduled = False
        now = time.monotonic()
        dt = min(0.1, now - self.last)
        self.last = now
        still = []
        for w in self.items:
            try:
                if w.step(dt):
                    still.append(w)
            except Exception:
                pass
        self.items = still
        if self.items:
            self.scheduled = True
            self.root.after(self.FRAME_MS, self.tick)


class UI:
    """Shared state of all custom widgets: palette, scaling and animations."""

    def __init__(self, root):
        self.root = root
        self.animator = Animator(root)
        self.animate = True
        self.p = dict(PALETTES["light"])
        self.widgets = []
        self.bg_frames = []
        self.backdrop = Backdrop()
        self.dpi = 1.0   # screen scaling (1.0 = 96 DPI)
        try:
            self.dpi = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
        except Exception:
            pass
        self.scale = 1.0  # window size chosen in the settings (1.0 = 100 %)
        self.k = self.dpi

    def set_scale(self, scale):
        self.scale = scale
        self.k = self.dpi * scale

    def font(self, size, weight="normal"):
        """Font given in pixels, so that it follows the chosen window size exactly."""
        return (UI_FONT, -max(6, int(round(size * 96 / 72 * self.k))), weight)

    def px(self, n):
        return int(round(n * self.k))

    def register(self, widget):
        self.widgets.append(widget)

    def set_palette(self, palette):
        self.p = palette
        for w in self.widgets:
            w.redraw()


class Backdrop:
    """The background picture: scaled to cover the window and dimmed with the background color."""

    def __init__(self):
        self.src = None          # PIL image (RGB) of the picture
        self.enabled = False
        self.cover = None        # picture at window size
        self.version = 0
        self._cover_key = None

    def active(self):
        return self.enabled and self.cover is not None

    def load(self, path):
        """Load the program's copy of the picture. False if not possible."""
        if Image is None or not os.path.exists(path):
            return False
        try:
            with Image.open(path) as im:
                self.src = im.convert("RGB")
            self._cover_key = None
            return True
        except Exception:
            self.src = None
            return False

    def update_cover(self, w, h, base, strength):
        """Recalculate the picture for a window of w x h pixels (only when something changed)."""
        key = (w, h, base, strength, self.enabled, id(self.src))
        if key == self._cover_key:
            return False
        self._cover_key = key
        self.version += 1
        if not (self.enabled and self.src is not None and w > 1 and h > 1):
            self.cover = None
            return True
        sw, sh = self.src.size
        f = max(w / sw, h / sh)                     # "cover": fill the window, crop the rest
        nw, nh = max(w, int(round(sw * f))), max(h, int(round(sh * f)))
        img = self.src.resize((nw, nh), Image.BILINEAR)
        left, top = (nw - w) // 2, (nh - h) // 2
        img = img.crop((left, top, left + w, top + h))
        if strength < 100:                          # dim the picture so the text stays readable
            img = Image.blend(Image.new("RGB", (w, h), base), img, strength / 100.0)
        self.cover = img
        return True

    def slice(self, x, y, w, h):
        """The part of the picture that lies under a widget at x, y (window coordinates)."""
        if not self.active() or w < 1 or h < 1:
            return None
        cw, ch = self.cover.size
        box = (max(0, x), max(0, y), min(cw, x + w), min(ch, y + h))
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        part = self.cover.crop(box)
        if (x, y, x + w, y + h) != box:            # widget sticks out: pad with the edge color
            canvas = Image.new("RGB", (w, h), self.cover.getpixel((min(cw - 1, max(0, x)), min(ch - 1, max(0, y)))))
            canvas.paste(part, (box[0] - x, box[1] - y))
            part = canvas
        from PIL import ImageTk
        return ImageTk.PhotoImage(part)


def round_points(x0, y0, x1, y1, r):
    """Polygon points of a rounded rectangle (draw with smooth=True)."""
    return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
            x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]


class CanvasWidget(tk.Canvas):
    """Base class of all custom widgets (drawn on a canvas, animated by tweens)."""

    def __init__(self, parent, ui, width, height, bgkey="card"):
        super().__init__(parent, width=ui.px(width), height=ui.px(height),
                         highlightthickness=0, bd=0, bg=ui.p[bgkey])
        self.ui = ui
        self.bgkey = bgkey
        self.w, self.h = ui.px(width), ui.px(height)
        self.enabled = True
        ui.register(self)
        self.bind("<Configure>", self.on_configure)

    def on_configure(self, e):
        self.w, self.h = e.width, e.height
        self.redraw()

    def paint_bg(self):
        """Background of the widget: its color, plus the background picture where it shows."""
        set_bg_color(self, self.ui.p[self.bgkey])
        bd = self.ui.backdrop
        if self.bgkey != "bg" or not bd.active():
            return
        x = self.winfo_rootx() - self.ui.root.winfo_rootx()
        y = self.winfo_rooty() - self.ui.root.winfo_rooty()
        key = (x, y, self.w, self.h, bd.version)
        if key != getattr(self, "_bg_key", None):
            self._bg_key = key
            self._bg_img = bd.slice(x, y, self.w, self.h)
        if getattr(self, "_bg_img", None) is not None:
            self.create_image(0, 0, image=self._bg_img, anchor="nw")

    def tweens(self):
        return ()

    def detach(self):
        """Remove variable traces before the widget is thrown away."""
        var, tid = getattr(self, "variable", None), getattr(self, "_trace", None)
        if var is not None and tid:
            try:
                var.trace_remove("write", tid)
            except Exception:
                pass
            self._trace = None

    def after_step(self):
        pass

    def tween(self, tw, target):
        """Move a tween to a new target (animated, or instantly if animations are off)."""
        tw.set(target, immediate=not self.ui.animate)
        if tw.active:
            self.ui.animator.add(self)
        else:
            self.redraw()

    def step(self, dt):
        for tw in self.tweens():
            tw.step(dt)
        self.after_step()
        self.redraw()
        return any(tw.active for tw in self.tweens())

    def state(self, flags=None):
        """ttk-compatible: state(['disabled']) / state(['!disabled'])."""
        if flags is None:
            return () if self.enabled else ("disabled",)
        for f in flags:
            if f == "disabled":
                self.enabled = False
            elif f == "!disabled":
                self.enabled = True
        self.apply_enabled()
        return None

    def apply_enabled(self):
        try:
            self.configure(cursor="hand2" if self.enabled else "arrow")
        except tk.TclError:
            pass
        self.redraw()

    def redraw(self):
        raise NotImplementedError


class RoundButton(CanvasWidget):
    """Rounded button / segment with animated hover, press and color changes."""

    def __init__(self, parent, ui, text, command=None, kind="secondary", width=100, height=36,
                 bgkey="card", font_size=10, bold=False, variable=None, value=None, radius=6):
        super().__init__(parent, ui, width, height, bgkey)
        self.text, self.command = text, command
        self.kind, self.prev_kind = kind, kind
        self.font_size, self.bold, self.radius = font_size, bold, radius
        self.variable, self.value = variable, value
        self.hover, self.press = Tween(0, 0.12), Tween(0, 0.07)
        self.kind_t, self.sel = Tween(1, 0.25), Tween(0, 0.18)
        if variable is not None:               # segment of a segmented switch
            self.kind = self.prev_kind = "segment"
            self.sel.set(1 if variable.get() == value else 0, immediate=True)
            self._trace = variable.trace_add("write", self.on_var)
        for seq, fn in (("<Enter>", self.on_enter), ("<Leave>", self.on_leave),
                        ("<ButtonPress-1>", self.on_down), ("<ButtonRelease-1>", self.on_up)):
            self.bind(seq, fn)
        self.configure(cursor="hand2")
        self.redraw()

    def tweens(self):
        return (self.hover, self.press, self.kind_t, self.sel)

    def kind_colors(self, kind):
        """(base, hover, pressed, text, border)"""
        p = self.ui.p
        if kind == "accent":
            return (p["accent"], p["accent_hover"], p["accent_hover"], p["accent_fg"], p["accent"])
        if kind == "danger":
            return (p["danger"], p["danger_hover"], p["danger_hover"], p["danger_fg"], p["danger"])
        return (p["btn"], p["btn_hover"], p["btn_active"], p["fg"], p["border"])

    def colors(self):
        """(fill, text, border) for the current animation state."""
        p = self.ui.p
        if self.kind == "segment":
            a, b, t = self.kind_colors("secondary"), self.kind_colors("accent"), self.sel.eased
            cur = tuple(mix(x, y, t) for x, y in zip(a, b))
        else:
            cur = self.kind_colors(self.kind)
            if self.kind_t.value < 1:           # fade from the previous kind (Start <-> Stop)
                old, t = self.kind_colors(self.prev_kind), self.kind_t.eased
                cur = tuple(mix(x, y, t) for x, y in zip(old, cur))
        base, hov, prs, fg, border = cur
        if not self.enabled:
            t = self.sel.eased if self.kind == "segment" else 0
            return mix(p["card"], p["accent_dim"], t), mix(p["muted"], p["accent_fg"], t), p["border"]
        fill = mix(mix(base, hov, self.hover.eased), prs, self.press.eased)
        return fill, fg, border

    def redraw(self):
        try:
            self.delete("all")
            self.paint_bg()
            fill, fg, border = self.colors()
            inset = 1 + 1.5 * self.ui.k * self.press.eased
            self.create_polygon(round_points(inset, inset, self.w - inset, self.h - inset,
                                             self.ui.px(self.radius)),
                                smooth=True, fill=fill, outline=border)
            self.create_text(self.w / 2, self.h / 2, text=self.text, fill=fg,
                             font=self.ui.font(self.font_size, "bold" if self.bold else "normal"))
        except tk.TclError:
            pass

    def on_enter(self, e):
        if self.enabled:
            self.tween(self.hover, 1)

    def on_leave(self, e):
        self.tween(self.hover, 0)
        self.tween(self.press, 0)

    def on_down(self, e):
        if self.enabled:
            self.tween(self.press, 1)

    def on_up(self, e):
        armed = self.press.target == 1
        self.tween(self.press, 0)
        if self.enabled and armed and 0 <= e.x < self.w and 0 <= e.y < self.h:
            self.activate()

    def activate(self):
        if self.variable is not None:
            self.variable.set(self.value)
        if self.command:
            self.command()

    def on_var(self, *_):
        self.tween(self.sel, 1 if self.variable.get() == self.value else 0)

    def set_text(self, text):
        self.text = text
        self.redraw()

    def set_kind(self, kind):
        if kind == self.kind:
            return
        self.prev_kind, self.kind = self.kind, kind
        self.kind_t.set(0, immediate=True)
        self.tween(self.kind_t, 1)

    def apply_enabled(self):
        if not self.enabled:
            self.hover.set(0, immediate=True)
            self.press.set(0, immediate=True)
        super().apply_enabled()


class ToggleCheck(CanvasWidget):
    """Checkbox with an animated check mark."""

    def __init__(self, parent, ui, text, variable, command=None, width=300, height=28, bgkey="card"):
        super().__init__(parent, ui, width, height, bgkey)
        self.text, self.variable, self.command = text, variable, command
        self.on, self.hover = Tween(1 if variable.get() else 0, 0.16), Tween(0, 0.12)
        self._trace = variable.trace_add("write", self.on_var)
        self.bind("<Enter>", lambda e: self.enabled and self.tween(self.hover, 1))
        self.bind("<Leave>", lambda e: self.tween(self.hover, 0))
        self.bind("<ButtonRelease-1>", self.on_click)
        self.configure(cursor="hand2")
        self.redraw()

    def tweens(self):
        return (self.on, self.hover)

    def on_var(self, *_):
        self.tween(self.on, 1 if self.variable.get() else 0)

    def on_click(self, e):
        if self.enabled and 0 <= e.x < self.w and 0 <= e.y < self.h:
            self.variable.set(not self.variable.get())
            if self.command:
                self.command()

    def redraw(self):
        try:
            self.delete("all")
            p, k = self.ui.p, self.ui.k
            self.paint_bg()
            s, x0 = self.ui.px(18), self.ui.px(2)
            y0 = (self.h - s) / 2
            t = self.on.eased
            if self.enabled:
                fill = mix(p["field"], p["accent"], t)
                border = mix(mix(p["border"], p["fg"], 0.35 * self.hover.eased), p["accent"], t)
                fg = p["fg"]
            else:
                fill, border, fg = mix(p["card"], p["accent_dim"], t), p["border"], p["muted"]
            self.create_polygon(round_points(x0, y0, x0 + s, y0 + s, self.ui.px(4)),
                                smooth=True, fill=fill, outline=border)
            v = self.on.value
            if v > 0:
                p1, p2, p3 = (x0 + .22 * s, y0 + .53 * s), (x0 + .42 * s, y0 + .72 * s), (x0 + .78 * s, y0 + .30 * s)
                a = min(1.0, v * 2)
                e1 = (p1[0] + (p2[0] - p1[0]) * a, p1[1] + (p2[1] - p1[1]) * a)
                self.create_line(p1[0], p1[1], e1[0], e1[1], fill=p["accent_fg"], width=2 * k, capstyle="round")
                if v > 0.5:
                    b = min(1.0, (v - 0.5) * 2)
                    e2 = (p2[0] + (p3[0] - p2[0]) * b, p2[1] + (p3[1] - p2[1]) * b)
                    self.create_line(p2[0], p2[1], e2[0], e2[1], fill=p["accent_fg"], width=2 * k, capstyle="round")
            self.create_text(x0 + s + self.ui.px(10), self.h / 2, text=self.text, anchor="w",
                             fill=fg, font=self.ui.font(10))
        except tk.TclError:
            pass


class TabBar(CanvasWidget):
    """Tab bar with an accent underline that slides to the selected tab."""

    def __init__(self, parent, ui, labels, command=None, height=40, bgkey="bg"):
        super().__init__(parent, ui, 300, height, bgkey)
        self.labels, self.command, self.current = list(labels), command, 0
        self.slide = Tween(1, 0.22)
        self.from_pos, self.to_pos = 0.0, 0.0
        self.hov = [Tween(0, 0.12) for _ in self.labels]
        for seq, fn in (("<Motion>", self.on_motion), ("<Leave>", self.on_leave),
                        ("<ButtonRelease-1>", self.on_click)):
            self.bind(seq, fn)
        self.configure(cursor="hand2")
        self.redraw()

    def tweens(self):
        return [self.slide] + self.hov

    def current_pos(self):
        return self.from_pos + (self.to_pos - self.from_pos) * self.slide.eased

    def tab_at(self, x):
        n = len(self.labels)
        return max(0, min(n - 1, int(x * n / max(1, self.w))))

    def on_motion(self, e):
        i = self.tab_at(e.x)
        for j, tw in enumerate(self.hov):
            self.tween(tw, 1 if j == i else 0)

    def on_leave(self, e):
        for tw in self.hov:
            self.tween(tw, 0)

    def on_click(self, e):
        self.select(self.tab_at(e.x))

    def select(self, i, notify=True, animate=True):
        self.current = i
        if animate:
            self.from_pos = self.current_pos()
            self.to_pos = float(i)
            self.slide.set(0, immediate=True)
            self.tween(self.slide, 1)
        else:
            self.from_pos = self.to_pos = float(i)
            self.slide.set(1, immediate=True)
            self.redraw()
        if notify and self.command:
            self.command(i)

    def redraw(self):
        try:
            self.delete("all")
            p, k = self.ui.p, self.ui.k
            self.paint_bg()
            n = len(self.labels)
            tw_ = self.w / n
            pos = self.current_pos()
            for i, label in enumerate(self.labels):
                near = max(0.0, 1.0 - abs(pos - i))
                col = mix(p["muted"], p["fg"], near)
                col = mix(col, p["fg"], 0.5 * self.hov[i].eased * (1 - near))
                self.create_text((i + 0.5) * tw_, (self.h - 4 * k) / 2, text=label, fill=col,
                                 font=self.ui.font(11, "bold"))
            self.create_line(0, self.h - 1, self.w, self.h - 1, fill=p["border"])
            pad = 14 * k
            x0 = pos * tw_ + pad
            self.create_polygon(round_points(x0, self.h - 3 * k, x0 + tw_ - 2 * pad, self.h, 1.5 * k),
                                smooth=True, fill=p["accent"], outline=p["accent"])
        except tk.TclError:
            pass


class Swatch(CanvasWidget):
    """Round color chip with an animated selection ring."""

    def __init__(self, parent, ui, color_fn, command=None, size=28, selected=False, bgkey="card"):
        super().__init__(parent, ui, size, size, bgkey)
        self.color_fn, self.command = color_fn, command
        self.ring, self.hover = Tween(1 if selected else 0, 0.18), Tween(0, 0.12)
        self.bind("<Enter>", lambda e: self.tween(self.hover, 1))
        self.bind("<Leave>", lambda e: self.tween(self.hover, 0))
        self.bind("<ButtonRelease-1>", self.on_click)
        self.configure(cursor="hand2")
        self.redraw()

    def tweens(self):
        return (self.ring, self.hover)

    def set_selected(self, on):
        self.tween(self.ring, 1 if on else 0)

    def on_click(self, e):
        if self.enabled and 0 <= e.x < self.w and 0 <= e.y < self.h and self.command:
            self.command()

    def redraw(self):
        try:
            self.delete("all")
            p, k = self.ui.p, self.ui.k
            self.paint_bg()
            cx, cy = self.w / 2, self.h / 2
            big = min(self.w, self.h) / 2 - 1
            ring, hv = self.ring.eased, self.hover.eased
            r = (big - 3 * k * ring) * (1 + 0.05 * hv)
            self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=self.color_fn(), outline=p["border"])
            if ring > 0.01:
                self.create_oval(cx - big, cy - big, cx + big, cy + big, outline=p["fg"],
                                 width=max(1, 2 * k * ring))
        except tk.TclError:
            pass


class KeyCap(CanvasWidget):
    """On-screen key that is pressed down (and flashes) whenever the program presses the key."""

    def __init__(self, parent, ui, label, width=300, height=76, bgkey="card"):
        super().__init__(parent, ui, width, height, bgkey)
        self.label = label
        self.press, self.flash = Tween(0, 0.07), Tween(0, 0.45)
        self.held = False
        self.auto_release = False
        self.redraw()

    def tweens(self):
        return (self.press, self.flash)

    def after_step(self):
        if self.auto_release and not self.held and self.press.value >= 1.0:
            self.auto_release = False
            self.press.set(0)

    def set_label(self, label):
        self.label = label
        self.redraw()

    def pulse(self):
        """One quick press (used in click mode)."""
        if not self.ui.animate:
            return
        self.auto_release = True
        self.flash.set(1, immediate=True)
        self.tween(self.flash, 0)
        self.tween(self.press, 1)

    def set_held(self, held):
        """Keep the key down while it is held (hold mode)."""
        self.held = bool(held)
        self.auto_release = False
        self.tween(self.press, 1 if held else 0)

    def redraw(self):
        try:
            self.delete("all")
            p, k = self.ui.p, self.ui.k
            self.paint_bg()
            depth = 8 * k
            travel = depth * 0.75 * self.press.eased
            capw = min(self.w - 8 * k, 200 * k)
            x0 = (self.w - capw) / 2
            x1 = x0 + capw
            top = 6 * k + travel
            face_h = max(10 * k, self.h - 12 * k - depth)
            tint = max(self.flash.eased * 0.6, 0.35 * self.press.eased if self.held else 0.0)
            face = mix(p["btn"], p["accent"], tint)
            side = mix(p["btn_active"], p["accent"], tint * 0.6)
            txt = mix(p["fg"], p["accent_fg"], min(1.0, tint / 0.6))
            r = 10 * k
            self.create_polygon(round_points(x0, 6 * k + depth, x1, 6 * k + depth + face_h, r),
                                smooth=True, fill=side, outline=p["border"])
            self.create_polygon(round_points(x0, top, x1, top + face_h, r),
                                smooth=True, fill=face, outline=p["border"])
            n = len(self.label)
            size = 22 if n <= 2 else 18 if n <= 5 else 14 if n <= 8 else 11
            self.create_text((x0 + x1) / 2, top + face_h / 2, text=self.label, fill=txt,
                             font=self.ui.font(size, "bold"))
        except tk.TclError:
            pass


def set_bg_color(widget, color):
    """Set a widget's background color only if it differs (see BgFrame)."""
    try:
        if str(widget.cget("bg")).lower() != str(color).lower():
            widget.configure(bg=color)
    except tk.TclError:
        pass


class BgFrame(tk.Canvas):
    """Container for the window background. Shows the background picture if one is set.

    Used instead of a frame wherever the window background is visible, so that the
    picture continues seamlessly behind all parts of the window.
    """

    def __init__(self, parent, ui, **kw):
        tk.Canvas.__init__(self, parent, bd=0, highlightthickness=0, bg=ui.p["bg"], **kw)
        self.ui = ui
        self._key = None
        self._img = None
        ui.bg_frames.append(self)
        self.bind("<Configure>", lambda e: self.refresh())

    def refresh(self):
        try:
            set_bg_color(self, self.ui.p["bg"])   # never re-configure unchanged: avoids a layout loop
            bd = self.ui.backdrop
            if not bd.active():
                if self._img is not None:
                    self.delete("backdrop")
                    self._img = self._key = None
                return
            x = self.winfo_rootx() - self.ui.root.winfo_rootx()
            y = self.winfo_rooty() - self.ui.root.winfo_rooty()
            ox, oy = self.canvasx(0), self.canvasy(0)   # scrolled canvas: draw at the visible part
            key = (x, y, self.winfo_width(), self.winfo_height(), ox, oy, bd.version)
            if key == self._key:
                return
            self._key = key
            self.delete("backdrop")
            self._img = bd.slice(x, y, self.winfo_width(), self.winfo_height())
            if self._img is not None:
                self.create_image(ox, oy, image=self._img, anchor="nw", tags="backdrop")
                self.tag_lower("backdrop")
        except tk.TclError:
            pass


class CanvasLabel(CanvasWidget):
    """Text on the window background (works on top of the background picture)."""

    def __init__(self, parent, ui, text="", size=10, bold=False, fgkey="fg", wrap=0, bgkey="bg"):
        super().__init__(parent, ui, 10, 10, bgkey)
        self.text, self.size, self.bold, self.fgkey, self.wrap = text, size, bold, fgkey, wrap
        self.color = None
        self.req = None
        self.redraw()

    def set_text(self, text, color=None):
        if text == self.text and color == self.color:
            return
        self.text, self.color = text, color
        self.redraw()

    def set_wrap(self, px):
        if px != self.wrap:
            self.wrap = px
            self.redraw()

    def redraw(self):
        try:
            self.delete("all")
            self.paint_bg()
            item = self.create_text(0, 0, text=self.text, anchor="nw",
                                    fill=self.color or self.ui.p[self.fgkey], width=self.wrap or 0,
                                    font=self.ui.font(self.size, "bold" if self.bold else "normal"))
            box = self.bbox(item)
            req = (max(1, box[2] - box[0]), max(1, box[3] - box[1])) if box else (1, 1)
            if req != self.req:
                self.req = req
                self.configure(width=req[0], height=req[1])
        except tk.TclError:
            pass


class ScrollArea(BgFrame):
    """Vertically scrollable area. The scrollbar only shows when it is needed."""

    def __init__(self, parent, ui):
        BgFrame.__init__(self, parent, ui)
        self.needed = False
        self.on_width = None   # callback(width) when the usable width changes
        self.on_moved = None   # callback() when the content was scrolled
        self.canvas = BgFrame(self, ui, width=ui.px(360), height=ui.px(400), yscrollincrement=ui.px(20))
        # One style only: the scrollbar keeps its width and is just recolored when it is
        # not needed. Switching between two styles changed the width on Tk 9, which made
        # the text wrap differently and toggled the scrollbar again and again (endless loop).
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                  style="App.Vertical.TScrollbar")
        self._last_width = None
        self.canvas.configure(yscrollcommand=self.on_scroll)
        self.inner = BgFrame(self.canvas, ui)
        self.inner.columnconfigure(0, weight=1)
        self.win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns", padx=(ui.px(4), 0))
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.inner.bind("<Configure>", self.update_region)
        self.canvas.bind("<Configure>", self.on_canvas)

    def update_region(self, e=None):
        try:
            self.canvas.configure(scrollregion=self.canvas.bbox(self.win))
        except tk.TclError:
            pass
        self.inner.refresh()

    def on_canvas(self, e):
        if e.width == self._last_width and e.height == getattr(self, "_last_height", None):
            return
        self._last_width, self._last_height = e.width, e.height
        try:
            self.canvas.itemconfigure(self.win, width=e.width)
        except tk.TclError:
            pass
        if self.on_width:
            self.on_width(e.width)
        self.canvas.refresh()

    def on_scroll(self, first, last):
        self.vbar.set(first, last)
        if self.on_moved:
            self.on_moved()
        need = not (float(first) <= 0.0 and float(last) >= 1.0)
        if need != self.needed:
            self.needed = need
            self.apply_bar_colors()

    def apply_bar_colors(self):
        """Show the scrollbar thumb only while scrolling is possible (size never changes)."""
        p = self.ui.p
        thumb = p["border"] if self.needed else p["bg"]
        hover = p["muted"] if self.needed else p["bg"]
        press = p["accent"] if self.needed else p["bg"]
        try:
            st = ttk.Style(self)
            st.configure("App.Vertical.TScrollbar", troughcolor=p["bg"], background=thumb,
                         bordercolor=p["bg"], lightcolor=thumb, darkcolor=thumb,
                         arrowsize=self.ui.px(9))
            st.map("App.Vertical.TScrollbar",
                   background=[("pressed", press), ("active", hover)],
                   lightcolor=[("pressed", press), ("active", hover)],
                   darkcolor=[("pressed", press), ("active", hover)])
        except tk.TclError:
            pass

    def scroll_units(self, n):
        if self.needed:
            self.canvas.yview_scroll(n, "units")

    def to_top(self):
        self.canvas.yview_moveto(0)

    def set_bg(self, color):
        set_bg_color(self, color)
        set_bg_color(self.canvas, color)


# ---------------------------------------------------------------------- GUI

class App:
    def __init__(self, root, start_minimized=False):
        self.root = root
        root.report_callback_exception = self.on_tk_error
        root.title("Keyboard Autoclicker")
        root.resizable(True, True)
        set_window_icon(root)
        self.style = ttk.Style(root)

        s = load_settings()
        self.ui = UI(root)
        self.scale = tk.StringVar(value=s["scale"])
        self.apply_scaling()
        self.ui.animate = s["animations"] == "1"
        self.running = False
        self.thread = None
        self.stop_event = threading.Event()
        self.events = queue.Queue()       # messages from background threads
        self.capture_target = None        # "key" or "hotkey"
        self.hotkey_name = combo_id(s["hotkey"])  # copy for the listener thread
        self.dark = False
        self.tick = 0
        self.locked = []                  # input widgets that are locked while running
        self.cards = []                   # card frames (recolored on theme change)
        self.status_text, self.status_kind = "Ready", "idle"
        self.press_count = 0              # written by the worker thread ...
        self.held = False
        self.seen_presses = 0             # ... and read by the GUI thread (key cap animation)
        self.seen_held = False

        self.action = tk.StringVar(value=s["action"])
        self.h, self.m, self.s, self.ms = (tk.StringVar(value=s[k]) for k in ("h", "m", "s", "ms"))
        self.jitter = tk.StringVar(value=s["jitter"])
        self.key = tk.StringVar(value=s["key"])
        self.hold = tk.StringVar(value=s["hold"])
        self.mode = tk.StringVar(value=s["mode"])
        self.count = tk.StringVar(value=s["count"])
        self.hold_for = tk.StringVar(value=s["hold_for"])
        self.autorepeat = tk.BooleanVar(value=s["autorepeat"] == "1")
        self.hotkey = tk.StringVar(value=s["hotkey"])
        self.theme = tk.StringVar(value=s["theme"])
        self.anim = tk.BooleanVar(value=s["animations"] == "1")
        self.topmost = tk.BooleanVar(value=s["topmost"] == "1")
        self.compact = tk.BooleanVar(value=s["compact"] == "1")
        self.in_compact = False
        self.full_geom = None             # window size of the full view while compact
        self.keycaps = []
        self.wrap_labels = []
        self.delay = tk.StringVar(value=s["delay"])
        self.limit = tk.StringVar(value=s["limit"])
        self.panic_esc = tk.BooleanVar(value=s["panic_esc"] == "1")
        self.panic_corner = tk.BooleanVar(value=s["panic_corner"] == "1")
        self.bg_mode = tk.StringVar(value=s["bg_mode"])
        self.bg_strength = tk.StringVar(value=s["bg_strength"])
        self.opacity = tk.StringVar(value=s["opacity"])
        self._bd_pending = False
        self.last_bg_mode = s["bg_mode"]
        if self.bg_mode.get() == "image" and not self.ui.backdrop.load(background_path()):
            self.bg_mode.set("color")     # picture missing or unreadable
        if self.bg_mode.get() == "transparent" and not IS_WIN:
            self.bg_mode.set("color")
        self.last_bg_mode = self.bg_mode.get()
        self.mods_down, self.capture_mods = set(), set()   # used by the listener thread
        self.esc_active = False
        self.run_base = 0                 # press counter at the start of the current run
        self.run_started = self.run_ended = None
        self.stats_text = None
        self.accent = tk.StringVar(value=s["accent"])
        self.accent_hex = tk.StringVar(value="")
        self.colors = {m: {c: tk.StringVar(value=s[f"{m}_{c}"]) for c in ("bg", "card", "fg")}
                       for m in ("light", "dark")}
        current = autostart_get()
        self.autostart = tk.BooleanVar(value=current is not None)
        # If the entry points to an old EXE path (program was moved), repair it
        if current is not None and getattr(sys, "frozen", False) and current != autostart_command():
            try:
                set_autostart(True)
            except OSError:
                pass

        self.build_ui()
        self.hotkey.trace_add("write", self.on_hotkey_change)
        self.key.trace_add("write", self.on_key_change)
        self.on_action_change()
        self.apply_theme()
        self.apply_topmost(save=False)
        self.restore_geometry(s["geometry"])
        self.compact.trace_add("write", self.apply_compact)
        # Ctrl + / Ctrl - / Ctrl 0 change the size, Ctrl + mouse wheel too
        for seq, step in (("<Control-plus>", 1), ("<Control-equal>", 1), ("<Control-KP_Add>", 1),
                          ("<Control-minus>", -1), ("<Control-KP_Subtract>", -1), ("<Control-0>", 0)):
            root.bind(seq, lambda e, st=step: self.zoom(st))
        root.bind_all("<MouseWheel>", self.on_wheel, add="+")
        # any size or position change: the background picture slices must follow
        root.bind("<Configure>", lambda e: self.schedule_backdrop(), add="+")
        root.after(150, lambda: set_titlebar_dark(self.root, self.dark))

        if start_minimized:
            root.iconify()

        self.listener = Listener(on_press=self.on_press, on_release=self.on_release)
        self.listener.daemon = True
        self.listener.start()

        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll()

    # ---------- build the interface ----------
    def card(self, parent, row, title):
        c = tk.Frame(parent, bd=0, highlightthickness=1)
        c.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        self.cards.append(c)
        c.title_label = ttk.Label(c, text=title.upper(), style="CardTitle.TLabel")
        c.title_label.grid(row=0, column=0, columnspan=4, sticky="w", padx=14, pady=(10, 6))
        return c

    def entry(self, parent, var, width=6):
        e = ttk.Entry(parent, textvariable=var, width=width, justify="center", font=self.ui.font(11))
        self.locked.append(e)
        return e

    def button(self, parent, text, command, **kw):
        return RoundButton(parent, self.ui, text, command, **kw)

    def seg(self, parent, text, var, value, command=None, lock=True, width=100):
        b = RoundButton(parent, self.ui, text, command, variable=var, value=value, height=36, width=width)
        if lock:
            self.locked.append(b)
        return b

    def wlabel(self, parent, **kw):
        """Muted label that wraps its text to the current window width."""
        kw.setdefault("style", "CardMuted.TLabel")
        kw.setdefault("justify", "left")
        kw["wraplength"] = self.ui.px(320)
        lbl = ttk.Label(parent, **kw)
        self.wrap_labels.append(lbl)
        return lbl

    def build_ui(self):
        ui = self.ui
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        P = ui.px(16)
        main = BgFrame(self.root, ui)
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        self.main = main

        # --- header ---
        head = BgFrame(main, ui)
        head.grid(row=0, column=0, sticky="ew", padx=P, pady=(ui.px(14), ui.px(6)))
        head.columnconfigure(0, weight=1)
        CanvasLabel(head, ui, "Keyboard Autoclicker", size=17, bold=True).grid(row=0, column=0, sticky="w")
        self.sub_label = CanvasLabel(head, ui, "Press or hold keys automatically", size=9, fgkey="muted")
        self.sub_label.grid(row=1, column=0, sticky="w")
        self.compact_btn = RoundButton(head, ui, "Compact", self.toggle_compact, width=84, height=30,
                                       bgkey="bg", font_size=9)
        self.compact_btn.grid(row=0, column=1, rowspan=2, sticky="e", padx=(ui.px(8), 0))

        # --- tabs ---
        self.tabbar = TabBar(main, ui, ["Autoclicker", "Settings"], self.on_tab)
        self.tabbar.grid(row=1, column=0, sticky="ew", padx=P, pady=(0, ui.px(10)))

        # --- scrollable pages ---
        self.scroll = ScrollArea(main, ui)
        self.scroll.grid(row=2, column=0, sticky="nsew", padx=P)
        self.scroll.on_width = self.on_content_width
        self.scroll.on_moved = self.schedule_backdrop
        pages = self.scroll.inner
        self.page_main, self.page_settings = BgFrame(pages, ui), BgFrame(pages, ui)
        for pg in (self.page_main, self.page_settings):
            pg.grid(row=0, column=0, sticky="nsew")
            pg.columnconfigure(0, weight=1)
        self.page_settings.grid_remove()
        self.build_main_page(self.page_main)
        self.build_settings_page(self.page_settings)

        # --- compact view (shown instead of the tabs and pages) ---
        cf = self.compact_frame = BgFrame(main, ui)
        cf.grid(row=2, column=0, sticky="nsew", padx=P)
        cf.columnconfigure(0, weight=1)
        self.mini_cap = KeyCap(cf, ui, key_label(self.key.get()), width=240, height=64, bgkey="bg")
        self.mini_cap.grid(row=0, column=0, sticky="ew")
        self.summary = CanvasLabel(cf, ui, "", size=9, fgkey="muted")
        self.summary.grid(row=1, column=0, sticky="w", pady=(ui.px(4), 0))
        cf.grid_remove()
        self.keycaps = [self.keycap, self.mini_cap]

        # --- start / stop (always visible, also when the window is small) ---
        # (Canvas buttons never take keyboard focus, so an automatically pressed
        # space key cannot trigger a focused button and stop the autoclicker.)
        self.btn = RoundButton(main, ui, "Start", self.toggle, kind="accent", height=46,
                               bgkey="bg", font_size=11, bold=True)
        self.btn.grid(row=3, column=0, sticky="ew", padx=P, pady=(ui.px(10), ui.px(8)))
        self.status = CanvasLabel(main, ui, "", size=10, wrap=ui.px(340))
        self.status.grid(row=4, column=0, sticky="w", padx=P)
        self.stats = CanvasLabel(main, ui, "", size=9, fgkey="muted")
        self.stats.grid(row=5, column=0, sticky="w", padx=P, pady=(ui.px(2), ui.px(12)))
        self.stats_text = None
        main.bind("<Configure>", self.on_main_width)

    def build_main_page(self, page):
        # --- mode: click / hold ---
        c = self.card(page, 0, "Mode")
        for i in range(2):
            c.columnconfigure(i, weight=1, uniform="mode")
        self.seg(c, "Click", self.action, "click", self.on_action_change).grid(
            row=1, column=0, sticky="ew", padx=(14, 3), pady=(0, 12))
        self.seg(c, "Hold key", self.action, "hold", self.on_action_change).grid(
            row=1, column=1, sticky="ew", padx=(3, 14), pady=(0, 12))

        # --- key ---
        c = self.card(page, 1, "Key")
        c.columnconfigure(0, weight=1)
        self.entry(c, self.key, 10).grid(row=1, column=0, sticky="ew", padx=(14, 6), pady=(0, 8), ipady=3)
        b = self.button(c, "Record", lambda: self.start_capture("key"), width=80, height=34)
        b.grid(row=1, column=1, padx=(0, 14), pady=(0, 8))
        self.locked.append(b)
        self.keycap = KeyCap(c, self.ui, key_label(self.key.get()), width=320, height=76)
        self.keycap.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 4))
        self.wlabel(c, text="Type a key or a combination (e.g. a, space, f5, ctrl+c, shift+tab) "
                            "or click Record and press it.").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 12))

        # --- click mode: interval etc. ---
        c = self.click_card = self.card(page, 2, "Interval")
        for i in range(4):
            c.columnconfigure(i, weight=1, uniform="iv")
        for i, (lbl, var) in enumerate([("Hrs", self.h), ("Min", self.m), ("Sec", self.s), ("Ms", self.ms)]):
            self.entry(c, var, 4).grid(row=1, column=i, sticky="ew", ipady=3,
                                       padx=(14 if i == 0 else 3, 14 if i == 3 else 3))
            ttk.Label(c, text=lbl, style="CardMuted.TLabel").grid(row=2, column=i, pady=(2, 8))
        ttk.Label(c, text="Random variation (± ms)", style="Card.TLabel").grid(
            row=3, column=0, columnspan=3, sticky="w", padx=14, pady=4)
        self.entry(c, self.jitter, 4).grid(row=3, column=3, sticky="ew", ipady=2, padx=(3, 14), pady=4)
        ttk.Label(c, text="Key press duration (ms)", style="Card.TLabel").grid(
            row=4, column=0, columnspan=3, sticky="w", padx=14, pady=4)
        self.entry(c, self.hold, 4).grid(row=4, column=3, sticky="ew", ipady=2, padx=(3, 14), pady=4)
        ttk.Label(c, text="Repeat", style="CardMuted.TLabel").grid(
            row=5, column=0, columnspan=4, sticky="w", padx=14, pady=(8, 4))
        self.seg(c, "Endless", self.mode, "inf").grid(
            row=6, column=0, columnspan=2, sticky="ew", padx=(14, 3), pady=(0, 12))
        self.seg(c, "Count", self.mode, "n").grid(row=6, column=2, sticky="ew", padx=3, pady=(0, 12))
        self.entry(c, self.count, 4).grid(row=6, column=3, sticky="ew", ipady=3, padx=(3, 14), pady=(0, 12))

        # --- hold mode: options ---
        c = self.hold_card = self.card(page, 2, "Hold")
        c.columnconfigure(0, weight=1)
        self.wlabel(c, text="The key stays pressed until you stop it (hotkey or stop button).").grid(
            row=1, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 8))
        ttk.Label(c, text="Release after (sec, 0 = never)", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", padx=14, pady=4)
        self.entry(c, self.hold_for, 5).grid(row=2, column=1, sticky="e", ipady=2, padx=(3, 14), pady=4)
        cb = ToggleCheck(c, self.ui, "Auto-repeat like a physically held key", self.autorepeat)
        cb.grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 12))
        self.locked.append(cb)

        # --- timing & safety ---
        c = self.card(page, 3, "Timing & safety")
        c.columnconfigure(0, weight=1)
        ttk.Label(c, text="Start delay (sec)", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", padx=14, pady=4)
        self.entry(c, self.delay, 5).grid(row=1, column=1, sticky="e", ipady=2, padx=(3, 14), pady=4)
        ttk.Label(c, text="Stop after (min, 0 = never)", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", padx=14, pady=4)
        self.entry(c, self.limit, 5).grid(row=2, column=1, sticky="e", ipady=2, padx=(3, 14), pady=4)
        cb = ToggleCheck(c, self.ui, "Emergency stop with Esc", self.panic_esc)
        cb.grid(row=3, column=0, columnspan=2, sticky="ew", padx=14, pady=(6, 2))
        self.locked.append(cb)
        cb = ToggleCheck(c, self.ui, "Emergency stop: mouse into a screen corner", self.panic_corner)
        cb.grid(row=4, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 6))
        self.locked.append(cb)
        self.wlabel(c, text="The start delay gives you time to click into the target window. "
                            "An emergency stop releases all keys at once.").grid(
            row=5, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 12))

        # --- hotkey ---
        c = self.card(page, 4, "Start/Stop hotkey")
        c.columnconfigure(0, weight=1)
        self.entry(c, self.hotkey, 10).grid(row=1, column=0, sticky="ew", padx=(14, 6), pady=(0, 12), ipady=3)
        b = self.button(c, "Record", lambda: self.start_capture("hotkey"), width=80, height=34)
        b.grid(row=1, column=1, padx=(0, 14), pady=(0, 12))
        self.locked.append(b)


    def build_settings_page(self, page):
        ui = self.ui
        # --- appearance ---
        c = self.card(page, 0, "Appearance")
        for i in range(3):
            c.columnconfigure(i, weight=1, uniform="theme")
        for i, (text, value) in enumerate((("System", "system"), ("Light", "light"), ("Dark", "dark"))):
            self.seg(c, text, self.theme, value, self.on_theme_change, lock=False).grid(
                row=1, column=i, sticky="ew", padx=(14 if i == 0 else 3, 14 if i == 2 else 3), pady=(0, 8))
        ToggleCheck(c, ui, "Animations", self.anim, self.on_anim_toggle).grid(
            row=2, column=0, columnspan=3, sticky="ew", padx=14, pady=(0, 12))

        # --- window ---
        c = self.card(page, 1, "Window")
        last = len(SCALES) - 1
        for i in range(len(SCALES)):
            c.columnconfigure(i, weight=1, uniform="scale")
        ttk.Label(c, text="Size", style="Card.TLabel").grid(
            row=1, column=0, columnspan=len(SCALES), sticky="w", padx=14, pady=(0, 4))
        for i, value in enumerate(SCALES):
            self.seg(c, f"{value}%", self.scale, value, self.on_scale_change, lock=False, width=52).grid(
                row=2, column=i, sticky="ew", padx=(14 if i == 0 else 2, 14 if i == last else 2), pady=(0, 10))
        ToggleCheck(c, ui, "Compact view", self.compact).grid(
            row=3, column=0, columnspan=len(SCALES), sticky="ew", padx=14, pady=(0, 4))
        ToggleCheck(c, ui, "Always on top", self.topmost, self.apply_topmost).grid(
            row=4, column=0, columnspan=len(SCALES), sticky="ew", padx=14, pady=(0, 6))
        self.wlabel(c, text="Drag the window edges to resize it. Ctrl + and Ctrl − change "
                            "the size, Ctrl 0 resets it. Ctrl + mouse wheel works too.").grid(
            row=5, column=0, columnspan=len(SCALES), sticky="w", padx=14, pady=(0, 12))

        # --- background ---
        c = self.card(page, 2, "Background")
        for i in range(5):
            c.columnconfigure(i, weight=1, uniform="bgc")
        modes = (("Color", "color"), ("Transparent", "transparent"), ("Image", "image"))
        row = ttk.Frame(c, style="Card.TFrame")
        row.grid(row=1, column=0, columnspan=5, sticky="ew", padx=14, pady=(0, 8))
        for i, (text, value) in enumerate(modes):
            row.columnconfigure(i, weight=1, uniform="bgm")
            b = self.seg(row, text, self.bg_mode, value, (lambda v=value: self.set_bg_mode(v)), lock=False, width=90)
            b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 3, 0))
            if value == "transparent" and not IS_WIN:
                b.state(["disabled"])
        row = ttk.Frame(c, style="Card.TFrame")
        row.grid(row=2, column=0, columnspan=5, sticky="ew", padx=14, pady=(0, 8))
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)
        self.button(row, "Choose image…", self.choose_image, height=32).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.remove_img_btn = self.button(row, "Remove image", self.remove_image, height=32)
        self.remove_img_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        ttk.Label(c, text="Image strength", style="Card.TLabel").grid(
            row=3, column=0, columnspan=5, sticky="w", padx=14, pady=(0, 4))
        row = ttk.Frame(c, style="Card.TFrame")
        row.grid(row=4, column=0, columnspan=5, sticky="ew", padx=14, pady=(0, 8))
        for i, value in enumerate(BG_STRENGTHS):
            row.columnconfigure(i, weight=1, uniform="bgs")
            self.seg(row, f"{value}%", self.bg_strength, value, self.on_bg_option, lock=False, width=52).grid(
                row=0, column=i, sticky="ew", padx=(0 if i == 0 else 3, 0))
        ttk.Label(c, text="Window opacity", style="Card.TLabel").grid(
            row=5, column=0, columnspan=5, sticky="w", padx=14, pady=(0, 4))
        for i, value in enumerate(OPACITIES):
            self.seg(c, f"{value}%", self.opacity, value, self.on_bg_option, lock=False, width=52).grid(
                row=6, column=i, sticky="ew", padx=(14 if i == 0 else 2, 14 if i == len(OPACITIES) - 1 else 2), pady=(0, 8))
        self.wlabel(c, text="Transparent: the window background between the cards becomes see-through "
                            "(Windows only); clicks there go to the window behind. Window opacity makes the "
                            "whole window see-through. Image strength dims the picture so text stays readable.").grid(
            row=7, column=0, columnspan=5, sticky="w", padx=14, pady=(0, 12))

        # --- accent color ---
        c = self.card(page, 3, "Accent color")
        row = ttk.Frame(c, style="Card.TFrame")
        row.grid(row=1, column=0, columnspan=4, sticky="w", padx=14, pady=(0, 10))
        self.accent_swatches = []
        defaults = [("", lambda: PALETTES["dark" if self.dark else "light"]["accent"])]
        defaults += [(hx, (lambda hx=hx: hx)) for _, hx in ACCENT_PRESETS]
        for i, (value, fn) in enumerate(defaults):
            sw = Swatch(row, ui, fn, (lambda v=value: self.set_accent(v)), size=28)
            sw.grid(row=0, column=i, padx=(0, 6))
            self.accent_swatches.append((sw, value))
        c.columnconfigure(0, weight=1)
        hexrow = ttk.Frame(c, style="Card.TFrame")
        hexrow.grid(row=2, column=0, columnspan=4, sticky="ew", padx=14, pady=(0, 12))
        ttk.Label(hexrow, text="Hex", style="CardMuted.TLabel").grid(row=0, column=0, padx=(0, 8))
        self.accent_entry = ttk.Entry(hexrow, textvariable=self.accent_hex, width=9, justify="center",
                                      font=self.ui.font(11))
        self.accent_entry.grid(row=0, column=1, ipady=2)
        self.accent_entry.bind("<Return>", self.on_hex_entry)
        self.accent_entry.bind("<FocusOut>", self.on_hex_entry)
        self.button(hexrow, "Pick…", self.pick_accent, width=80, height=32).grid(row=0, column=2, padx=(8, 0))

        # --- custom colors ---
        c = self.colors_card = self.card(page, 4, "Colors")
        c.columnconfigure(0, weight=1)
        for i, (key, label) in enumerate((("bg", "Background"), ("card", "Cards"), ("fg", "Text"))):
            ttk.Label(c, text=label, style="Card.TLabel").grid(row=1 + i, column=0, sticky="w", padx=14, pady=4)
            Swatch(c, ui, (lambda k=key: self.ui.p[k]), (lambda k=key: self.pick_override(k)),
                   size=26).grid(row=1 + i, column=1, sticky="e", padx=(0, 14), pady=4)
        self.wlabel(c, text="Colors are saved separately for the light and the dark theme.").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=14, pady=(6, 6))
        self.button(c, "Reset colors", self.reset_colors, height=34).grid(
            row=5, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 12))

        # --- startup ---
        c = self.card(page, 5, "Startup")
        self.autostart_cb = ToggleCheck(c, ui, "Start with Windows (minimized)", self.autostart,
                                        self.on_autostart_toggle)
        self.autostart_cb.grid(row=1, column=0, columnspan=4, sticky="ew", padx=14, pady=(0, 12))
        c.columnconfigure(0, weight=1)
        if winreg is None:
            self.autostart_cb.state(["disabled"])

    # ---------- appearance ----------
    def apply_theme(self):
        mode = self.theme.get()
        self.dark = system_is_dark() if mode == "system" else mode == "dark"
        overrides = {k: v.get() for k, v in self.colors["dark" if self.dark else "light"].items()}
        p = build_palette(self.dark, self.accent.get(), overrides)
        if self.bg_mode.get() == "transparent" and IS_WIN:
            p["bg"] = transparent_key(p["bg"])   # this color becomes see-through
        self.ui.set_palette(p)
        st = self.style
        try:
            st.theme_use("clam")   # only this theme can be fully recolored
        except tk.TclError:
            pass
        self.root.configure(bg=p["bg"])
        self.scroll.set_bg(p["bg"])
        for c in self.cards:
            c.configure(bg=p["card"], highlightbackground=p["border"], highlightcolor=p["border"])

        st.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["field"],
                     bordercolor=p["border"], troughcolor=p["field"], focuscolor=p["bg"],
                     insertcolor=p["fg"], selectbackground=p["accent"],
                     selectforeground=p["accent_fg"], font=self.ui.font(10))
        st.configure("TFrame", background=p["bg"])
        st.configure("Card.TFrame", background=p["card"])
        st.configure("TLabel", background=p["bg"], foreground=p["fg"])
        st.configure("Title.TLabel", background=p["bg"], font=self.ui.font(17, "bold"))
        st.configure("Sub.TLabel", background=p["bg"], foreground=p["muted"], font=self.ui.font(9))
        st.configure("Status.TLabel", background=p["bg"], font=self.ui.font(10))
        st.configure("Card.TLabel", background=p["card"], foreground=p["fg"])
        st.configure("CardMuted.TLabel", background=p["card"], foreground=p["muted"], font=self.ui.font(9))
        st.configure("CardTitle.TLabel", background=p["card"], foreground=p["muted"],
                     font=self.ui.font(8, "bold"))
        st.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"],
                     insertcolor=p["fg"], bordercolor=p["border"],
                     lightcolor=p["field"], darkcolor=p["field"], padding=3)
        st.map("TEntry",
               fieldbackground=[("disabled", p["card"])],
               foreground=[("disabled", p["muted"])],
               bordercolor=[("focus", p["accent"])],
               lightcolor=[("focus", p["accent"])],
               darkcolor=[("focus", p["accent"])])

        # thin scrollbar without arrows; invisible while nothing needs scrolling
        bar = [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})]
        try:
            st.layout("App.Vertical.TScrollbar", bar)
        except tk.TclError:
            pass
        self.scroll.apply_bar_colors()

        set_titlebar_dark(self.root, self.dark)
        self.set_status(self.status_text, self.status_kind)
        self.apply_background()
        self.refresh_settings_widgets()

    def refresh_settings_widgets(self):
        cur = self.accent.get().lower()
        for sw, value in self.accent_swatches:
            sw.set_selected(cur == value)
        self.accent_hex.set(self.ui.p["accent"])
        self.colors_card.title_label.config(
            text=f"Colors – {'dark' if self.dark else 'light'} theme".upper())

    def set_status(self, text, kind="idle"):
        self.status_text, self.status_kind = text, kind
        p = self.ui.p
        color = {"idle": p["muted"], "run": p["accent"], "ok": p["success"], "err": p["danger"]}[kind]
        self.status.set_text(f"●  {text}", color)

    def on_theme_change(self):
        self.apply_theme()
        save_settings(self.collect_settings())

    def on_anim_toggle(self):
        self.ui.animate = self.anim.get()
        save_settings(self.collect_settings())

    def set_accent(self, value):
        self.accent.set(value)
        self.apply_theme()
        save_settings(self.collect_settings())

    def pick_color(self, initial, title):
        try:
            result = colorchooser.askcolor(color=initial, title=title, parent=self.root)
        except tk.TclError:
            return None
        hx = result[1] if result else None
        return hx.lower() if hx and is_hex(hx) else None

    def pick_accent(self):
        hx = self.pick_color(self.ui.p["accent"], "Accent color")
        if hx:
            self.set_accent(hx)

    def on_hex_entry(self, *_):
        v = self.accent_hex.get().strip().lower()
        if v and not v.startswith("#"):
            v = "#" + v
        if is_hex(v):
            if v != self.ui.p["accent"].lower():
                self.set_accent(v)
        else:
            self.accent_hex.set(self.ui.p["accent"])   # invalid input: go back

    def pick_override(self, key):
        names = {"bg": "Background color", "card": "Card color", "fg": "Text color"}
        hx = self.pick_color(self.ui.p[key], names[key])
        if hx:
            self.colors["dark" if self.dark else "light"][key].set(hx)
            self.apply_theme()
            save_settings(self.collect_settings())

    def reset_colors(self):
        for var in self.colors["dark" if self.dark else "light"].values():
            var.set("")
        self.apply_theme()
        save_settings(self.collect_settings())

    def on_tab(self, i):
        if i == 0:
            self.page_settings.grid_remove()
            self.page_main.grid()
        else:
            self.page_main.grid_remove()
            self.page_settings.grid()
        self.scroll.to_top()

    def on_action_change(self):
        if self.action.get() == "hold":
            self.click_card.grid_remove()
            self.hold_card.grid()
        else:
            self.hold_card.grid_remove()
            self.click_card.grid()
        self.update_button()

    def on_tk_error(self, exc, value, tb):
        """Errors inside Tk callbacks: write them to the error log instead of losing them."""
        import traceback
        log_error("".join(traceback.format_exception(exc, value, tb)))

    # ---------- background ----------
    def apply_background(self):
        mode = self.bg_mode.get()
        p = self.ui.p
        try:
            self.root.attributes("-transparentcolor", p["bg"] if mode == "transparent" and IS_WIN else "")
        except tk.TclError:
            pass
        try:
            self.root.attributes("-alpha", int(self.opacity.get()) / 100.0)
        except (tk.TclError, ValueError):
            pass
        self.ui.backdrop.enabled = mode == "image"
        try:
            self.remove_img_btn.state(["!disabled"] if os.path.exists(background_path()) else ["disabled"])
        except (tk.TclError, AttributeError):
            pass
        self.refresh_backdrop()

    def schedule_backdrop(self):
        if self.ui.backdrop.enabled and not self._bd_pending:
            self._bd_pending = True
            self.root.after(30, self.refresh_backdrop)

    def refresh_backdrop(self):
        """Bring the background picture up to date in every part of the window."""
        self._bd_pending = False
        try:
            w, h = self.root.winfo_width(), self.root.winfo_height()
        except tk.TclError:
            return
        try:
            strength = int(self.bg_strength.get())
        except ValueError:
            strength = 50
        self.ui.backdrop.update_cover(w, h, self.ui.p["bg"], strength)
        for f in list(self.ui.bg_frames):
            f.refresh()
        for wdg in list(self.ui.widgets):
            if wdg.bgkey == "bg":
                wdg.redraw()

    def set_bg_mode(self, mode):
        if mode == "image" and self.ui.backdrop.src is None:
            if not self.ui.backdrop.load(background_path()):
                if not self.choose_image():           # no picture yet: let the user pick one
                    self.bg_mode.set(self.last_bg_mode)
                    return
        self.bg_mode.set(mode)
        self.last_bg_mode = mode
        self.apply_theme()
        save_settings(self.collect_settings())

    def on_bg_option(self):
        self.apply_theme()
        save_settings(self.collect_settings())

    def choose_image(self):
        """Pick a picture, keep a copy of it and use it as background. True on success."""
        if Image is None:
            messagebox.showerror("Background image", "Background images need the Pillow package.\n\n"
                                                     "Install it with:  pip install pillow")
            return False
        try:
            path = filedialog.askopenfilename(
                parent=self.root, title="Choose a background image",
                filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("All files", "*.*")])
        except tk.TclError:
            return False
        if not path:
            return False
        try:
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((2560, 2560))
                os.makedirs(os.path.dirname(background_path()), exist_ok=True)
                im.save(background_path(), "PNG")
        except Exception as e:
            messagebox.showerror("Background image", f"This picture could not be opened:\n{e}")
            return False
        self.ui.backdrop.load(background_path())
        self.bg_mode.set("image")
        self.last_bg_mode = "image"
        self.apply_theme()
        save_settings(self.collect_settings())
        return True

    def remove_image(self):
        try:
            os.remove(background_path())
        except OSError:
            pass
        self.ui.backdrop.src = None
        if self.bg_mode.get() == "image":
            self.bg_mode.set("color")
            self.last_bg_mode = "color"
        self.apply_theme()
        save_settings(self.collect_settings())

    # ---------- window size ----------
    def apply_scaling(self):
        try:
            factor = int(self.scale.get()) / 100.0
        except ValueError:
            factor = 1.0
        self.ui.set_scale(factor)

    def on_scale_change(self):
        # rebuild a moment later, not inside the click handler of the button being replaced
        self.root.after(10, self._do_rescale)

    def _do_rescale(self):
        self.apply_scaling()
        self.rebuild_ui()
        if self.in_compact:
            self.full_geom = None          # the old full size no longer fits
            try:
                self.root.geometry("")
            except tk.TclError:
                pass
        else:
            self.fit_window()
        save_settings(self.collect_settings())

    def zoom(self, step):
        """step +1 / -1 = next size step, 0 = back to 100 %."""
        cur = self.scale.get()
        i = SCALES.index(cur) if cur in SCALES else SCALES.index("100")
        j = SCALES.index("100") if step == 0 else max(0, min(len(SCALES) - 1, i + step))
        if SCALES[j] != cur:
            self.scale.set(SCALES[j])
            self.on_scale_change()
        return "break"

    def rebuild_ui(self):
        """Build the whole window again with the new size (all values live in variables)."""
        tab = self.tabbar.current
        for w in self.ui.widgets:
            try:
                w.detach()
            except Exception:
                pass
        self.ui.widgets = []
        self.ui.bg_frames = []
        self.ui.animator.items = []
        self.locked, self.cards, self.wrap_labels = [], [], []
        try:
            self.main.destroy()
        except tk.TclError:
            pass
        self.build_ui()
        self.on_action_change()
        self.apply_theme()
        self.tabbar.select(tab, notify=True, animate=False)
        if self.running:
            self.set_inputs_enabled(False)
        self.on_key_change()
        self.seen_held = None              # show a held key again on the new key caps
        self.apply_compact(resize=False)

    def on_content_width(self, width):
        wl = max(self.ui.px(140), width - self.ui.px(40))
        if wl == getattr(self, "_last_wrap", None) and self.wrap_labels and \
                self.wrap_labels[0].cget("wraplength") == wl:
            return
        self._last_wrap = wl
        for lbl in self.wrap_labels:
            try:
                lbl.configure(wraplength=wl)
            except tk.TclError:
                pass

    def on_main_width(self, e):
        try:
            self.status.set_wrap(max(self.ui.px(140), e.width - self.ui.px(36)))
        except tk.TclError:
            pass

    def on_wheel(self, e):
        try:
            if e.state & 0x0004:           # Ctrl + mouse wheel = change the size
                self.zoom(1 if e.delta > 0 else -1)
                return
            if self.in_compact:
                return
            w = self.root.winfo_containing(e.x_root, e.y_root)
            if w is not None and str(w).startswith(str(self.scroll)):
                steps = int(-e.delta / 120) or (-1 if e.delta > 0 else 1)
                self.scroll.scroll_units(steps * 3)
        except (tk.TclError, KeyError, AttributeError, TypeError):
            pass

    def apply_topmost(self, save=True):
        try:
            self.root.attributes("-topmost", bool(self.topmost.get()))
        except tk.TclError:
            pass
        if save:
            save_settings(self.collect_settings())

    def toggle_compact(self):
        self.compact.set(not self.compact.get())

    def apply_compact(self, *_, resize=True):
        on = bool(self.compact.get())
        try:
            if on:
                if resize and not self.in_compact:
                    self.full_geom = self.root.geometry()     # remember the full size
                self.sub_label.grid_remove()
                self.tabbar.grid_remove()
                self.scroll.grid_remove()
                self.compact_frame.grid()
                self.compact_btn.set_text("Expand")
                self.root.minsize(self.ui.px(240), 1)
                if resize:
                    self.root.geometry("")                    # shrink to the compact content
            else:
                self.compact_frame.grid_remove()
                self.sub_label.grid()
                self.tabbar.grid()
                self.scroll.grid()
                self.compact_btn.set_text("Compact")
                self.root.minsize(self.ui.px(320), self.ui.px(300))
                if resize and self.in_compact:
                    size = (self.full_geom or "").split("+")[0]
                    if re.match(r"^\d+x\d+$", size):
                        self.root.geometry(size)
                    else:
                        self.fit_window()
        except tk.TclError:
            pass
        self.in_compact = on
        self.update_summary()
        if resize:
            save_settings(self.collect_settings())

    def fit_window(self):
        """Size the window to its content, but never taller than the screen."""
        try:
            self.root.update_idletasks()
            screen_h = self.root.winfo_screenheight()
            inner_h = self.scroll.inner.winfo_reqheight()
            other = self.main.winfo_reqheight() - self.scroll.winfo_reqheight()
            h = max(self.ui.px(160), min(inner_h, int(screen_h * 0.85) - other))
            self.scroll.canvas.configure(height=h, width=self.scroll.inner.winfo_reqwidth())
            self.root.geometry("")
        except (tk.TclError, TypeError, ValueError):
            pass

    def restore_geometry(self, saved):
        try:
            geom = sanitize_geometry(saved, self.root.winfo_screenwidth(), self.root.winfo_screenheight(),
                                     self.ui.px(240), self.ui.px(200))
        except (tk.TclError, TypeError, ValueError):
            geom = None
        self.fit_window()
        self.apply_compact(resize=False)
        try:
            if self.in_compact:
                self.full_geom = geom
                self.root.geometry("")
                parts = (geom or "").split("+")
                if len(parts) == 3:
                    self.root.geometry(f"+{parts[1]}+{parts[2]}")
            elif geom:
                self.root.geometry(geom)
        except tk.TclError:
            pass

    def geometry_for_save(self):
        try:
            cur = self.root.geometry()
        except tk.TclError:
            return ""
        if not self.in_compact:
            return cur if GEOM_RE.match(cur or "") else ""
        size = (self.full_geom or "").split("+")[0]
        parts = (cur or "").split("+")
        if re.match(r"^\d+x\d+$", size) and len(parts) == 3:
            g = f"{size}+{parts[1]}+{parts[2]}"
            return g if GEOM_RE.match(g) else ""
        return ""

    def update_summary(self):
        if not hasattr(self, "summary"):
            return
        key = key_label(self.key.get())
        if self.action.get() == "hold":
            text = f"Hold key · {key}"
        else:
            try:
                ms = ((self.read_number(self.h, "") * 3600 + self.read_number(self.m, "") * 60
                       + self.read_number(self.s, "")) * 1000 + self.read_number(self.ms, ""))
                text = f"Click · {key} · every {format_ms(ms)}"
            except ValueError:
                text = f"Click · {key}"
        try:
            self.summary.set_text(text)
        except tk.TclError:
            pass

    # ---------- autostart ----------
    def on_autostart_toggle(self):
        want = self.autostart.get()
        try:
            set_autostart(want)
        except Exception as e:
            self.autostart.set(not want)
            messagebox.showerror("Autostart", f"Autostart could not be changed:\n{e}")

    # ---------- helpers ----------
    def collect_settings(self):
        data = {
            "action": self.action.get(),
            "h": self.h.get(), "m": self.m.get(), "s": self.s.get(), "ms": self.ms.get(),
            "jitter": self.jitter.get(), "key": self.key.get(), "hold": self.hold.get(),
            "mode": self.mode.get(), "count": self.count.get(),
            "hold_for": self.hold_for.get(),
            "autorepeat": "1" if self.autorepeat.get() else "0",
            "hotkey": self.hotkey.get(), "theme": self.theme.get(),
            "animations": "1" if self.anim.get() else "0",
            "accent": self.accent.get(),
            "scale": self.scale.get(),
            "topmost": "1" if self.topmost.get() else "0",
            "compact": "1" if self.compact.get() else "0",
            "geometry": self.geometry_for_save(),
            "delay": self.delay.get(), "limit": self.limit.get(),
            "panic_esc": "1" if self.panic_esc.get() else "0",
            "panic_corner": "1" if self.panic_corner.get() else "0",
            "bg_mode": self.bg_mode.get(), "bg_strength": self.bg_strength.get(),
            "opacity": self.opacity.get(),
        }
        for m in ("light", "dark"):
            for c in ("bg", "card", "fg"):
                data[f"{m}_{c}"] = self.colors[m][c].get()
        return data

    def on_hotkey_change(self, *_):
        self.hotkey_name = combo_id(self.hotkey.get())
        self.update_button()

    def on_key_change(self, *_):
        label = "…" if self.capture_target == "key" else key_label(self.key.get())
        for cap in self.keycaps:
            cap.set_label(label)
        self.update_summary()

    def update_button(self):
        hotkey = self.hotkey.get().strip().upper()
        if self.running:
            text = "Release" if self.action.get() == "hold" else "Stop"
        else:
            text = "Hold key" if self.action.get() == "hold" else "Start"
        self.btn.set_text(f"{text}  ({hotkey})")
        self.btn.set_kind("danger" if self.running else "accent")
        self.update_summary()

    def set_inputs_enabled(self, enabled):
        flag = ["!disabled"] if enabled else ["disabled"]
        for w in self.locked:
            w.state(flag)

    def start_capture(self, target):
        if self.running:
            return
        if self.capture_target == target:      # second click = cancel
            self.capture_target = None
            self.on_key_change()
            self.set_status("Ready")
            return
        self.capture_mods = set()
        self.capture_target = target
        self.on_key_change()
        self.set_status("Press the key or combination now …", "run")

    @staticmethod
    def read_number(var, label):
        text = var.get().strip().replace(",", ".")
        if text == "":
            return 0.0
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"{label}: “{var.get()}” is not a number.")
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{label}: Please enter a number of 0 or more.")
        return value

    # ---------- background thread -> GUI (thread-safe via queue) ----------
    def effective_mods(self):
        """Modifiers the user holds (modifiers pressed by this program are ignored)."""
        with _pressed_lock:
            own = {modifier_of(key_name_of(k)) for k in _pressed}
        return set(self.mods_down) - own

    def on_press(self, key):
        """Runs in the listener thread: must NOT touch Tkinter."""
        try:
            name = key_to_str(key)
            if not name:
                return
            mod = modifier_of(name)
            others = self.effective_mods() - ({mod} if mod else set())
            if mod:
                self.mods_down.add(mod)
            target = self.capture_target
            if target:
                if mod:                                   # wait for the main key
                    self.capture_mods.add(mod)
                    return
                mods = sorted(self.capture_mods | others, key=MODIFIERS.index)
                self.capture_target = None
                self.events.put(("captured", target, "+".join(mods + [name])))
                return
            main = mod or name
            combo = "+".join(sorted(others, key=MODIFIERS.index) + [main])
            plain_hotkey = "+" not in self.hotkey_name or self.hotkey_name == "+"
            if combo == self.hotkey_name or (plain_hotkey and main == self.hotkey_name):
                self.events.put(("toggle",))
            elif main == "esc" and self.running and self.esc_active:
                self.events.put(("panic",))
        except Exception:
            pass  # an exception would end the listener

    def on_release(self, key):
        """Runs in the listener thread. Records a modifier on its own (e.g. 'shift')."""
        try:
            mod = modifier_of(key_to_str(key))
            if not mod:
                return
            target = self.capture_target
            if target and mod in self.capture_mods:
                mods = sorted(self.capture_mods - {mod}, key=MODIFIERS.index)
                self.capture_target = None
                self.events.put(("captured", target, "+".join(mods + [mod])))
            self.mods_down.discard(mod)
        except Exception:
            pass

    def sync_keycap(self):
        """Mirror what the worker thread does onto the on-screen key."""
        if self.press_count != self.seen_presses:
            self.seen_presses = self.press_count
            if self.action.get() == "click":
                for cap in self.keycaps:
                    cap.pulse()
        if self.held != self.seen_held:
            self.seen_held = self.held
            for cap in self.keycaps:
                cap.set_held(self.held)

    def poll(self):
        try:
            while True:
                try:
                    ev = self.events.get_nowait()
                except queue.Empty:
                    break
                try:
                    self.handle(ev)
                except Exception:
                    pass  # a faulty event must not end the loop
            self.sync_keycap()
            self.update_stats()
            self.check_corner()
            self.tick += 1
            if self.tick % 60 == 0 and self.theme.get() == "system" and system_is_dark() != self.dark:
                self.apply_theme()  # the Windows theme was changed
        except Exception:
            pass
        finally:
            self.root.after(30, self.poll)   # always keeps running

    def handle(self, ev):
        kind = ev[0]
        if kind == "captured":
            (self.key if ev[1] == "key" else self.hotkey).set(ev[2])
            self.on_key_change()
            self.set_status("Ready")
        elif kind == "toggle":
            self.toggle()
        elif kind == "panic":
            self.emergency_stop()
        elif kind == "countdown" and ev[2] is self.stop_event and self.running:
            self.set_status(f"Starting in {ev[1]} … click into the target window", "run")
        elif kind == "go" and ev[1] is self.stop_event and self.running:
            self.set_status(self.running_text(), "run")
        elif kind == "done" and ev[2] is self.stop_event:
            reason = ev[3] if len(ev) > 3 else "count"
            if reason == "released":
                self.stop("Finished – key released", "ok")
            elif reason == "time":
                self.stop(f"Finished – time limit reached ({ev[1]}×)", "ok")
            else:
                self.stop(f"Finished ({ev[1]}×)", "ok")
        elif kind == "error" and ev[2] is self.stop_event:
            self.stop("Key press error", "err")
            messagebox.showerror("Error", ev[1])

    def running_text(self):
        hk = key_label(self.hotkey.get())
        if self.action.get() == "hold":
            return f"Holding key – press {hk} to release"
        return f"Running – press {hk} to stop"

    def update_stats(self):
        presses = self.press_count - self.run_base
        secs = 0
        if self.run_started is not None:
            end = self.run_ended if self.run_ended is not None else time.monotonic()
            secs = end - self.run_started
        text = f"Presses: {presses}   ·   Time: {format_duration(secs)}"
        if text != self.stats_text:
            self.stats_text = text
            try:
                self.stats.set_text(text)
            except tk.TclError:
                pass

    def check_corner(self):
        """Emergency stop when the mouse is pushed into a corner of the screen."""
        if not (self.running and self.panic_corner.get()):
            return
        try:
            x, y = self.root.winfo_pointerxy()
            left, top, w, h = screen_rect(self.root)
        except (tk.TclError, TypeError, ValueError):
            return
        m = 2
        near_x = x <= left + m or x >= left + w - 1 - m
        near_y = y <= top + m or y >= top + h - 1 - m
        if near_x and near_y:
            self.emergency_stop()

    # ---------- control ----------
    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.running:
            return
        self.capture_target = None
        self.on_key_change()
        try:
            keys = parse_combo(self.key.get())
            key_parts = split_combo(self.key.get())
        except ValueError as e:
            messagebox.showerror("Error", f"Key: {e}")
            return
        try:
            parse_combo(self.hotkey.get())
            hk_parts = split_combo(self.hotkey.get())
        except ValueError as e:
            messagebox.showerror("Error", f"Hotkey: {e}")
            return
        if hk_parts[-1] in key_parts:
            messagebox.showerror("Error", "The hotkey must not be the key itself or part of the "
                                          "key combination. Please choose another hotkey.")
            return

        holding = self.action.get() == "hold"
        try:
            delay = self.read_number(self.delay, "Start delay")
            if delay > 60:
                raise ValueError("Start delay: maximum 60 seconds.")
            limit = self.read_number(self.limit, "Stop after") * 60
            if limit > MAX_SECONDS:
                raise ValueError("Stop after: the time is too large (maximum 30 days).")
            if holding:
                duration = self.read_number(self.hold_for, "Release after")
                if duration > MAX_SECONDS:
                    raise ValueError("The times are too large (maximum 30 days).")
                job = (self.hold_worker, (keys, duration or None, self.autorepeat.get(), delay, limit or None))
            else:
                interval = (self.read_number(self.h, "Hours") * 3600
                            + self.read_number(self.m, "Minutes") * 60
                            + self.read_number(self.s, "Seconds")
                            + self.read_number(self.ms, "Milliseconds") / 1000)
                jitter = self.read_number(self.jitter, "Variation") / 1000
                press = self.read_number(self.hold, "Key press duration") / 1000
                total = None
                if self.mode.get() == "n":
                    cnt = self.read_number(self.count, "Count")
                    if cnt < 1 or cnt != int(cnt):
                        raise ValueError("Count: Please enter a whole number of 1 or more.")
                    total = int(cnt)
                if interval > MAX_SECONDS or jitter > MAX_SECONDS or press > MAX_SECONDS:
                    raise ValueError("The times are too large (maximum 30 days).")
                job = (self.click_worker,
                       (keys, max(interval, 0.001), jitter, press, total, delay, limit or None))
        except ValueError as e:
            messagebox.showerror("Input error", str(e))
            return

        save_settings(self.collect_settings())
        # the program's own Esc presses must not trigger the emergency stop
        self.esc_active = self.panic_esc.get() and "esc" not in key_parts
        self.run_base = self.press_count
        self.run_started = self.run_ended = None
        self.stop_event = threading.Event()   # a separate event for every run
        self.running = True
        self.set_inputs_enabled(False)
        self.update_button()
        if delay > 0:
            self.set_status(f"Starting in {math.ceil(delay)} … click into the target window", "run")
        else:
            self.set_status(self.running_text(), "run")
        func, args = job
        self.thread = threading.Thread(target=func, args=(self.stop_event,) + args, daemon=True)
        self.thread.start()

    def stop(self, text="Stopped", kind="idle"):
        self.stop_event.set()
        self.running = False
        if self.run_started is not None and self.run_ended is None:
            self.run_ended = time.monotonic()
        self.set_inputs_enabled(True)
        self.update_button()
        self.set_status(text, kind)

    def emergency_stop(self):
        if self.running:
            self.stop("Emergency stop – all keys released", "err")
        release_all()

    def countdown(self, stop, delay):
        """Wait 'delay' seconds, reporting the seconds left. False if stopped meanwhile."""
        end = time.monotonic() + delay
        while not stop.is_set():
            left = end - time.monotonic()
            if left <= 0:
                break
            n = math.ceil(left)
            self.events.put(("countdown", n, stop))
            if stop.wait(left - (n - 1)):
                return False
        if stop.is_set():
            return False
        self.run_started = time.monotonic()
        self.events.put(("go", stop))
        return True

    def click_worker(self, stop, keys, interval, jitter, press, total, delay=0, limit=None):
        try:
            if not self.countdown(stop, delay):
                return
            end = time.monotonic() + limit if limit else None
            done = 0
            while not stop.is_set():
                if end is not None and time.monotonic() >= end:
                    self.events.put(("done", done, stop, "time"))
                    return
                press_combo(keys)
                self.press_count += 1
                try:
                    if press:
                        stop.wait(press)
                finally:
                    release_combo(keys)    # the keys are always released
                done += 1
                if total is not None and done >= total:
                    self.events.put(("done", done, stop, "count"))
                    return
                wait = max(0.001, interval + random.uniform(-jitter, jitter))
                if end is not None:
                    wait = min(wait, max(0.0, end - time.monotonic()))
                stop.wait(wait)
        except Exception as e:
            self.events.put(("error", str(e), stop))

    def hold_worker(self, stop, keys, duration, repeat, delay=0, limit=None):
        """Hold the key (combination) until stopped, or until the time is up."""
        finished = False
        try:
            if not self.countdown(stop, delay):
                return
            limits = [t for t in (duration, limit) if t]
            duration = min(limits) if limits else None
            press_combo(keys)
            self.press_count += 1
            self.held = True
            try:
                begin = time.monotonic()
                end = begin + duration if duration else None
                nxt = begin + REPEAT_DELAY if repeat else None
                while True:
                    now = time.monotonic()
                    if end is not None and now >= end:
                        finished = True
                        break
                    waits = [t for t in (end, nxt) if t is not None]
                    timeout = max(0.0, min(waits) - now) if waits else None
                    if stop.wait(timeout):
                        break
                    if nxt is not None and time.monotonic() >= nxt:
                        kb.press(keys[-1])     # like the repeat of a real, held key
                        nxt = time.monotonic() + REPEAT_INTERVAL
            finally:
                try:
                    release_combo(keys)        # the keys are always released
                finally:
                    self.held = False
            if finished and not stop.is_set():
                self.events.put(("done", 0, stop, "released"))
        except Exception as e:
            self.held = False
            self.events.put(("error", str(e), stop))

    def close(self):
        try:
            save_settings(self.collect_settings())
        finally:
            self.stop_event.set()
            if self.thread is not None and self.thread.is_alive():
                self.thread.join(1.0)   # wait until the key has been released
            release_all()
            self.listener.stop()
            self.root.destroy()


def main():
    if already_running():
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo("Keyboard Autoclicker", "The program is already running.")
        r.destroy()
        return
    root = tk.Tk()
    try:
        App(root, start_minimized="--minimized" in sys.argv)
    except Exception:
        import traceback
        log_error(traceback.format_exc())
        messagebox.showerror("Keyboard Autoclicker",
                             "The program could not start. Details were saved in:\n"
                             + os.path.join(os.path.dirname(settings_path()), "error.log"))
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
