"""
Internal launcher helper — run via launch.bat, not directly.
Shows a terminal progress bar until the Graphify window appears.
"""
import ctypes
import os
import subprocess
import sys
import time

CWD  = os.path.dirname(os.path.abspath(__file__))
EXE  = sys.executable.replace("python.exe", "pythonw.exe")
if not os.path.exists(EXE):
    EXE = sys.executable   # fallback: console python (still works)

LOG  = os.path.join(CWD, "graphify_error.log")
WIDTH   = 32
TIMEOUT = 20   # seconds before giving up
FILL_T  = 4    # seconds to reach ~95% fill (looks natural)

SPINNER = ["|", "/", "-", "\\"]

log_fh = open(LOG, "w")
proc   = subprocess.Popen(
    [EXE, "app.py"],
    stderr=log_fh,
    stdout=log_fh,
    cwd=CWD,
)

user32 = ctypes.windll.user32

def _window_up(title: str) -> bool:
    return user32.FindWindowW(None, title) != 0

start = time.time()
print()

while True:
    elapsed = time.time() - start

    # ── Success ───────────────────────────────────────────────────────────
    if _window_up("Graphify"):
        bar = "#" * WIDTH
        print(f"\r  Launching Graphify  [{bar}]  Ready!     \n")
        break

    # ── Timeout ───────────────────────────────────────────────────────────
    if elapsed > TIMEOUT:
        print(f"\r  Timed out — check graphify_error.log for details\n")
        break

    # ── Process died before window appeared ───────────────────────────────
    if proc.poll() is not None:
        print(f"\r  Graphify exited unexpectedly — check graphify_error.log\n")
        sys.exit(1)

    # ── Animated progress bar ─────────────────────────────────────────────
    pct    = min(elapsed / FILL_T, 0.95)
    filled = int(WIDTH * pct)
    bar    = "#" * filled + "-" * (WIDTH - filled)
    spin   = SPINNER[int(elapsed * 8) % 4]
    print(f"\r  Launching Graphify  [{bar}]  {spin}  ", end="", flush=True)
    time.sleep(0.1)
