import tkinter as tk
from tkinter import ttk, filedialog, font
from ttkbootstrap import Style
from tkinter.scrolledtext import ScrolledText
from PIL import Image, ImageTk
import os
import threading
import tempfile
from pathlib import Path
from pydub import AudioSegment
from pulsar import VideoGenerator 

class MovieEngineApp:
    def __init__(self, root, audio_file=None):
        self.root = root
        self.root.title("Movie Engine")
        self.root.geometry("500x950") 
        self.audio_file = audio_file
        self.style = Style(theme="darkly")
        self.images = {} 
        self.export_path = str(Path.home() / "Downloads")

        self.main_frame = ttk.Frame(self.root, padding=20)
        self.main_frame.pack(fill="both", expand=True)

        ttk.Label(self.main_frame, text="MOVIE ENGINE", font=("Helvetica", 28, "bold")).pack(pady=(0, 20))

        # Asset Rows
        self.bg_canvas = self.create_asset_row("Background", "#302040")
        self.sp1_canvas = self.create_asset_row("Speaker 1", "#8a8ad4")
        self.sp2_canvas = self.create_asset_row("Speaker 2", "#40b0a0")

        self.create_signature_row()

        ttk.Separator(self.main_frame, orient="horizontal").pack(fill="x", pady=15)

        # Buttons
        btn_frame = ttk.Frame(self.main_frame)
        btn_frame.pack(pady=10, fill="x")

        self.export_btn = ttk.Button(btn_frame, text="📁 Set Export", bootstyle="secondary", command=self.select_export_folder)
        self.export_btn.pack(side="left", padx=5, expand=True, fill="x")

        self.gen_button = ttk.Button(btn_frame, text="Generate", bootstyle="info", command=self.generate_action)
        self.style.configure('Large.TButton', font=("Helvetica", 14, "bold"))
        self.gen_button.configure(style='Large.TButton')
        self.gen_button.pack(side="left", padx=5, expand=True, fill="x")

        # Logging
        ttk.Label(self.main_frame, text="log", font=("Courier", 10)).pack(anchor="w")
        self.log_box = ScrolledText(self.main_frame, height=8, bg="#cccccc", fg="black", font=("Courier", 10))
        self.log_box.pack(fill="both", expand=True, pady=5)
        
        # Initialization
        self.log_message(f"Default export: {self.export_path}")
        if self.audio_file:
            self.log_message(f"Target Audio: {os.path.basename(self.audio_file)}")
        
        # Load Defaults into Previews
        self.load_default_assets()

    def create_asset_row(self, label_text, placeholder_color):
        frame = ttk.Frame(self.main_frame)
        frame.pack(fill="x", pady=10)
        left_inner = ttk.Frame(frame)
        left_inner.pack(side="left")
        ttk.Label(left_inner, text=label_text, font=("Helvetica", 12)).pack(anchor="w")
        
        canvas = tk.Canvas(frame, width=100, height=60, bg=placeholder_color, highlightthickness=0)
        canvas.pack(side="right")
        canvas.file_path = None 
        
        ttk.Button(left_inner, text="Change image", bootstyle="info", command=lambda c=canvas: self.load_image(c)).pack(anchor="w", pady=5)
        return canvas

    def load_image(self, canvas, file_path=None):
        """Loads image into canvas. If file_path is None, opens dialog."""
        if not file_path:
            file_path = filedialog.askopenfilename(filetypes=[("Image files", "*.jpg *.jpeg *.png *.bmp")])
        
        if file_path and os.path.exists(file_path):
            try:
                canvas.file_path = file_path 
                img = Image.open(file_path).resize((100, 60), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                self.images[canvas] = photo
                canvas.delete("all")
                canvas.create_image(0, 0, anchor="nw", image=photo)
                self.log_message(f"Loaded: {os.path.basename(file_path)}")
            except Exception as e:
                self.log_message(f"Failed to load {os.path.basename(file_path)}: {e}")

    def load_default_assets(self):
        """Attempts to load the default assets into the preview boxes on startup."""
        defaults = {
            self.bg_canvas: r"DefaultImages\FreeBackground.jpg",
            self.sp1_canvas: r"DefaultImages\icon2.png",
            self.sp2_canvas: r"DefaultImages\icon1.png"
        }
        for canvas, path in defaults.items():
            if os.path.exists(path):
                self.load_image(canvas, file_path=path)
            else:
                self.log_message(f"Notice: Default asset {os.path.basename(path)} not found.")

    def create_signature_row(self):
        sig_frame = ttk.Frame(self.main_frame)
        sig_frame.pack(fill="x", pady=10)
        ttk.Label(sig_frame, text="Signature", font=("Helvetica", 12)).pack(anchor="w")
        
        entry_frame = ttk.Frame(sig_frame)
        entry_frame.pack(fill="x", pady=5)
        
        self.sig_entry = ttk.Entry(entry_frame)
        self.sig_entry.insert(0, "PDA - https://discord.gg/Tvz2eHkxBe")
        self.sig_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

        # Get System Fonts
        system_fonts = sorted([f for f in font.families() if not f.startswith('@')])
        
        self.font_dropdown = ttk.Combobox(entry_frame, values=system_fonts, width=20)
        
        # Set default font preference
        if "Arial" in system_fonts:
            self.font_dropdown.set("Arial")
        elif len(system_fonts) > 0:
            self.font_dropdown.current(0)
            
        self.font_dropdown.pack(side="right")

    def select_export_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.export_path = path
            self.log_message(f"Export set to: {path}")

    def log_message(self, message):
        self.log_box.insert(tk.END, f"> {message}\n")
        self.log_box.see(tk.END)

    def generate_action(self):
        if not self.audio_file or not os.path.exists(self.audio_file):
            self.log_message("Error: Audio file missing!")
            return
        self.gen_button.configure(state="disabled")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        left_temp = None
        right_temp = None
        try:
            self.log_message("Processing audio channels...")
            audio = AudioSegment.from_file(self.audio_file)
            
            if audio.channels < 2:
                left_aud = right_aud = audio
            else:
                mono_channels = audio.split_to_mono()
                left_aud, right_aud = mono_channels[0], mono_channels[1]

            temp_dir = tempfile.gettempdir()
            left_temp = os.path.join(temp_dir, "sp1_audio.wav")
            right_temp = os.path.join(temp_dir, "sp2_audio.wav")
            left_aud.export(left_temp, format="wav")
            right_aud.export(right_temp, format="wav")

            # Final Assets (using current file_path from canvas or fallback strings)
            bg = self.bg_canvas.file_path or r"DefaultImages\FreeBackground.jpg"
            sp1 = self.sp1_canvas.file_path or r"DefaultImages\icon2.png"
            sp2 = self.sp2_canvas.file_path or r"DefaultImages\icon1.png"
            glow = r"DefaultImages\blurb.png" 
            
            self.log_message("Pulsar Engine started...")
            engine = VideoGenerator(width=1920, height=1080, target_h=720, fps=15)
            
            out = engine.generate(
                audio1_path=left_temp, 
                audio2_path=right_temp, 
                bg_path=bg, 
                icon1_path=sp2, 
                icon2_path=sp1, 
                glow_path=glow, 
                output_folder=self.export_path, 
                signature_text=self.sig_entry.get(), 
                font_name=self.font_dropdown.get()
            )
            self.log_message(f"Done! Created: {os.path.basename(out)}")

        except Exception as e:
            self.log_message(f"Error: {str(e)}")
        finally:
            for path in [left_temp, right_temp]:
                if path and os.path.exists(path):
                    try: os.remove(path)
                    except: pass
            self.root.after(0, lambda: self.gen_button.configure(state="normal"))

if __name__ == "__main__":
    root = tk.Tk()
    # Replace with your actual audio test path
    app = MovieEngineApp(root, audio_file="test_audio.mp3") 
    root.mainloop()
