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

import sys

import requests


# Try to import your custom engine; if it's bundled, PyInstaller handles it

try:

    from MK1Engine import run_video_generation

except ImportError:

    run_video_generation = None


# --- PyInstaller Path Logic ---

def resource_path(relative_path):

    """ Get absolute path to resource, works for dev and for PyInstaller """

    try:

        base_path = sys._MEIPASS

    except Exception:

        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


# Resolve default asset paths immediately

DEFAULT_BG = resource_path(os.path.join("DefaultImages", "FreeBackground.jpg"))

DEFAULT_ICON1 = resource_path(os.path.join("DefaultImages", "icon1.png"))

DEFAULT_ICON2 = resource_path(os.path.join("DefaultImages", "icon2.png"))

DEFAULT_GLOW = resource_path(os.path.join("DefaultImages", "blurb.png"))

APP_ICON = resource_path(os.path.join("DefaultImages", "PDA.ico"))


class MovieEngineApp:

    def __init__(self, root, audio_file=None):

        self.root = root

        self.root.title("Movie Engine")

        self.root.geometry("500x980")

        self.audio_file = audio_file

        self.style = Style(theme="darkly")

        self.images = {}

        self.export_path = str(Path.home() / "Downloads")

       

        # Set window icon safely

        if os.path.exists(APP_ICON):

            self.root.iconbitmap(APP_ICON)


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


        # Discord Webhook

        webhook_frame = ttk.Frame(self.main_frame)

        webhook_frame.pack(fill="x", pady=(5, 10))

        ttk.Label(webhook_frame, text="autoupload to discord webhook", font=("Helvetica", 10)).pack(anchor="w")

        self.webhook_entry = ttk.Entry(webhook_frame)

        self.webhook_entry.pack(fill="x", pady=2)


        # Logging

        ttk.Label(self.main_frame, text="log", font=("Courier", 10)).pack(anchor="w")

        self.log_box = ScrolledText(self.main_frame, height=8, bg="#cccccc", fg="black", font=("Courier", 10))

        self.log_box.pack(fill="both", expand=True, pady=5)

       

        self.log_message(f"Default export: {self.export_path}")

        if self.audio_file:

            self.log_message(f"Target Audio: {os.path.basename(self.audio_file)}")

       

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

            except Exception as e:

                self.log_message(f"Load Error: {e}")


    def load_default_assets(self):

        defaults = {

            self.bg_canvas: DEFAULT_BG,

            self.sp1_canvas: DEFAULT_ICON2,

            self.sp2_canvas: DEFAULT_ICON1

        }

        for canvas, path in defaults.items():

            if os.path.exists(path):

                self.load_image(canvas, file_path=path)


    def create_signature_row(self):

        sig_frame = ttk.Frame(self.main_frame)

        sig_frame.pack(fill="x", pady=10)

        ttk.Label(sig_frame, text="Signature", font=("Helvetica", 12)).pack(anchor="w")

        entry_frame = ttk.Frame(sig_frame)

        entry_frame.pack(fill="x", pady=5)

        self.sig_entry = ttk.Entry(entry_frame)

        self.sig_entry.insert(0, "PDA - https://discord.gg/Tvz2eHkxBe")

        self.sig_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))

       

        system_fonts = sorted([f for f in font.families() if not f.startswith('@')])

        self.font_dropdown = ttk.Combobox(entry_frame, values=system_fonts, width=20)

        self.font_dropdown.set("Arial" if "Arial" in system_fonts else system_fonts[0] if system_fonts else "")

        self.font_dropdown.pack(side="right")


    def select_export_folder(self):

        path = filedialog.askdirectory()

        if path:

            self.export_path = path

            self.log_message(f"Export set to: {path}")


    def log_message(self, message):

        self.log_box.insert(tk.END, f"> {message}\n")

        self.log_box.see(tk.END)


    def upload_to_catbox(self, file_path):

        try:

            url = "https://catbox.moe/user/api.php"

            data = {"reqtype": "fileupload", "userhash": ""}

            with open(file_path, 'rb') as f:

                files = {'fileToUpload': f}

                response = requests.post(url, data=data, files=files)

            return response.text if response.status_code == 200 else None

        except Exception as e:

            self.log_message(f"Upload failed: {e}")

            return None


    def send_to_webhook(self, webhook_url, content_url):

        try:

            requests.post(webhook_url, json={"content": content_url})

        except: pass


    def generate_action(self):

        if not self.audio_file or not os.path.exists(self.audio_file):

            self.log_message("Error: Audio file missing!")

            return

        self.gen_button.configure(state="disabled")

        threading.Thread(target=self._worker, daemon=True).start()


    def _worker(self):

        left_temp, right_temp = None, None

        try:

            self.log_message("Splitting audio...")

            audio = AudioSegment.from_file(self.audio_file)

           

            # Split mono/stereo logic

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


            bg = self.bg_canvas.file_path or DEFAULT_BG

            sp1 = self.sp1_canvas.file_path or DEFAULT_ICON2

            sp2 = self.sp2_canvas.file_path or DEFAULT_ICON1

           

            if run_video_generation is None:

                raise ImportError("MK1Engine not found!")


            self.log_message("Pulsar Engine V2 MK1 started...")

            engine = run_video_generation(width=1920, height=1080, target_h=720, fps=15)

           

            out_file = engine.generate(

                audio1_path=left_temp,

                audio2_path=right_temp,

                bg_path=bg,

                icon1_path=sp2,

                icon2_path=sp1,

                glow_path=DEFAULT_GLOW,

                output_folder=self.export_path,

                signature_text=self.sig_entry.get(),

                font_name=self.font_dropdown.get()

            )

           

            self.log_message(f"Created: {os.path.basename(out_file)}")


            webhook_url = self.webhook_entry.get().strip()

            if webhook_url:

                if (os.path.getsize(out_file) / (1024*1024)) > 200:

                    self.log_message("File too large for Discord (>200MB)")

                else:

                    self.log_message("Uploading...")

                    link = self.upload_to_catbox(out_file)

                    if link:

                        self.send_to_webhook(webhook_url, link)

                        self.log_message("Webhook sent!")


        except Exception as e:

            self.log_message(f"Error: {e}")

        finally:

            for p in [left_temp, right_temp]:

                if p and os.path.exists(p): os.remove(p)

            self.root.after(0, lambda: self.gen_button.configure(state="normal"))


if __name__ == "__main__":

    root = tk.Tk()

    app = MovieEngineApp(root)

    root.mainloop() 
