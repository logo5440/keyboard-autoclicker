# Double-click to start the autoclicker WITHOUT a console/terminal window.
# (Windows opens .pyw files with pythonw.exe.) Requirement: pip install pynput
import os
import runpy
import sys

folder = os.path.dirname(os.path.abspath(__file__))
os.chdir(folder)
sys.path.insert(0, folder)
runpy.run_path(os.path.join(folder, "keyboard_autoclicker.py"), run_name="__main__")
