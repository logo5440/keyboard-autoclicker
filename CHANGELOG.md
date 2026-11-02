# Changelog

## 1.9.1
- **Fixed:** with Python 3.15 / Tk 9.0 the window never appeared (an endless layout loop between the scrollbar and the text wrapping, and between the background containers and the grid layout).
- Errors are now written to `%APPDATA%\KeyboardAutoclicker\error.log` instead of being lost.
- `.bat` and `.iss` files always keep Windows line endings.

## 1.9
- Background: plain color, transparent (Windows) or your own picture, with image strength.
- Window opacity from 100 % to 60 %.

## 1.8
- Key combinations for the key and the hotkey (e.g. `ctrl+c`, `shift+tab`, `ctrl+f6`).
- Start delay with countdown, press counter, running time, stop after X minutes.
- Emergency stop with `Esc` or by pushing the mouse into a screen corner.

## 1.7
- Resizable window with scrolling, size steps 80–150 % (Settings tab or Ctrl +/−/0).
- Compact view, always on top; window size and position are remembered.

## 1.6
- Settings tab with theme, accent color and custom colors.
- Animations: on-screen key that presses with your key, smooth hover and click effects.

## 1.5
- English user interface and illustrated installation guide.

## 1.4
- No terminal window, own app icon.

## 1.3
- Hold key mode, modern card-based design.

## 1.2
- Light and dark mode, start with Windows, saved settings, single instance.

## 1.1
- Bug fixes: thread-safe GUI updates, stuck keys, focus problems.

## 1.0
- First version: press a key repeatedly with interval, count and global hotkey.
