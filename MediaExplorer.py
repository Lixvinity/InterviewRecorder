import os
import shutil
import tempfile
import numpy as np
from pathlib import Path
import ttkbootstrap as tb
from ttkbootstrap.scrolled import ScrolledFrame
from ttkbootstrap.dialogs import Messagebox
from ttkbootstrap.dialogs.dialogs import Querybox
from tkinter import filedialog
from pydub import AudioSegment
import tkinter as tk
import sys

# --- PyInstaller Path Logic ---
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        # Fallback to the directory where the script is actually running
        base_path = os.path.abspath(".")
    
    return os.path.join(base_path, relative_path)

# 1. BUNDLED ASSETS
# Ensure ffmpeg.exe and ffprobe.exe are in your project root during the build process
FFMPEG_PATH = resource_path("ffmpeg.exe")
FFPROBE_PATH = resource_path("ffprobe.exe")

# Assign paths to pydub
AudioSegment.converter = FFMPEG_PATH
AudioSegment.ffprobe = FFPROBE_PATH

# --- CRITICAL ADDITION: Inject the project folder into the system PATH ---
# This ensures any other library or standard subprocess call finds them here first
project_bin_dir = os.path.dirname(FFMPEG_PATH)
os.environ["PATH"] = project_bin_dir + os.pathsep + os.environ.get("PATH", "")

# 2. DYNAMIC IMPORTS
try:
    from movieengine import MovieEngineApp 
except ImportError:
    MovieEngineApp = None

