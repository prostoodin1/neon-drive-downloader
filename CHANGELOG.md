# Changelog

## 5.5.0-beta.5

- Added optional automatic full system diagnostics and safe repair at application startup.
- Added Windows sign-in startup registration managed from Settings.
- Added an option to keep Neon alive in the system tray after a background queue finishes.
- Added a compact download/upload direction switch directly above the Home transfer arrow.
- Made the selected Rclone chunk size control the real Google Drive upload chunk; the
  manual maximum is now 2048 MiB (2 GiB), while the Extreme profile uses a safer
  valid 512 MiB chunk (approximately 537 MB).
- Fixed automatic update discovery in beta builds so newer prereleases are visible and
  can be downloaded directly from the in-app Updates page.
- Added a branded OAuth completion page that asks the browser tab to close automatically
  after Google Drive is connected.
- Fixed diagnostics for the managed `NeonGoogleDrive:` OAuth destination so it is never
  treated as a local Explorer folder.
- Made the version manager detect user/system installations across 32-bit and 64-bit
  registry views and compare beta versions semantically.
- Added an installer action to launch the registered Neon Drive uninstaller while retaining
  user settings for a later reinstall; normal upgrades continue to preserve every setting.
- Fixed startup restoration so a saved simple preset no longer overwrites manual Rclone,
  queue, chunk, stream, checksum, or copy-profile settings after an update or restart.
- Added focused tests for startup registration, direct Drive diagnostics, direction switching,
  background completion behavior, uninstallation, version matching, and Drive chunk flags.

## 5.5.0-beta.4

- Added a one-click Google Drive OAuth2 connection using the bundled Rclone browser flow.
- Added direct uploads to the managed `NeonGoogleDrive:` remote, bypassing Google Drive
  for desktop while preserving the existing Explorer-folder workflow as an alternative.
- Store the Google refresh token only in Neon's managed Rclone config and never print it
  to the application terminal or session log.
- Added connect, reconnect, disconnect, connection-status, and quick destination controls.
- Force direct cloud transfers through one Rclone process and pass the managed config to
  every direct upload while keeping multi-stream performance settings inside that process.
- Added remote-path collision protection, Drive web opening, and automated OAuth/config tests.
- Made the hidden AI CLI emit UTF-8 reliably on Windows consoles.

## 5.5.0-beta.3

- Added a persistent successful-transfer counter with total, download, and upload
  volumes plus the number of days in the current statistics period.
- Added manual counter reset and optional automatic reset when a new month begins.
- Added a muted light Google Drive theme with contextual blue, green, yellow, and
  red controls while retaining Dark, Light, and OLED themes.
- Made release discovery and installer downloads use the public GitHub API and
  direct public asset URLs exclusively; GitHub login and GitHub CLI are not required.
- Made the transfer counter visible on Home and in the sidebar system card, with
  detailed controls in Settings.

## 5.5.0-beta.2

- Rebuilt the application around the requested dark Neon dashboard with a
  permanent left sidebar, compact status header, and system state card.
- Combined download and upload setup on Home with source, destination, preset,
  current transfers, live performance, and recent results in one view.
- Redesigned Templates as three detailed Slow, Optimal, and Maximum profile cards.
- Moved advanced configuration into a two-column Settings hub with section navigation.
- Added automatic workspace-aware sizing, 900x640, 1180x760, and 1380x880 presets,
  and responsive sidebar collapse so controls remain visible without maximizing.
- Added a dark/light theme switch to Neon Drive Installer and automatic graceful
  app shutdown before replacing an installed version.

## 5.5.0-beta.1

- Added a separate `NeonDriveInstaller.exe` version manager that lists GitHub
  releases, displays their changelogs, and installs either new or previous versions.
- Included the version manager in the universal setup and added a shortcut from
  the Updates page and the Windows Start menu.
- Redesigned the main application around a clean light interface with side
  navigation enabled by default and a compact collapsible menu.
- Added a dedicated Templates page with Slow, Optimal, and Maximum transfer cards
  shared by downloads and uploads.
- Kept releases ZIP-free: every release contains the application setup and the
  separate version manager executable.

## 5.4.0-beta.13

- Renamed the product to Neon Drive and made the light theme the default for new installs.
- Moved Settings behind a compact bottom gear beside the persistent system status.
- Added shared Slow, Optimal, and Maximum presets for downloads and uploads.
- Added a hidden JSON CLI for AI agents without exposing it as an application tab.
- Enforced a single Neon Drive instance and reused its local IPC endpoint for CLI commands.
- Bundled the official SHA-256-verified Rclone inside the universal installer and added
  a safe reinstall action that removes only orphaned Neon-managed Rclone processes.
- Closed background Neon Drive and Rclone after the final transfer when the window had
  been closed with background continuation enabled.
- Stopped publishing portable ZIP and legacy onefile packages; releases now contain only
  the universal Setup executable.

## 5.4.0-beta.12

- Added a background system diagnostics card to Settings with a one-click
  “check and repair” action and a detailed result report.
- Checks Robocopy, Rclone, official download connectivity, free disk space,
  application folders, configured destinations, Google Drive, and selected sources.
- Safely creates missing service/download folders and automatically installs or
  repairs the official Rclone build after SHA-256 verification.

## 5.4.0-beta.11

- Rejected uploads to the non-writable Google Drive virtual root and explained that
  users must select My Drive, Shared drives, or a nested folder.
- Probed destination create-and-rename support before starting a queue and surfaced
  concrete per-file Rclone or Robocopy errors at the end of failed transfers.
- Disabled local preallocation in Rclone to avoid Google Drive File Stream corruption
  and size-check failures while preserving the original final filename and extension.
- Added a mandatory source-readiness gate that waits for stable size and modification
  time, active writers to release the file, and temporary partial names to disappear.
- Rechecks the source after transfer and automatically returns it to the waiting queue
  if it changed while being copied.
- Added balanced, fast, maximum, extreme, and manual Rclone traffic profiles while
  preserving the single Rclone process limit.
- Expanded large-file tuning to 32 streams, 32 folder transfers, 64 checkers, and
  configurable per-thread write buffering.
- Modernized the transfer pages with clearer direction headers, compact actions,
  refined cards and navigation surfaces, and start controls that remain visible in
  the small-window layout.

## 5.4.0-beta.10

- Limited Rclone and hybrid transfers to one Rclone process at a time while keeping
  multi-threaded transfer inside that process.
- Blocked managed Rclone updates while an active transfer is still using `rclone.exe`.

## 5.4.0-beta.9

- Persisted every configurable transfer, Rclone, appearance, update, tray, log, and path setting.
- Added restoration of the active tab using stable page identifiers even when optional tabs change.
- Added restoration of window geometry, remembered manual size, position, and maximized state.
- Added an in-app note explaining that settings and layout state are saved automatically.
- Added a restart-level GUI test covering advanced/files tabs, sidebar state, window size,
  theme, design, engine, Rclone options, source and destination paths, and active tab.

## 5.4.0-beta.8

- Added an optional Files tab controlled by a live Settings toggle.
- Added a combined download and upload overview with source, destination, status,
  per-file progress, transferred bytes, read/download speed, and effective write speed.
- Kept the overview synchronized with queued, active, completed, failed, and stopped tasks.
- Added a subtle horizontal page slide when selecting tabs in sidebar navigation mode.
- Added GUI tests for dynamic tab insertion, aggregated file status, and sidebar slide animation.

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
