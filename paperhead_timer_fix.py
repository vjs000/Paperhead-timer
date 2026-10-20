"""PAPERHEAD v49.5 manual IGT tracker (Windows, 64-bit game assumed).

Install: py -m pip install pymem keyboard
Run:     py paperhead_manual_igt_obs_gui.py

Default hotkeys: U = finish, I = resume, J = checkpoint reload, O = reset,
F3 = debug, P = quit. All hotkeys are customizable in the GUI.
Buttons work even if global hotkeys do not.

Next-level confirmation remains manual because IGT resets and LevelNum
updates are unreliable indicators of when loading has finished.
RTA starts on the first Next Level confirmation and runs through loads and pauses.
Use OBS Window Capture on the compact Runner Timer window.
"""
import queue
import time
import json
from pathlib import Path
import struct
import sys
import re
import tkinter as tk
from tkinter import ttk, colorchooser, messagebox
from tkinter import font as tkfont
from datetime import datetime

import pymem
import pymem.process

try:
    import keyboard
except ImportError:
    keyboard = None

PROCESS_NAME = "PAPERHEAD.exe"
MODULE_NAME = "mono-2.0-bdwgc.dll"
LEVEL_OFFSETS = (0xA00B38, 0xD0, 0x8, 0x18, 0x740)
IGT_OFFSETS = (0x7437B0, 0x198, 0x468, 0x918, 0xC48, 0x1280)
POLL_MS = 50
# Use a writable settings folder even when packaged with PyInstaller --onefile.
DATA_DIR = Path.home() / "AppData" / "Local" / "PAPERHEAD-IGT-Tracker" if sys.platform == "win32" else Path.home() / ".paperhead-igt-tracker"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "paperhead_hotkeys.json"
DISPLAY_FILE = DATA_DIR / "paperhead_display.json"
# Allow existing settings from older versions to carry over.
LEGACY_SETTINGS_FILE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent / "paperhead_hotkeys.json"
LEGACY_DISPLAY_FILE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent / "paperhead_display.json"
DEFAULT_BG = "#101218"
DEFAULT_HOTKEYS = {"finish": "u", "resume": "i", "checkpoint": "j", "reset": "o", "debug": "f3", "close": "p"}
ACTION_NAMES = {"finish": "Finish level", "resume": "Resume / Next", "checkpoint": "Reload checkpoint", "reset": "Reset", "debug": "Debug", "close": "Quit"}
# Set to 4 if PAPERHEAD is a 32-bit process (most modern builds are 64-bit).
POINTER_BYTES = 8


