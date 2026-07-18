# Changelog

## 5.0.0

- Moved all file presentation modes into Settings and placed file progress on Download.
- Added detailed list, shortcut grid, and terminal-path file views with destination links.
- Fixed overall progress to use monotonic transferred bytes across all active jobs.
- Improved queue ETA with a responsive rolling speed window.
- Added sequential, limited-concurrency, and all-at-once download modes.
- Added OLED, dark, and light themes with preset or custom button accent colors.
- Added restart-required banner, smooth progress transitions, and tab animations.
- Added tray operation, completion notifications, automatic start, and output-folder actions.
- Preserved terminal scroll position while reading older output.
- Added log retention controls and quick access to the log directory.
- Added automatic/manual update modes and installation of previous GitHub Releases.

## 4.1.0

- Added automatic updates through GitHub Releases.
- Added manual update controls and version information to Settings.
- Added safe post-exit EXE replacement and automatic restart.
- Added sequential and parallel download modes.
- Added per-file progress cards, speed, elapsed time, and ETA.
- Added interface preferences and animated tab transitions.
- Fixed large-file Qt signal overflow by using a double byte counter.