class MediaExplorer(tb.Toplevel): 
    def __init__(self, master, folder_path):
        super().__init__(master)
        self.folder_path = Path(folder_path)
        
        self.title("Logged Interviews")
        self.geometry("900x600") 
        self.attributes('-topmost', True) 

        # Corrected Icon Pathing
        icon_path = resource_path(os.path.join("DefaultImages", "PDA.ico"))
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass 

        header = tb.Label(self, text="Logged Interviews", font=("Helvetica", 22, "bold"), bootstyle="light")
        header.pack(pady=20)

        self.list_frame = ScrolledFrame(self, autohide=True)
        self.list_frame.pack(fill="both", expand=True, padx=25, pady=10)

        self.load_files()

    def load_files(self):
        for widget in self.list_frame.winfo_children():
            widget.destroy()

        if not self.folder_path.exists():
            tb.Label(self.list_frame, text=f"Folder not found: {self.folder_path}", bootstyle="danger").pack(pady=20)
            return

        files = sorted(self.folder_path.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True)

        if not files:
            tb.Label(self.list_frame, text="No MP3 files found.", bootstyle="info").pack(pady=20)

        for mp3_path in files:
            self.create_file_row(mp3_path)

    def open_movie_maker(self, audio_path):
        if MovieEngineApp is None:
            Messagebox.show_error("movieengine.py not found or failed to load.", "Module Error")
            return
        
        movie_window = tk.Toplevel(self)
        MovieEngineApp(movie_window, audio_file=str(audio_path))

    def create_file_row(self, file_path):
        row_container = tb.Frame(self.list_frame)
        row_container.pack(fill="x", pady=5)

        display_name = (file_path.name[:35] + '..') if len(file_path.name) > 35 else file_path.name
        tb.Label(row_container, text=display_name, width=40, anchor="w").pack(side="left", padx=10)

        # Packed from Right to Left
        tb.Button(row_container, text="Delete", bootstyle="danger-outline", width=8,
                  command=lambda fp=file_path: self.delete_file(fp)).pack(side="right", padx=3)
        
        tb.Button(row_container, text="Play", bootstyle="success", width=8, 
                  command=lambda fp=file_path: self.process_and_play(fp)).pack(side="right", padx=3)

        tb.Button(row_container, text="Make Video", bootstyle="info", width=12, 
                  command=lambda fp=file_path: self.open_movie_maker(fp)).pack(side="right", padx=3)

        tb.Button(row_container, text="Export", bootstyle="warning", width=8, 
                  command=lambda fp=file_path: self.open_export_dialog(fp)).pack(side="right", padx=3)

        tb.Button(row_container, text="Rename", bootstyle="secondary", width=8, 
                  command=lambda fp=file_path: self.rename_file(fp)).pack(side="right", padx=3)

        tb.Separator(self.list_frame, bootstyle="dark").pack(fill="x", padx=10, pady=2)

    def rename_file(self, file_path):
        new_name = Querybox.get_string(
            prompt="Enter new file name (without extension):", 
            title="Rename File", 
            initialvalue=file_path.stem
        )
        if new_name:
            new_path = file_path.with_name(f"{new_name}.mp3")
            if new_path.exists():
                Messagebox.show_error("A file with this name already exists.", "Rename Error")
            else:
                try:
                    file_path.rename(new_path)
                    self.load_files()
                except Exception as e:
                    Messagebox.show_error(f"Could not rename file: {e}", "Rename Error")

    def open_export_dialog(self, file_path):
        export_win = tb.Toplevel(self)
        export_win.title("Export Options")
        export_win.geometry("300x230")
        export_win.resizable(False, False)
        export_win.attributes('-topmost', True)
        export_win.grab_set()  # Make window modal

        tb.Label(export_win, text=f"Export: {file_path.name[:20]}...", font=("Helvetica", 12, "bold")).pack(pady=15)

        def choose_dir_and_export(mode):
            export_dir = filedialog.askdirectory(title="Select Destination Folder")
            if not export_dir:
                return
            export_win.destroy()
            self.perform_export(file_path, Path(export_dir), mode)

        tb.Button(export_win, text="Merged Channels (Mono)", bootstyle="primary", 
                  command=lambda: choose_dir_and_export("merged")).pack(fill="x", padx=20, pady=5)
        tb.Button(export_win, text="Split Channels (L/R)", bootstyle="primary", 
                  command=lambda: choose_dir_and_export("split")).pack(fill="x", padx=20, pady=5)
        tb.Button(export_win, text="Raw File (Copy)", bootstyle="primary", 
                  command=lambda: choose_dir_and_export("raw")).pack(fill="x", padx=20, pady=5)

    def perform_export(self, file_path, export_dir, mode):
        try:
            base_name = file_path.stem
            
            if mode == "raw":
                shutil.copy2(file_path, export_dir / file_path.name)
                Messagebox.show_info("Raw file exported successfully.", "Export Complete")
                
            elif mode == "merged":
                audio = AudioSegment.from_mp3(file_path)
                # pydub's set_channels(1) mixes down multi-channel audio to mono
                mono_audio = audio.set_channels(1)
                export_path = export_dir / f"{base_name}_merged.mp3"
                mono_audio.export(export_path, format="mp3")
                Messagebox.show_info("Merged file exported successfully.", "Export Complete")
                
            elif mode == "split":
                audio = AudioSegment.from_mp3(file_path)
                if audio.channels == 1:
                    Messagebox.show_warning("File is already mono. Exporting as is.", "Notice")
                    export_path = export_dir / f"{base_name}_mono.mp3"
                    audio.export(export_path, format="mp3")
                else:
                    left, right = audio.split_to_mono()
                    left_path = export_dir / f"{base_name}_Left.mp3"
                    right_path = export_dir / f"{base_name}_Right.mp3"
                    left.export(left_path, format="mp3")
                    right.export(right_path, format="mp3")
                    Messagebox.show_info("Split channels exported successfully.", "Export Complete")
                    
        except Exception as e:
            Messagebox.show_error(f"Export failed: {e}", "Export Error")

    def process_and_play(self, file_path):
        try:
            audio = AudioSegment.from_mp3(file_path)
            samples = np.array(audio.get_array_of_samples())
            
            if audio.channels > 1:
                samples = samples.reshape((-1, audio.channels))
                merged_float = samples.astype(np.float64).mean(axis=1)
                mono_samples = merged_float.astype(samples.dtype)
            else:
                mono_samples = samples

            mono_audio = audio._spawn(mono_samples.tobytes(), overrides={
                'channels': 1,
                'frame_rate': audio.frame_rate
            })
            
            temp_dir = Path(tempfile.gettempdir())
            temp_file = temp_dir / f"preview_{file_path.name}"
            
            mono_audio.export(temp_file, format="mp3")
            os.startfile(temp_file)
            
        except Exception as e:
            Messagebox.show_error(f"Playback failed: {e}", "Audio Error")

    def delete_file(self, file_path):
        if Messagebox.yesno(f"Permanently delete {file_path.name}?", "Confirm Deletion") == "Yes":
            try:
                file_path.unlink()
                self.load_files()
            except Exception as e:
                Messagebox.show_error(f"Could not delete file: {e}", "File Error")

if __name__ == "__main__":
    root = tb.Window(themename="darkly")
    # Note: Make sure this directory exists or use a dynamic path
    my_path = Path.home() / "Documents" / "recordings"
    
    # Create the directory if it doesn't exist to prevent errors on initial load
    my_path.mkdir(parents=True, exist_ok=True)
    
    app = MediaExplorer(root, my_path)
    root.mainloop()
