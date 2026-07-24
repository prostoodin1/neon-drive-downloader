# Changelog

## 5.4.0-beta.7

- Moved progress, speed, ETA, state, and the start action inside the Download and Upload pages.
- Removed the transfer footer from Settings, Advanced mode, and Updates so it no longer follows
  the user through unrelated pages.
- Added small, standard, and large window presets plus a mode that remembers a manually resized window.
- Added a Small screen design mode with tighter headers, controls, cards, and spacing.
- Added independent Download and Upload status controls and GUI coverage for the new layouts.

## 5.4.0-beta.6

- Added top, expanded sidebar, and initially collapsed sidebar navigation layouts.
- Added a Codex-style header control that smoothly collapses and restores the sidebar
  without changing the active page.
- Smoothed tab fades, sidebar movement, progress updates, and general interface transitions.
- Added one-click download and connection of the official Windows Rclone executable.
- Verified the official Rclone ZIP against its release SHA256SUMS before atomically replacing
  the managed executable under the application data directory.

## 5.4.0-beta.5

- Added selectable Robocopy, Rclone, and safe hybrid copy engines for downloads and uploads.
- Added Rclone chunk size, multi-thread cutoff, streams, transfers, checkers, buffer,
  checksum, sparse-file compatibility, and retry controls.
- Added an optional Advanced mode tab and hid the technical terminal in the simpler default mode.
- Consolidated appearance controls into Settings and reduced the number of permanent top-level tabs.
- Added Rclone progress parsing, engine routing tests, and protection against assigning two engines
  to the same destination item.

## 5.4.0-beta.4

- Converted the upload screen into an optional beta-only add-on controlled from
  the Updates tab with install, remove, and GitHub actions.
- Hidden both the upload tab and add-on controls from stable builds.
- Added compact, comfortable, and minimalist design modes with denser modern
  buttons, tabs, cards, inputs, and spacing throughout the application.
- Added manifest validation and isolated add-on storage under the application
  data directory without touching files already uploaded to Google Drive.
- Kept the most recently downloaded application installer in a single cache and
  displayed its version on the Updates tab after restarting the app.

## 5.4.0-beta.3

- Added a dedicated `ВЫГРУЗКА` tab for copying local files and folders to a
  Google Drive location selected through Windows Explorer.
- Added independent source, destination, queue preview, terminal, pause, stop,
  progress, speed, and ETA state for download and upload screens.
- Kept uploads on Robocopy so Google Drive for desktop remains responsible for
  caching and safely sending data to the cloud.

## 5.4.0-beta.1

- Added a Turbo profile that reads independent ranges of one large cloud-backed file in parallel.
- Added a configurable 2–16 Turbo worker slider with an aggregate pressure limit across active files.
- Added resumable `.neon-part` checkpoints for fully completed file segments.
- Kept fast Robocopy as the automatic Turbo fallback for folders and multi-file trees.
- Preserved pause, resume, stop, progress, speed, and ETA behavior for segmented copies.

## 5.3.0

- Increased contrast for all application text and fixed unreadable QMessageBox dialogs.
- Added real Stable, Optimized, and Maximum Robocopy performance profiles.
- Added configurable `/MT` directory threads with a bounded aggregate worker budget.
- Verified multi-threaded folder copying and byte progress against a real Robocopy process.
- Removed the unused Interface preview section.
- Replaced the application artwork with a bold multi-resolution Windows icon.
- Added an installed onedir build and Inno Setup package that never extracts to `_MEI` at runtime.
- Kept a transitional onefile asset so v5.2 and earlier auto-updaters remain compatible.
- Updated the updater to prefer silent installer-based upgrades and offer onefile migration.

## 5.2.0

- Capped every parallel mode at 10 simultaneous Robocopy processes and fixed slot refilling.
- Prevented parallel sources with colliding destination names from corrupting one another.
- Kept mode-dependent settings visible while clearly dimming and disabling them.
- Added a custom neon application icon for the window, tray, and Windows executable.
- Moved onefile extraction and downloaded updates out of the shared Windows temp directory.
- Updated the replacement helper to wait for PyInstaller onefile cleanup before swapping EXEs.
- Upgraded the build bootloader to PyInstaller 6.21 and disabled UPX for more reliable startup.

## 5.1.0

- Restored separate Download, Settings, Interface, and Updates tabs.
- Rebuilt Settings to match the approved two-column layout.
- Added source-link and destination-link visibility controls.
- Fixed Stop After File to target one concrete active file and stop remaining jobs afterward.
- Added staggered file-card, restart-banner, and status-change animations.

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
