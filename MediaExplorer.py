import os
import tempfile
import numpy as np
from pathlib import Path
import ttkbootstrap as tb
from ttkbootstrap.scrolled import ScrolledFrame
from ttkbootstrap.dialogs import Messagebox
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
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# 1. CRITICAL: Tell pydub where bundled ffmpeg is
# Note: You must include ffmpeg.exe in your PyInstaller --add-data command
FFMPEG_PATH = resource_path("ffmpeg.exe")
AudioSegment.converter = FFMPEG_PATH

# 2. IMPORT YOUR MOVIE UI SCRIPT
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
        
        # Ensures the window stays on top of the main dashboard
        self.attributes('-topmost', True) 

        # Corrected Icon Pathing
        icon_path = resource_path(os.path.join("DefaultImages", "PDA.ico"))
        if os.path.exists(icon_path):
            self.iconbitmap(icon_path)

        header = tb.Label(self, text="Logged Interviews", font=("Helvetica", 22, "bold"), bootstyle="light")
        header.pack(pady=20)

        self.list_frame = ScrolledFrame(self, autohide=True)
        self.list_frame.pack(fill="both", expand=True, padx=25, pady=10)

        self.load_files()

    def load_files(self):
        # Clear existing rows
        for widget in self.list_frame.winfo_children():
            widget.destroy()

        if not self.folder_path.exists():
            tb.Label(self.list_frame, text=f"Folder not found: {self.folder_path}", bootstyle="danger").pack(pady=20)
            return

        # Sort files by modification time (newest first)
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
        # Pass the audio path to your MovieEngineApp class
        MovieEngineApp(movie_window, audio_file=str(audio_path))

    def create_file_row(self, file_path):
        row_container = tb.Frame(self.list_frame)
        row_container.pack(fill="x", pady=5)

        # Truncate long filenames for UI stability
        display_name = (file_path.name[:35] + '..') if len(file_path.name) > 35 else file_path.name
        tb.Label(row_container, text=display_name, width=40, anchor="w").pack(side="left", padx=10)

        # UI BUTTONS
        tb.Button(row_container, text="Delete", bootstyle="danger-outline", width=8,
                  command=lambda fp=file_path: self.delete_file(fp)).pack(side="right", padx=3)
        
        tb.Button(row_container, text="Play", bootstyle="success", width=8, 
                  command=lambda fp=file_path: self.process_and_play(fp)).pack(side="right", padx=3)

        tb.Button(row_container, text="Make Video", bootstyle="info", width=12, 
                  command=lambda fp=file_path: self.open_movie_maker(fp)).pack(side="right", padx=3)

        tb.Separator(self.list_frame, bootstyle="dark").pack(fill="x", padx=10, pady=2)

    def process_and_play(self, file_path):
        """ Processes audio into mono for preview and opens system player """
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
            Messagebox.show_error(f"Playback failed: {e}\n\nNote: Ensure ffmpeg.exe is in the project root.", "Audio Error")

    def delete_file(self, file_path):
        if Messagebox.yesno(f"Permanently delete {file_path.name}?", "Confirm Deletion") == "Yes":
            try:
                file_path.unlink()
                self.load_files()
            except Exception as e:
                Messagebox.show_error(f"Could not delete file: {e}", "File Error")

if __name__ == "__main__":
    # Standard development testing block
    root = tb.Window(themename="darkly")
    my_path = Path.home() / "Documents" / "recordings"
    app = MediaExplorer(root, my_path)
    root.mainloop()
