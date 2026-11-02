"""Start-up smoke test (Windows).

Builds the real window, lets Tk process events for a few seconds and fails if the
program hangs (for example an endless layout loop) or raises an error on start-up.
Run:  python tests/smoke_test.py
"""
import faulthandler
import os
import sys
import tempfile
import time

# A hang never ends by itself: after 60 s print where it hangs and exit with an error.
faulthandler.dump_traceback_later(60, exit=True)

# Use a throw-away settings folder so the test never touches real settings.
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="kac-smoke-")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tkinter as tk  # noqa: E402
import keyboard_autoclicker as kac  # noqa: E402

print("Python", sys.version.split()[0], "| Tk", tk.TkVersion)
root = tk.Tk()
app = kac.App(root)

end = time.monotonic() + 3
updates = 0
while time.monotonic() < end:
    root.update()
    updates += 1
    time.sleep(0.02)

w, h = root.winfo_width(), root.winfo_height()
print(f"window {w}x{h}, {updates} event loops, viewable={root.winfo_viewable()}")
assert w > 100 and h > 100, "window has no usable size"

# switch tab, theme and size once: none of them may hang or fail
app.tabbar.select(1)
app.theme.set("dark")
app.on_theme_change()
app.scale.set("125")
app.on_scale_change()
for _ in range(30):
    root.update()
    time.sleep(0.02)

log = os.path.join(os.environ["APPDATA"], kac.APP_NAME, "error.log")
if os.path.exists(log):
    print(open(log, encoding="utf-8").read())
    raise SystemExit("errors were logged during start-up")

app.close()
faulthandler.cancel_dump_traceback_later()
print("SMOKE TEST OK")