def fmt(seconds):
    seconds = max(0.0, float(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours:02}:{minutes:02}:{sec:05.2f}"


class Tracker:
    def __init__(self, root):
        self.root = root
        root.title("PAPERHEAD — Runner Timer (OBS)")
        root.geometry("560x275")
        root.minsize(250, 150)
        self.events = queue.Queue()
        display_settings = self.load_display_settings()
        self.bg_color = display_settings["background"]
        self.always_on_top = display_settings["always_on_top"]
        root.configure(bg=self.active_background())
        self.runner_widgets = []
        self.bg_entry = None
        self.topmost_choice = None
        self.rta_start = None
        self.rta_elapsed = 0.0
        self.settings_window = None
        self.pm = None
        self.module_base = None
        self.last_attach_attempt = 0
        self.mode = "WAITING"  # WAITING, TRACKING, TRANSITION, CHECKPOINT
        self.total_completed = 0.0
        self.level_baseline = 0.0
        self.level_offset = 0.0
        self.last_raw = None
        self.level = None
        self.locked_level = None
        self.locked_igt = None
        self.checkpoint_raw = None
        self.checkpoint_time = None
        self.display_level = 0.0
        self.hotkeys = self.load_hotkeys()
        self.log_history = []
        self.hotkey_handles = []
        self.capture_hook = None
        self.capture_action = None
        self.capture_modifiers = set()
        self.build_ui()
        self.apply_display_settings()
        self.register_hotkeys()
        self.log(f"Manual-only mode. Press {self.hotkeys['resume'].upper()} when level 1 is ready.")
        self.root.after(POLL_MS, self.poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def load_display_settings(self):
        # Older settings may contain mode=chroma/transparent; ignore it.
        data = {"background": DEFAULT_BG, "always_on_top": False}
        for path in (DISPLAY_FILE, LEGACY_DISPLAY_FILE):
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    continue
                value = saved.get("background", DEFAULT_BG)
                if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                    data["background"] = value.upper()
                if isinstance(saved.get("always_on_top"), bool):
                    data["always_on_top"] = saved["always_on_top"]
                return data
            except (OSError, ValueError, TypeError):
                pass
        return data

    def active_background(self):
        return self.bg_color

    def save_display_settings(self):
        try:
            DISPLAY_FILE.write_text(json.dumps({
                "background": self.bg_color,
                "always_on_top": self.always_on_top
            }, indent=2), encoding="utf-8")
        except OSError as exc:
            self.log(f"Could not save display settings: {exc}")

    def apply_display_settings(self):
        self.root.configure(bg=self.bg_color)
        for widget in self.runner_widgets:
            widget.configure(bg=self.bg_color)
        self.root.attributes("-topmost", self.always_on_top)
        if self.topmost_choice is not None:
            self.topmost_choice.set(self.always_on_top)
        self.save_display_settings()

    def change_always_on_top(self):
        self.always_on_top = bool(self.topmost_choice.get())
        self.apply_display_settings()
        self.log(f"Always on top: {'ON' if self.always_on_top else 'OFF'}")

    def toggle_always_on_top(self):
        self.always_on_top = not self.always_on_top
        self.apply_display_settings()
        self.log(f"Always on top: {'ON' if self.always_on_top else 'OFF'}")

    def set_background(self, value):
        value = value.strip().upper()
        if not re.fullmatch(r"#[0-9A-F]{6}", value):
            messagebox.showerror("Invalid hex", "Enter a color like #101218.", parent=self.settings_window)
            return
        self.bg_color = value
        self.apply_display_settings()
        self.log(f"Background set to {value}")

    def pick_background(self):
        result = colorchooser.askcolor(color=self.bg_color, parent=self.settings_window)
        if result[1]:
            self.set_background(result[1])
            if self.bg_entry is not None:
                self.bg_entry.delete(0, "end")
                self.bg_entry.insert(0, self.bg_color)

    def show_context_menu(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Controls / Settings", command=self.open_settings)
        menu.add_command(label=("Disable" if self.always_on_top else "Enable") + " always on top",
                         command=self.toggle_always_on_top)
        menu.add_separator()
        menu.add_command(label="Exit", command=self.close)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def build_ui(self):
        # The labels and container share the same solid background color.
        families = set(tkfont.families(self.root))
        timer_family = next((name for name in ("Cascadia Mono", "Consolas", "Courier New")
                             if name in families), "Courier New")
        frame = tk.Frame(self.root, bg=self.active_background(), padx=18, pady=12)
        frame.pack(fill="both", expand=True)
        self.timer_frame = frame
        self.runner_widgets.append(frame)

        def label(parent, **kwargs):
            widget = tk.Label(parent, bg=self.active_background(), **kwargs)
            self.runner_widgets.append(widget)
            return widget

        def timer_section(title):
            label(frame, text=title, fg="#FFFFFF",
                  font=(timer_family, 14, "bold"), anchor="w").pack(anchor="w")
            digits = label(frame, text="00:00:00.00", fg="#FFFFFF",
                           font=(timer_family, 30, "bold"), anchor="w", padx=0, pady=0)
            digits.pack(anchor="w")
            return digits

        self.rta_label = timer_section("REAL TIME")
        # Clean spacing between the two timers.
        spacer = tk.Frame(frame, bg=self.active_background(), height=10)
        spacer.pack(fill="x")
        self.runner_widgets.append(spacer)
        self.big = timer_section("IN-GAME TIME")

        # Keep level/status out of the OBS timer; it is available in Settings.
        self.info = tk.StringVar(value="Waiting for game")
        self.details = tk.StringVar(value="")
        self.action_buttons = {}
        self.hotkey_labels = {}
        self.logbox = None
        for widget in self.runner_widgets:
            widget.bind("<Button-3>", self.show_context_menu)
            widget.bind("<Double-Button-1>", lambda _event: self.open_settings())
        self.root.bind("<Button-3>", self.show_context_menu)

    def open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.deiconify()
            self.settings_window.lift()
            return
        win = tk.Toplevel(self.root)
        self.settings_window = win
        win.title("PAPERHEAD — Controls and Debug")
        win.geometry("720x700")
        win.minsize(630, 520)
        win.protocol("WM_DELETE_WINDOW", self.hide_settings)
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        colors = ttk.LabelFrame(frame, text="Runner display / OBS", padding=8)
        colors.pack(fill="x", pady=(0, 10))
        self.topmost_choice = tk.BooleanVar(value=self.always_on_top)
        ttk.Checkbutton(colors, text="Always on top (keep timer above other windows)",
                        variable=self.topmost_choice, command=self.change_always_on_top).pack(
                            anchor="w", pady=(0, 8))
        bg_row = ttk.Frame(colors)
        bg_row.pack(fill="x")
        ttk.Label(bg_row, text="Background HEX:").pack(side="left", padx=(0, 6))
        self.bg_entry = ttk.Entry(bg_row, width=12)
        self.bg_entry.insert(0, self.bg_color)
        self.bg_entry.pack(side="left")
        ttk.Button(bg_row, text="Apply", command=lambda: self.set_background(self.bg_entry.get())).pack(side="left", padx=5)
        ttk.Button(bg_row, text="Pick color", command=self.pick_background).pack(side="left")
        ttk.Label(colors, text="The selected color fills the entire timer background. No chroma key, transparent, or display modes. Always on top works with most windowed and borderless games; exclusive fullscreen may cover it.",
                  wraplength=600).pack(anchor="w", pady=(7, 0))
        ttk.Label(frame, text="Controls (global hotkeys work while game is focused)").pack(anchor="w")
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=8)
        for action in ("finish", "resume", "checkpoint", "reset", "debug", "close"):
            btn = ttk.Button(bar, command=getattr(self, action))
            btn.pack(side="left", padx=3)
            self.action_buttons[action] = btn
        hotkey_frame = ttk.LabelFrame(frame, text="Hotkeys — click Change, then press a key", padding=8)
        hotkey_frame.pack(fill="x", pady=(2, 10))
        for index, action in enumerate(("finish", "resume", "checkpoint", "reset", "debug", "close")):
            ttk.Label(hotkey_frame, text=ACTION_NAMES[action], width=15).grid(
                row=index, column=0, sticky="w", padx=(2, 12), pady=3)
            label = ttk.Label(hotkey_frame, text=self.hotkeys[action].upper(), width=24)
            label.grid(row=index, column=1, sticky="w")
            self.hotkey_labels[action] = label
            ttk.Button(hotkey_frame, text="Change", command=lambda a=action: self.begin_capture(a)).grid(
                row=index, column=2, padx=(5, 5))
        ttk.Button(hotkey_frame, text="Restore defaults", command=self.restore_defaults).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(7, 0))
        ttk.Label(hotkey_frame, text="Press Esc to cancel binding.").grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(5, 0))
        ttk.Label(frame, textvariable=self.details, font=("Consolas", 10)).pack(anchor="w")
        ttk.Label(frame, text="Event log").pack(anchor="w", pady=(8, 0))
        self.logbox = tk.Text(frame, height=12, state="disabled", font=("Consolas", 9))
        self.logbox.pack(fill="both", expand=True)
        for line in self.log_history:
            self._append_log(line)
        self.refresh_button_labels()

    def hide_settings(self):
        if self.capture_action is not None:
            self.cancel_capture()
        if self.settings_window is not None:
            self.settings_window.withdraw()

    def _append_log(self, line):
        if self.logbox is None:
            return
        self.logbox.configure(state="normal")
        self.logbox.insert("end", line + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def log(self, message):
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        self.log_history.append(line)
        self.log_history = self.log_history[-300:]
        self._append_log(line)

    def load_hotkeys(self):
        try:
            path = SETTINGS_FILE if SETTINGS_FILE.exists() else LEGACY_SETTINGS_FILE
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
            return {action: str(data.get(action, default)).strip().lower()
                    for action, default in DEFAULT_HOTKEYS.items()}
        except (OSError, ValueError, TypeError):
            return DEFAULT_HOTKEYS.copy()

    def refresh_button_labels(self):
        for action, button in self.action_buttons.items():
            button.configure(text=f"{self.hotkeys[action].upper()}  {ACTION_NAMES[action]}")
        if hasattr(self, "hotkey_labels"):
            for action, label in self.hotkey_labels.items():
                if action != self.capture_action:
                    label.configure(text=self.hotkeys[action].upper())

    def begin_capture(self, action):
        if keyboard is None:
            self.log("Cannot capture keys: install keyboard with pip install keyboard.")
            return
        self.cancel_capture()
        self.capture_action = action
        self.capture_modifiers = set()
        self.unregister_hotkeys()  # Prevent a previous shortcut from firing while rebinding.
        self.hotkey_labels[action].configure(text="PRESS A KEY...")
        self.log(f"Listening for {ACTION_NAMES[action]} shortcut. Esc cancels.")
        try:
            self.capture_hook = keyboard.hook(self.on_capture_key, suppress=False)
        except Exception as exc:
            self.log(f"Could not listen for keys: {exc}")
            self.cancel_capture()

    def on_capture_key(self, event):
        # Runs on the keyboard library's worker thread; queue updates for Tk's main thread.
        name = str(event.name).lower()
        mods = {"left ctrl": "ctrl", "right ctrl": "ctrl", "ctrl": "ctrl",
                "left shift": "shift", "right shift": "shift", "shift": "shift",
                "left alt": "alt", "right alt": "alt", "alt": "alt",
                "left windows": "windows", "right windows": "windows", "windows": "windows"}
        if name in mods:
            if event.event_type == "down":
                self.capture_modifiers.add(mods[name])
            elif event.event_type == "up":
                self.capture_modifiers.discard(mods[name])
            return
        if event.event_type != "down":
            return
        if name == "esc":
            self.events.put(("capture_cancel", None))
            return
        modifier_order = ["ctrl", "shift", "alt", "windows"]
        combination = "+".join([m for m in modifier_order if m in self.capture_modifiers] + [name])
        self.events.put(("capture_done", combination))

    def cancel_capture(self):
        if self.capture_hook is not None and keyboard is not None:
            try:
                keyboard.unhook(self.capture_hook)
            except Exception:
                pass
        self.capture_hook = None
        self.capture_action = None
        self.capture_modifiers = set()
        if hasattr(self, "hotkey_labels"):
            self.refresh_button_labels()
        # Restore existing hotkeys unless we're shutting down.
        if hasattr(self, "action_buttons"):
            self.register_hotkeys()

    def finish_capture(self, value):
        action = self.capture_action
        if action is None:
            return
        if value in (v for k, v in self.hotkeys.items() if k != action):
            self.log(f"'{value}' is already assigned to another action. Try again.")
            return
        try:
            keyboard.parse_hotkey(value)
        except Exception as exc:
            self.log(f"Invalid hotkey '{value}': {exc}")
            return
        old = self.hotkeys[action]
        self.hotkeys[action] = value
        self.cancel_capture()
        if len(self.hotkey_handles) != len(self.hotkeys):
            self.hotkeys[action] = old
            self.register_hotkeys()
            self.refresh_button_labels()
            self.log("Could not register that shortcut; previous one restored.")
            return
        try:
            SETTINGS_FILE.write_text(json.dumps(self.hotkeys, indent=2), encoding="utf-8")
            self.log(f"Saved {ACTION_NAMES[action]} = {value.upper()}")
        except OSError as exc:
            self.log(f"Hotkey applied but not saved: {exc}")

    def unregister_hotkeys(self):
        if keyboard is not None:
            for handle in self.hotkey_handles:
                try:
                    keyboard.remove_hotkey(handle)
                except Exception:
                    pass
        self.hotkey_handles = []

    def register_hotkeys(self):
        self.unregister_hotkeys()
        if keyboard is None:
            self.log("keyboard module missing: GUI buttons still work.")
            return
        try:
            for action, hotkey in self.hotkeys.items():
                handle = keyboard.add_hotkey(
                    hotkey, lambda name=action: self.events.put(name), suppress=False)
                self.hotkey_handles.append(handle)
            self.log("Hotkeys active: " + ", ".join(
                f"{ACTION_NAMES[a]}={k}" for a, k in self.hotkeys.items()))
        except Exception as exc:
            self.unregister_hotkeys()
            self.log(f"Global hotkeys unavailable: {exc}. Use GUI buttons.")

    def restore_defaults(self):
        self.cancel_capture()
        self.hotkeys = DEFAULT_HOTKEYS.copy()
        self.register_hotkeys()
        self.refresh_button_labels()
        try:
            SETTINGS_FILE.write_text(json.dumps(self.hotkeys, indent=2), encoding="utf-8")
            self.log("Restored default hotkeys.")
        except OSError as exc:
            self.log(f"Defaults applied but not saved: {exc}")

    def attach(self):
        import time
        if time.monotonic() - self.last_attach_attempt < 2:
            return
        self.last_attach_attempt = time.monotonic()
        try:
            pm = pymem.Pymem(PROCESS_NAME)
            module = pymem.process.module_from_name(pm.process_handle, MODULE_NAME)
            if module is None:
                raise RuntimeError(f"Module {MODULE_NAME} not loaded")
            self.pm = pm
            self.module_base = module.lpBaseOfDll
            self.log(f"Connected to {PROCESS_NAME} / {MODULE_NAME}")
        except Exception as exc:
            self.pm = None
            self.module_base = None
            self.info.set(f"Waiting for {PROCESS_NAME}")

    def pointer_address(self, offsets):
        # Cheat Engine style: [[module+offset0]+offset1]...+last_offset
        addr = self.module_base + offsets[0]
        for offset in offsets[1:]:
            data = self.pm.read_bytes(addr, POINTER_BYTES)
            pointer = struct.unpack("<Q" if POINTER_BYTES == 8 else "<I", data)[0]
            if not pointer:
                raise RuntimeError("Null pointer in chain")
            addr = pointer + offset
        return addr

    def read_game(self):
        level = self.pm.read_int(self.pointer_address(LEVEL_OFFSETS))
        igt = self.pm.read_float(self.pointer_address(IGT_OFFSETS))
        if not (0 <= level <= 18 and 0 <= igt < 1_000_000):
            raise ValueError(f"Unexpected values: LevelNum={level}, IGT={igt}")
        return level, float(igt)

    def accumulate_igt(self, raw):
        """Count only forward game-time movement, never subtract checkpoint rollbacks.

        Each decrease starts a new segment. This retains elapsed time before
        checkpoint reloads and prevents small negative float jitter from
        reducing the displayed timer.
        """
        if self.last_raw is None:
            self.last_raw = raw
            return
        if raw < self.last_raw:
            self.level_offset += max(0.0, self.last_raw - self.level_baseline)
            self.level_baseline = raw
            if self.last_raw - raw > 0.05:
                self.log("IGT rolled back (checkpoint/restart); previous elapsed time preserved.")
        self.display_level = max(
            self.display_level,
            self.level_offset + max(0.0, raw - self.level_baseline),
        )
        self.last_raw = raw

    def poll(self):
        try:
            while not self.events.empty():
                event = self.events.get_nowait()
                if isinstance(event, tuple):
                    kind, value = event
                    if kind == "capture_cancel":
                        self.log("Key binding canceled.")
                        self.cancel_capture()
                    elif kind == "capture_done":
                        self.finish_capture(value)
                elif self.capture_action is None:
                    getattr(self, event)()
            if self.pm is None:
                self.attach()
            if self.pm is not None:
                try:
                    level, raw = self.read_game()
                    self.level = level
                    if self.mode == "TRACKING":
                        self.accumulate_igt(raw)
                    elif self.mode != "CHECKPOINT":
                        self.last_raw = raw
                    # During a checkpoint reload keep the frozen level time visible.
                    total = self.total_completed + (self.display_level if self.mode in ("TRACKING", "CHECKPOINT") else 0.0)
                    self.big.configure(text=fmt(total))
                    self.info.set(f"Level {level:2d}    State: {self.mode}")
                    self.details.set(f"Raw: {fmt(raw)}    Level IGT: {fmt(self.display_level)}")
                except Exception as exc:
                    self.log(f"Read failed: {exc}")
                    self.pm = None
                    self.module_base = None
                    self.info.set("Connection lost; reconnecting...")
        finally:
            rta = self.rta_elapsed
            if self.rta_start is not None:
                rta += time.monotonic() - self.rta_start
            self.rta_label.configure(text=fmt(rta))
            self.root.after(POLL_MS, self.poll)

    def finish(self):
        if self.pm is None or self.mode != "TRACKING":
            self.log("Finish ignored: not tracking a level.")
            return
        try:
            level, raw = self.read_game()
            # Sample immediately on Finish, including checkpoint rollback handling.
            self.accumulate_igt(raw)
            level_time = self.display_level
            self.total_completed += level_time
            self.locked_level, self.locked_igt = level, level_time
            self.display_level = level_time
            self.mode = "TRANSITION"
            self.big.configure(text=fmt(self.total_completed))
            self.log(f"FINISH LEVEL {level} LOCKED: {fmt(level_time)} | TOTAL {fmt(self.total_completed)}")
        except Exception as exc:
            self.log(f"F1 failed: {exc}")

    def checkpoint(self):
        """Freeze the current level before the player triggers a checkpoint reload."""
        if self.pm is None or self.mode != "TRACKING":
            self.log("Checkpoint ignored: not tracking a level.")
            return
        try:
            level, raw = self.read_game()
            self.accumulate_igt(raw)
            self.checkpoint_raw = raw
            self.checkpoint_time = self.display_level
            self.mode = "CHECKPOINT"
            self.big.configure(text=fmt(self.total_completed + self.display_level))
            self.log(f"CHECKPOINT RELOAD ARMED (level {level}) | "
                     f"raw {fmt(raw)} | saved level {fmt(self.display_level)}. "
                     f"Press {self.hotkeys['resume'].upper()} after loading.")
        except Exception as exc:
            self.log(f"Checkpoint failed: {exc}")

    def resume(self):
        if self.pm is None:
            self.log("Resume ignored: not connected.")
            return
        if self.mode == "TRACKING":
            self.log("Resume ignored: already tracking.")
            return
        try:
            level, raw = self.read_game()
            if level == 0:
                self.log("Resume ignored: still in menu (LevelNum=0).")
                return
            if self.mode == "CHECKPOINT":
                if self.checkpoint_raw is None or self.checkpoint_time is None:
                    self.log("Resume ignored: no checkpoint snapshot.")
                    return
                # Preserve every second earned before the reload. On a cancel
                # (no rollback), also include forward IGT since J was pressed.
                rollback = raw < self.checkpoint_raw - 0.05
                if rollback:
                    lost = self.checkpoint_raw - raw
                    self.display_level = self.checkpoint_time
                    self.log(f"CHECKPOINT LOADED | IGT rolled back {lost:.2f}s; "
                             f"preserved {fmt(self.display_level)}")
                else:
                    self.display_level = self.checkpoint_time + max(0.0, raw - self.checkpoint_raw)
                    self.log("CHECKPOINT RESUMED (no rollback detected).")
                # New segment begins at the new raw value; the old accumulated
                # level time is carried forward as an offset, never discarded.
                self.level_offset = self.display_level
                self.level_baseline = raw
                self.last_raw = raw
                self.checkpoint_raw = None
                self.checkpoint_time = None
                self.mode = "TRACKING"
                self.big.configure(text=fmt(self.total_completed + self.display_level))
                return
            if self.mode == "TRANSITION" and level == self.locked_level:
                self.log("Next level ignored: LevelNum has not changed yet.")
                return
            self.level_baseline = raw
            self.last_raw = raw
            self.level_offset = 0.0
            self.display_level = 0.0
            self.mode = "TRACKING"
            if self.rta_start is None:
                self.rta_start = time.monotonic()
            self.log(f"NEXT LEVEL {level} STARTED | IGT baseline {fmt(raw)}")
        except Exception as exc:
            self.log(f"Resume failed: {exc}")

    def reset(self):
        self.rta_start = None
        self.rta_elapsed = 0.0
        self.rta_label.configure(text=fmt(0))
        self.mode = "WAITING"
        self.total_completed = 0.0
        self.level_baseline = 0.0
        self.level_offset = 0.0
        self.display_level = 0.0
        self.last_raw = None
        self.locked_level = None
        self.locked_igt = None
        self.checkpoint_raw = None
        self.checkpoint_time = None
        self.big.configure(text=fmt(0))
        self.log(f"RESET. Press {self.hotkeys['resume'].upper()} when level 1 is ready.")

    def debug(self):
        self.log(f"DEBUG mode={self.mode} level={self.level} checkpointRaw={self.checkpoint_raw} "
                 f"lastRaw={self.last_raw} baseline={self.level_baseline:.3f} "
                 f"completed={self.total_completed:.3f} levelTime={self.display_level:.3f}")

    def close(self):
        if self.capture_hook is not None and keyboard is not None:
            try:
                keyboard.unhook(self.capture_hook)
            except Exception:
                pass
        self.unregister_hotkeys()
        self.root.destroy()


if __name__ == "__main__":
    app = tk.Tk()
    Tracker(app)
    app.mainloop()
