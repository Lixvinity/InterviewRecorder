import os
import tempfile
import numpy as np
from pathlib import Path
import ttkbootstrap as tb
from ttkbootstrap.scrolled import ScrolledFrame
from ttkbootstrap.dialogs import Messagebox
from pydub import AudioSegment
import tkinter as tk

# 1. IMPORT YOUR MOVIE UI SCRIPT
# Assuming your UI script is named movie_ui.py
from movieengine import MovieEngineApp 

class MediaExplorer(tb.Toplevel): 
    def __init__(self, master, folder_path):
        super().__init__(master)
        self.folder_path = Path(folder_path)
        
        self.title("Logged Interviews")
        self.geometry("900x600") # Widened slightly to fit the new button
        self.attributes('-topmost', True) 

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

    # 2. NEW METHOD TO LAUNCH THE MOVIE UI
    def open_movie_maker(self, audio_path):
        # Create a new Toplevel window for the Movie Engine
        movie_window = tk.Toplevel(self)
        # Pass the audio path to your MovieEngineApp class
        MovieEngineApp(movie_window, audio_file=str(audio_path))

    def create_file_row(self, file_path):
        row_container = tb.Frame(self.list_frame)
        row_container.pack(fill="x", pady=5)

        display_name = (file_path.name[:35] + '..') if len(file_path.name) > 35 else file_path.name
        tb.Label(row_container, text=display_name, width=40, anchor="w").pack(side="left", padx=10)

        # DELETE BUTTON
        tb.Button(row_container, text="Delete", bootstyle="danger-outline", width=8,
                  command=lambda fp=file_path: self.delete_file(fp)).pack(side="right", padx=3)
        
        # PLAY BUTTON
        tb.Button(row_container, text="Play", bootstyle="success", width=8, 
                  command=lambda fp=file_path: self.process_and_play(fp)).pack(side="right", padx=3)

        # 3. THE NEW "MAKE VIDEO" BUTTON
        tb.Button(row_container, text="Make Video", bootstyle="info", width=12, 
                  command=lambda fp=file_path: self.open_movie_maker(fp)).pack(side="right", padx=3)

        tb.Separator(self.list_frame, bootstyle="dark").pack(fill="x", padx=10, pady=2)

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
            
            temp_file = Path(tempfile.gettempdir()) / f"merged_{file_path.name}"
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
    # Update this path to your recordings folder
    my_path = r"C:\Users\James\Documents\recordings" 
    app = MediaExplorer(root, my_path)
    root.mainloop()
