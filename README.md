# StackedViewer

Plays one video file in a grid of tiles. Each tile plays the same file,
starting a set number of minutes after the tile before it. Tiles are muted
unless you switch one on.

Built with Python, Qt 6 (PySide6) and libmpv, for Windows and macOS.

**Status: milestone 2.** The start screen is done. The player it opens is
still the milestone 1 test grid: it plays correctly, but has no control bar
yet.

## Setup

Python 3.10 or newer.

```bash
pip install -r requirements.txt
```

Then install the libmpv library:

- **macOS:** `brew install mpv`
- **Windows:** download a libmpv build (an `mpv-dev-x86_64-*.7z` archive from
  the [mpv Windows builds](https://sourceforge.net/projects/mpv-player-windows/files/libmpv/)),
  and copy `libmpv-2.dll` into a folder named `libmpv` at the top of this
  project.
- **Linux:** `sudo apt install libmpv2`

To use a library somewhere else, set `STACKEDVIEWER_LIBMPV` to its folder.

## Running

```bash
python -m stackedviewer
```

On the start screen:

1. Drop a video onto the window, or use **Choose file...**.
2. Pick a layout: a preset, or any rows and columns up to 12 x 12.
3. Set the offset in minutes between each tile and the next (decimals allowed).
4. Check the preview, which shows where each tile will start. Tiles whose
   offset runs past the end of the video wrap around to the beginning and are
   marked in orange.
5. Press **Start**.

The file, layout and offset are remembered for next time.

During playback: Space pauses and resumes, F toggles fullscreen, Esc returns
to the start screen. Click a tile to hear it (one tile at a time); click it
again to mute.

## The test grid on its own

The milestone 1 command-line tool is still available for measuring a machine:

```bash
python -m stackedviewer.spike VIDEO --rows 2 --cols 2 --offset 5
```

| Option | Meaning |
|---|---|
| `--rows`, `--cols` | grid size (default 2 x 2) |
| `--offset` | minutes between tiles (default 5); wraps past the end of the video |
| `--hwdec` | hardware decoding mode: `auto-safe` (default), `auto-copy`, `no` |
| `--benchmark SECONDS` | measure for that long, print a report, exit |
| `--fullscreen` | start fullscreen |

Each tile shows its position, actual and source frame rate, dropped frames and
the hardware decoder in use.

## Measuring a machine

Run these with a typical 1080p file and, if you use them, a 4K file:

```bash
python -m stackedviewer.spike VIDEO --rows 2 --cols 2 --offset 5 --benchmark 30
python -m stackedviewer.spike VIDEO --rows 4 --cols 4 --offset 5 --benchmark 30
```

The report lists, per tile, the hardware decoder, frame rate, dropped frames
and drift from its expected position, plus a verdict of `OK` or `STRUGGLING`.
If `hwdec` reads `no` or the verdict is `STRUGGLING`, try `--hwdec auto-copy`.

## Tests

```bash
python -m pytest
```
