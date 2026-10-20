# PAPERHEAD IGT Tracker

A standalone Windows timer for speedrunning **PAPERHEAD v49.5**, featuring manual in-game time tracking, real-time timing, customizable hotkeys, and an OBS-friendly interface.

This is an unofficial community-developed tool.

## Features

- **IGT (In-Game Time):** Tracks cumulative gameplay time across levels.
- **RTA (Real Time):** Tracks real elapsed time, including loading screens and pauses.
- **Checkpoint reload support:** Preserves time spent on failed attempts when reloading checkpoints.
- **Manual level confirmation:** Prevents loading screens and transitions from affecting cumulative IGT.
- **Customizable hotkeys:** Change keyboard shortcuts through the settings menu.
- **OBS-friendly display:** Compact timer with customizable background color.
- **Always on Top:** Keep the timer visible while playing in windowed or borderless fullscreen mode.
- **Saved preferences:** Hotkeys and display settings are remembered between sessions.

## Installation

1. Go to the [Releases](../../releases) page.
2. Download the latest `PAPERHEAD-TIMER.exe`.
3. Launch PAPERHEAD v49.5.
4. Run the executable.

**Python is not required** when using the executable.

## How to Use

### Starting a run

1. Launch PAPERHEAD and the tracker.
2. Enter the first level.
3. Press **O** to reset the tracker if necessary.
4. When the first level is ready, press **I** to begin tracking. (when you see the "Hold Space to continue" message on the bottom right)

The tracker displays two timers:

- **REAL TIME (RTA):** Total elapsed time, including loading screens and pauses.
- **IN-GAME TIME (IGT):** Accumulated gameplay time, excluding manually skipped loading time.

### Finishing a level

1. Complete the current level.
2. When you reach the end screen, press **U** to save the level's final IGT.
3. Proceed to the next level.
4. Once the next level is ready, press **I** to resume tracking ("Hold Space to continue" on the bottom right).

Repeat this process for each level.

### Reloading a checkpoint

The game may restore its internal IGT to an earlier value when loading a checkpoint. The tracker can preserve the time spent before the reload.

1. Press **J** to notify the tracker that you're about to reload a checkpoint (while paused).
2. Reload the checkpoint normally.
3. Wait until the checkpoint finishes loading.
4. Press **I** to resume tracking.
   
You can repeat this process multiple times during a level.

**If you cancel the reload:** Press **I** to resume tracking normally.

### Finishing a run

After completing the final level, press **U** to record its final IGT.

## Default Hotkeys

| Key | Action |
|---|---|
| **U** | Finish the current level |
| **I** | Start the next level / Resume after checkpoint reload |
| **J** | Prepare checkpoint reload |
| **O** | Reset the run |
| **P** | Exit the tracker |
| **F3** | Debug information |

All hotkeys can be customized.

## Settings

Right-click the timer to open the context menu.

You can:

- Open **Controls / Settings** to customize hotkeys and background color.
- Enable or disable **Always on Top**.
- Exit the tracker.

Preferences are saved automatically.

## OBS Setup

1. Open OBS Studio.
2. Add a **Window Capture** source.
3. Select the PAPERHEAD TIMER window.
4. Resize and position the timer in your layout.
5. Optionally, choose a background color in the tracker settings and apply a **Chroma Key** filter in OBS to remove it.

Choose a background color that contrasts with the white timer text.

## Running from Source (Ignore if you plan using the executable).

The complete Python source code is available in this repository.

### Requirements

- Windows
- Python 3
- PAPERHEAD v49.5

Install the Python dependencies:

```powershell
python -m pip install pymem keyboard
```

Run the tracker:

```powershell
python paperhead_igt_obs_checkpoint_manual.py
```

### Building the executable

Install PyInstaller:

```powershell
python -m pip install pyinstaller
```

Build the executable:

```powershell
python -m PyInstaller --onefile --windowed --clean --name "PAPERHEAD-IGT-Tracker" paperhead_igt_obs_checkpoint_manual.py
```

The executable will appear in the `dist` folder.

## Compatibility and Limitations

- Designed for **PAPERHEAD v49.5**. It might break with updates.
- Requires manual confirmation of level transitions and checkpoint reloads.
- Press **J before reloading** and **I after the checkpoint finishes loading**.
- The tracker reads the game's process memory to obtain IGT values.
- Game updates may change the memory addresses used by the tracker.
- Always on Top may not work in exclusive fullscreen.
- The checkpoint feature is experimental and should be tested before use in competitive runs.

## Reporting Issues

DM me on discord: vjs0

## Disclaimer

This is an unofficial community project and is not affiliated with the developers of PAPERHEAD.
