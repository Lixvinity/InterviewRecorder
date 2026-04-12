import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

import ttkbootstrap as tb
from ttkbootstrap.scrolled import ScrolledFrame
from ttkbootstrap.dialogs import Messagebox
from ttkbootstrap.dialogs.dialogs import Querybox
from tkinter import filedialog
from pydub import AudioSegment
import tkinter as tk
import sys


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def resource_path(relative_path: str) -> str:
    """Return absolute path — compatible with PyInstaller bundles and dev."""
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, relative_path)


def _configure_ffmpeg() -> None:
    """Point pydub at the bundled ffmpeg/ffprobe binaries and patch PATH."""
    ffmpeg  = resource_path("ffmpeg.exe")
    ffprobe = resource_path("ffprobe.exe")
    AudioSegment.converter = ffmpeg
    AudioSegment.ffprobe   = ffprobe
    # Prepend the folder so subprocesses from other libs find the binaries too.
    bin_dir = os.path.dirname(ffmpeg)
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

_configure_ffmpeg()


# ---------------------------------------------------------------------------
# Optional dependency
# ---------------------------------------------------------------------------

try:
    from movieengine import MovieEngineApp
except ImportError:
    MovieEngineApp = None


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _make_button(parent, text: str, style: str, width: int, cmd: Callable) -> tb.Button:
    """Create a ttkbootstrap button — single place to tweak global button style."""
    return tb.Button(parent, text=text, bootstyle=style, width=width, command=cmd)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MediaExplorer(tb.Toplevel):

    _SUPPORTED_EXT = "*.mp3"

    def __init__(self, master, folder_path):
        super().__init__(master)
        self.folder_path = Path(folder_path)

        self.title("Logged Interviews")
        self.geometry("900x600")
        self.attributes("-topmost", True)

        icon_path = resource_path(os.path.join("DefaultImages", "PDA.ico"))
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

        tb.Label(self, text="Logged Interviews",
                 font=("Helvetica", 22, "bold"), bootstyle="light").pack(pady=20)

        self.list_frame = ScrolledFrame(self, autohide=True)
        self.list_frame.pack(fill="both", expand=True, padx=25, pady=10)

        self._load_files()

    # ------------------------------------------------------------------
    # File list
    # ------------------------------------------------------------------

    def _load_files(self) -> None:
        """Rebuild the file list in-place (destroy → recreate rows)."""
        for w in self.list_frame.winfo_children():
            w.destroy()

        if not self.folder_path.exists():
            tb.Label(self.list_frame,
                     text=f"Folder not found: {self.folder_path}",
                     bootstyle="danger").pack(pady=20)
            return

        files = sorted(
            self.folder_path.glob(self._SUPPORTED_EXT),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not files:
            tb.Label(self.list_frame, text="No MP3 files found.",
                     bootstyle="info").pack(pady=20)
            return

        for mp3_path in files:
            self._create_file_row(mp3_path)

    def _create_file_row(self, file_path: Path) -> None:
        row = tb.Frame(self.list_frame)
        row.pack(fill="x", pady=5)

        name = file_path.name
        display = (name[:35] + "..") if len(name) > 35 else name
        tb.Label(row, text=display, width=40, anchor="w").pack(side="left", padx=10)

        # Button spec: (label, style, width, handler) — packed right→left
        buttons = [
            ("Delete",     "danger-outline", 8,  lambda fp=file_path: self._delete_file(fp)),
            ("Play",       "success",        8,  lambda fp=file_path: self._play(fp)),
            ("Make Video", "info",           12, lambda fp=file_path: self._open_movie_maker(fp)),
            ("Export",     "warning",        8,  lambda fp=file_path: self._open_export_dialog(fp)),
            ("Rename",     "secondary",      8,  lambda fp=file_path: self._rename_file(fp)),
        ]
        for label, style, width, cmd in buttons:
            _make_button(row, label, style, width, cmd).pack(side="right", padx=3)

        tb.Separator(self.list_frame, bootstyle="dark").pack(fill="x", padx=10, pady=2)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _rename_file(self, file_path: Path) -> None:
        new_name = Querybox.get_string(
            prompt="Enter new file name (without extension):",
            title="Rename File",
            initialvalue=file_path.stem,
        )
        if not new_name:
            return

        new_path = file_path.with_name(f"{new_name}.mp3")
        if new_path.exists():
            Messagebox.show_error("A file with this name already exists.", "Rename Error")
            return
        try:
            file_path.rename(new_path)
            self._load_files()
        except OSError as e:
            Messagebox.show_error(f"Could not rename file: {e}", "Rename Error")

    def _delete_file(self, file_path: Path) -> None:
        if Messagebox.yesno(f"Permanently delete {file_path.name}?", "Confirm Deletion") != "Yes":
            return
        try:
            file_path.unlink()
            self._load_files()
        except OSError as e:
            Messagebox.show_error(f"Could not delete file: {e}", "File Error")

    def _play(self, file_path: Path) -> None:
        """Convert to mono via pydub (no numpy needed) and open with the OS player."""
        try:
            audio = AudioSegment.from_mp3(file_path)
            # set_channels(1) handles both mono-passthrough and stereo→mono mix-down
            mono = audio.set_channels(1)
            tmp = Path(tempfile.gettempdir()) / f"preview_{file_path.name}"
            mono.export(tmp, format="mp3")
            os.startfile(tmp)
        except Exception as e:
            Messagebox.show_error(f"Playback failed: {e}", "Audio Error")

    def _open_movie_maker(self, audio_path: Path) -> None:
        if MovieEngineApp is None:
            Messagebox.show_error("movieengine.py not found or failed to load.", "Module Error")
            return
        MovieEngineApp(tk.Toplevel(self), audio_file=str(audio_path))

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _open_export_dialog(self, file_path: Path) -> None:
        win = tb.Toplevel(self)
        win.title("Export Options")
        win.geometry("300x230")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        win.grab_set()

        short = file_path.name[:20] + ".." if len(file_path.name) > 20 else file_path.name
        tb.Label(win, text=f"Export: {short}",
                 font=("Helvetica", 12, "bold")).pack(pady=15)

        def on_mode(mode: str) -> None:
            export_dir = filedialog.askdirectory(title="Select Destination Folder")
            if not export_dir:
                return
            win.destroy()
            self._export(file_path, Path(export_dir), mode)

        export_buttons = [
            ("Merged Channels (Mono)", "merged"),
            ("Split Channels (L/R)",   "split"),
            ("Raw File (Copy)",         "raw"),
        ]
        for label, mode in export_buttons:
            tb.Button(win, text=label, bootstyle="primary",
                      command=lambda m=mode: on_mode(m)).pack(fill="x", padx=20, pady=5)

    def _export(self, file_path: Path, export_dir: Path, mode: str) -> None:
        try:
            stem = file_path.stem

            if mode == "raw":
                shutil.copy2(file_path, export_dir / file_path.name)
                Messagebox.show_info("Raw file exported successfully.", "Export Complete")

            elif mode == "merged":
                mono = AudioSegment.from_mp3(file_path).set_channels(1)
                mono.export(export_dir / f"{stem}_merged.mp3", format="mp3")
                Messagebox.show_info("Merged file exported successfully.", "Export Complete")

            elif mode == "split":
                audio = AudioSegment.from_mp3(file_path)
                if audio.channels == 1:
                    audio.export(export_dir / f"{stem}_mono.mp3", format="mp3")
                    Messagebox.show_warning("File is mono — exported as-is.", "Notice")
                else:
                    left, right = audio.split_to_mono()
                    left.export(export_dir / f"{stem}_Left.mp3",  format="mp3")
                    right.export(export_dir / f"{stem}_Right.mp3", format="mp3")
                    Messagebox.show_info("Split channels exported.", "Export Complete")

        except Exception as e:
            Messagebox.show_error(f"Export failed: {e}", "Export Error")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    root = tb.Window(themename="darkly")
    recordings = Path.home() / "Documents" / "recordings"
    recordings.mkdir(parents=True, exist_ok=True)
    MediaExplorer(root, recordings)
    root.mainloop()
