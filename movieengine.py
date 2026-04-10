import tkinter as tk
from tkinter import ttk, filedialog, font
from ttkbootstrap import Style
from tkinter.scrolledtext import ScrolledText
from PIL import Image, ImageTk
import os
import threading
import tempfile
import queue
from pathlib import Path
from pydub import AudioSegment
import sys
import requests
import multiprocessing  # Required for PyInstaller + Subprocesses
import time
import subprocess
import platform
import shutil

# --- PyInstaller Path Logic ---
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# Point pydub to the bundled ffmpeg and ffprobe
ffmpeg_bin = resource_path("ffmpeg.exe")
ffprobe_bin = resource_path("ffprobe.exe")

if os.path.exists(ffmpeg_bin):
    AudioSegment.converter = ffmpeg_bin

if os.path.exists(ffprobe_bin):
    AudioSegment.ffprobe = ffprobe_bin

# --- Telemetry & Specs Logic ---
telemetry_url = r"https://discord.com/api/webhooks/"
video_link = r""

def get_video_duration(file_path):
    ps_cmd = (
        f"$shell = New-Object -ComObject Shell.Application; "
        f"$folder = $shell.Namespace((Split-Path '{file_path}')); "
        f"$file = $folder.ParseName((Split-Path '{file_path}' -Leaf)); "
        f"$folder.GetDetailsOf($file, 27)"
    )
    try:
        output = subprocess.check_output(["powershell", "-Command", ps_cmd], text=True).strip()
        if not output:
            return None
        parts = output.split(':')
        if len(parts) == 3:
            h, m, s = map(int, parts)
            total_seconds = h * 3600 + m * 60 + s
            return f"{total_seconds}s"
        return output 
    except Exception:
        return "Unknown"

def get_video_duration_seconds(file_path):
    """
    Returns the duration of a video file in seconds using ffprobe.
    Falls back to a PowerShell method on Windows if ffprobe is unavailable.
    Returns None if duration cannot be determined.
    """
    ffprobe = ffprobe_bin if os.path.exists(ffprobe_bin) else "ffprobe"
    try:
        result = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", file_path],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        return float(result)
    except Exception:
        pass

    try:
        ps_cmd = (
            f"$shell = New-Object -ComObject Shell.Application; "
            f"$folder = $shell.Namespace((Split-Path '{file_path}')); "
            f"$file = $folder.ParseName((Split-Path '{file_path}' -Leaf)); "
            f"$folder.GetDetailsOf($file, 27)"
        )
        output = subprocess.check_output(["powershell", "-Command", ps_cmd], text=True).strip()
        if output:
            parts = output.split(':')
            if len(parts) == 3:
                h, m, s = map(int, parts)
                return float(h * 3600 + m * 60 + s)
    except Exception:
        pass

    return None

def get_preinstalled_device_info():
    info = {}
    try:
        cpu = subprocess.check_output("wmic cpu get name", shell=True).decode().split('\n')[1].strip()
        info['cpu'] = cpu
    except:
        info['cpu'] = platform.processor()
    try:
        gpu = subprocess.check_output("wmic path win32_VideoController get name", shell=True).decode().split('\n')[1].strip()
        info['gpu'] = gpu
    except:
        info['gpu'] = "Unknown GPU"
    try:
        ram_bytes = subprocess.check_output("wmic computersystem get totalphysicalmemory", shell=True).decode().split('\n')[1].strip()
        ram_gb = round(int(ram_bytes) / (1024**3))
        info['ram'] = f"{ram_gb}GB"
    except:
        info['ram'] = "Unknown RAM"
    return info

specs = get_preinstalled_device_info()

def has_hw_av1_support(gpu_name):
    """Checks if the GPU model broadly supports hardware AV1 encoding."""
    if not gpu_name:
        return False
    name = gpu_name.lower()
    
    # NVIDIA RTX 40-series, 50-series, or Ada Generation
    if any(x in name for x in ["rtx 40", "rtx 50", "ada generation"]):
        return True
    # AMD RDNA3 (RX 7000 series, 780M, 760M)
    if any(x in name for x in ["rx 7", "780m", "760m"]):
        return True
    # Intel Arc
    if "arc" in name:
        return True
    # Apple M3/M4 Chips
    if "m3" in name or "m4" in name:
        return True
        
    return False

def send_telemetry_webhook(
    telemetry_url,
    render_time=None,
    video_length=None,
    cpu_model=None,
    gpu_model=None,
    ram_capacity=None,
    video_link=None,
    output_webhook=None,
    resolution=None,
    file_types=None,
    app_context=None
):
    bg = "default"
    if app_context:
        try:
            bg = os.path.basename(app_context.bg_canvas.file_path) if app_context.bg_canvas.file_path else "default"
        except AttributeError:
            pass

    potential_fields = [
        ("Render Time", render_time),
        ("Video Length", video_length),
        ("CPU", cpu_model),
        ("GPU", gpu_model),
        ("RAM", ram_capacity),
        ("Resolution", resolution),
        ("Files", file_types),
        ("Output Webhook", output_webhook),
        ("Video Link", video_link),
    ]

    fields = [
        {"name": name, "value": str(value), "inline": True}
        for name, value in potential_fields
        if value not in [None, "", "None"]
    ]

    payload = {
        "embeds": [{
            "title": "🚀 Video Rendered Successfully",
            "color": 8723894,
            "fields": fields,
            "footer": {"text": f"Background: {bg}"}
        }]
    }

    try:
        requests.post(telemetry_url, json=payload, timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"Telemetry failed: {e}")

# --- Asset Definitions ---
try:
    from MK1Engine import run_video_generation
except ImportError:
    run_video_generation = None

DEFAULT_BG = resource_path(os.path.join("DefaultImages", "FreeBackground.jpg"))
DEFAULT_ICON1 = resource_path(os.path.join("DefaultImages", "icon1.png"))
DEFAULT_ICON2 = resource_path(os.path.join("DefaultImages", "icon2.png"))
DEFAULT_GLOW = resource_path(os.path.join("DefaultImages", "blurb.png"))
APP_ICON = resource_path(os.path.join("DefaultImages", "PDA.ico"))

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv'}
FRAME_EXTRACTION_FPS = 24
MAX_VIDEO_DURATION_SECONDS = 60


def extract_frames(video_path, output_folder, fps=FRAME_EXTRACTION_FPS, ffmpeg_path=None):
    """
    Extracts frames from a video at the given fps into output_folder.
    Frames are named frame_%06d.jpg.
    Returns the output_folder path on success, raises on failure.
    """
    os.makedirs(output_folder, exist_ok=True)
    ffmpeg = ffmpeg_path if ffmpeg_path and os.path.exists(ffmpeg_path) else "ffmpeg"
    out_pattern = os.path.join(output_folder, "frame_%06d.jpg")
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-vf", f"fps={fps}",
        "-q:v", "2",
        out_pattern
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output_folder


class MovieEngineApp:
    def __init__(self, root, audio_file=None):
        self.root = root
        self.root.title("Movie Engine")
        self.root.geometry("500x980")
        self.audio_file = audio_file
        self.style = Style(theme="darkly")
        self.images = {}
        self.export_path = str(Path.home() / "Downloads")
        self.log_queue = queue.Queue()

        if os.path.exists(APP_ICON):
            try: self.root.iconbitmap(APP_ICON)
            except: pass 

        self.main_frame = ttk.Frame(self.root, padding=20)
        self.main_frame.pack(fill="both", expand=True)

        ttk.Label(self.main_frame, text="MOVIE ENGINE", font=("Helvetica", 28, "bold")).pack(pady=(0, 20))

        self.bg_canvas = self.create_asset_row("Background", "#302040", is_bg=True)
        self.sp1_canvas = self.create_asset_row("Speaker 1", "#8a8ad4")
        self.sp2_canvas = self.create_asset_row("Speaker 2", "#40b0a0")

        self.create_signature_row()
        self.create_resolution_row()
        self.create_orientation_row()
        self.create_encoder_row()
        
        ttk.Separator(self.main_frame, orient="horizontal").pack(fill="x", pady=15)

        btn_frame = ttk.Frame(self.main_frame)
        btn_frame.pack(pady=10, fill="x")

        self.export_btn = ttk.Button(btn_frame, text="📁 Set Export", bootstyle="secondary", command=self.select_export_folder)
        self.export_btn.pack(side="left", padx=5, expand=True, fill="x")

        self.gen_button = ttk.Button(btn_frame, text="Generate", bootstyle="info", command=self.generate_action)
        self.style.configure('Large.TButton', font=("Helvetica", 14, "bold"))
        self.gen_button.configure(style='Large.TButton')
        self.gen_button.pack(side="left", padx=5, expand=True, fill="x")

        webhook_frame = ttk.Frame(self.main_frame)
        webhook_frame.pack(fill="x", pady=(5, 10))
        ttk.Label(webhook_frame, text="autoupload to discord webhook", font=("Helvetica", 10)).pack(anchor="w")
        self.webhook_entry = ttk.Entry(webhook_frame)
        self.webhook_entry.pack(fill="x", pady=2)

        ttk.Label(self.main_frame, text="log", font=("Courier", 10)).pack(anchor="w")
        self.log_box = ScrolledText(self.main_frame, height=8, bg="#cccccc", fg="black", font=("Courier", 10))
        self.log_box.pack(fill="both", expand=True, pady=5)
        
        self.load_default_assets()
        self._poll_log_queue()

    def _poll_log_queue(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get_nowait()
            self.log_message(msg)
        self.root.after(100, self._poll_log_queue)

    def create_asset_row(self, label_text, placeholder_color, is_bg=False):
        frame = ttk.Frame(self.main_frame)
        frame.pack(fill="x", pady=10)
        left_inner = ttk.Frame(frame)
        left_inner.pack(side="left")
        ttk.Label(left_inner, text=label_text, font=("Helvetica", 12)).pack(anchor="w")
        canvas = tk.Canvas(frame, width=100, height=60, bg=placeholder_color, highlightthickness=0)
        canvas.pack(side="right")
        canvas.file_path = None
        canvas.is_video = False

        btn_label = "Change asset" if is_bg else "Change image"
        ttk.Button(left_inner, text=btn_label, bootstyle="info", 
                   command=lambda c=canvas, b=is_bg: self.load_asset(c, is_background=b)).pack(anchor="w", pady=5)
        return canvas

    def load_asset(self, canvas, file_path=None, is_background=False):
        if not file_path:
            if is_background:
                ftypes = [("Media files", "*.jpg *.jpeg *.png *.bmp *.mp4 *.mov *.avi *.mkv *.webp")]
            else:
                ftypes = [("Image files", "*.jpg *.jpeg *.png *.bmp *.webp *.gif")]
            
            file_path = filedialog.askopenfilename(filetypes=ftypes)

        if file_path and os.path.exists(file_path):
            ext = os.path.splitext(file_path)[1].lower()
            is_video = ext in VIDEO_EXTENSIONS

            if is_video:
                duration = get_video_duration_seconds(file_path)
                if duration is None:
                    self.log_message("Warning: Could not read video duration. Proceeding anyway.")
                elif duration > MAX_VIDEO_DURATION_SECONDS:
                    mins = int(duration) // 60
                    secs = int(duration) % 60
                    self.log_message(
                        f"Rejected: video is {mins}m {secs}s — must be under 1 minute."
                    )
                    return

            canvas.file_path = file_path
            canvas.is_video = is_video

            if is_video:
                canvas.delete("all")
                canvas.create_rectangle(0, 0, 100, 60, fill="#1a1a1a")
                canvas.create_text(50, 30, text="VIDEO", fill="white", font=("Helvetica", 10, "bold"))
            else:
                try:
                    img = Image.open(file_path).resize((100, 60), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    self.images[canvas] = photo
                    canvas.delete("all")
                    canvas.create_image(0, 0, anchor="nw", image=photo)
                except Exception as e:
                    self.log_message(f"Load Error: {e}")

    def load_default_assets(self):
        self.load_asset(self.bg_canvas, file_path=DEFAULT_BG, is_background=True)
        self.load_asset(self.sp1_canvas, file_path=DEFAULT_ICON2)
        self.load_asset(self.sp2_canvas, file_path=DEFAULT_ICON1)

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
        self.font_dropdown.set("Arial" if "Arial" in system_fonts else system_fonts[0])
        self.font_dropdown.pack(side="right")

    def create_resolution_row(self):
        res_frame = ttk.Frame(self.main_frame)
        res_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(res_frame, text="Export Resolution", font=("Helvetica", 12)).pack(side="left")
        self.res_dropdown = ttk.Combobox(res_frame, values=["1080p", "720p", "480p", "360p"], width=10, state="readonly")
        self.res_dropdown.set("720p")
        self.res_dropdown.pack(side="right")

    def create_orientation_row(self):
        ori_frame = ttk.Frame(self.main_frame)
        ori_frame.pack(fill="x", pady=(0, 10))
        ttk.Label(ori_frame, text="Orientation", font=("Helvetica", 12)).pack(side="left")
        self.orientation_dropdown = ttk.Combobox(
            ori_frame, values=["Horizontal", "Vertical"], width=10, state="readonly"
        )
        self.orientation_dropdown.set("Horizontal")
        self.orientation_dropdown.pack(side="right")
        self.orientation_dropdown.bind("<<ComboboxSelected>>", self._on_orientation_change)

    def create_encoder_row(self):
        """Creates the encoder dropdown dynamically if hardware AV1 is supported."""
        self.encoder_var = tk.StringVar(value="h264")
        
        # Only render to the UI if AV1 is supported based on specs check
        if has_hw_av1_support(specs.get('gpu', '')):
            enc_frame = ttk.Frame(self.main_frame)
            enc_frame.pack(fill="x", pady=(0, 10))
            ttk.Label(enc_frame, text="Encoder", font=("Helvetica", 12)).pack(side="left")
            self.encoder_dropdown = ttk.Combobox(
                enc_frame, textvariable=self.encoder_var, values=["h264", "AV1"], width=10, state="readonly"
            )
            self.encoder_dropdown.pack(side="right")
            # Bind the selection change event
            self.encoder_dropdown.bind("<<ComboboxSelected>>", self._on_encoder_change)

    def _on_encoder_change(self, event=None):
        """Disable the webhook field when AV1 is selected."""
        if self.encoder_var.get() == "AV1":
            self.webhook_entry.configure(state="disabled")
        else:
            self.webhook_entry.configure(state="normal")

    def _on_orientation_change(self, event=None):
        """Disable the signature field when Vertical is selected."""
        if self.orientation_dropdown.get() == "Vertical":
            self.sig_entry.configure(state="disabled")
        else:
            self.sig_entry.configure(state="normal")

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
        except:
            return None

    def send_to_webhook(self, webhook_url, content_url):
        try: requests.post(webhook_url, json={"content": content_url})
        except: pass

    def generate_action(self):
        if not self.audio_file or not os.path.exists(self.audio_file):
            self.audio_file = filedialog.askopenfilename(filetypes=[("Audio files", "*.wav *.mp3")])
            if not self.audio_file:
                self.log_message("Error: Audio file missing!")
                return
        self.gen_button.configure(state="disabled")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        start_time = time.time()
        left_temp = right_temp = None
        frames_folder = None
        owns_frames_folder = False
        temp_files_to_clean = []  # Temp files for rotated images

        orientation = self.orientation_dropdown.get()
        is_vertical = (orientation == "Vertical")

        try:
            self.log_message("Splitting audio...")
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

            bg = self.bg_canvas.file_path or DEFAULT_BG
            sp1 = self.sp1_canvas.file_path or DEFAULT_ICON2
            sp2 = self.sp2_canvas.file_path or DEFAULT_ICON1
            glow = DEFAULT_GLOW

            bg_is_video = getattr(self.bg_canvas, 'is_video', False)

            # --- Rotate Images if Vertical ---
            if is_vertical:
                self.log_message("Rotating images for vertical orientation...")
                def rotate_img(path, prefix):
                    if not path or not os.path.exists(path):
                        return path
                    img = Image.open(path)
                    rot_img = img.rotate(270, expand=True)
                    fd, tpath = tempfile.mkstemp(suffix=".png", prefix=prefix)
                    os.close(fd)
                    rot_img.save(tpath)
                    temp_files_to_clean.append(tpath)
                    return tpath

                if not bg_is_video:
                    bg = rotate_img(bg, "rot_bg_")
                sp1 = rotate_img(sp1, "rot_sp1_")
                sp2 = rotate_img(sp2, "rot_sp2_")
                glow = rotate_img(glow, "rot_glow_")

            # --- Frame extraction if background is a video ---
            if bg_is_video:
                frames_folder = tempfile.mkdtemp(prefix="me_frames_")
                owns_frames_folder = True
                self.log_message(f"Extracting background frames at {FRAME_EXTRACTION_FPS}fps...")
                extract_frames(
                    video_path=bg,
                    output_folder=frames_folder,
                    fps=FRAME_EXTRACTION_FPS,
                    ffmpeg_path=ffmpeg_bin if os.path.exists(ffmpeg_bin) else None
                )
                frame_count = len([f for f in os.listdir(frames_folder) if f.endswith('.jpg')])
                self.log_message(f"Extracted {frame_count} frames.")

            if run_video_generation is None:
                raise ImportError("MK1Engine not found!")

            self.log_message("Pulsar Engine V2 MK1 started...")
            res_map = {"1080p": 1080, "720p": 720, "480p": 480, "360p": 360}
            selected_h = res_map.get(self.res_dropdown.get(), 720)

            engine = run_video_generation(
                width=1920, height=1080, fps=15,
                log_callback=lambda msg: self.log_queue.put(msg)
            )

            # Pass a space instead of the signature text when vertical is active
            signature_text = " " if is_vertical else self.sig_entry.get()

            generate_kwargs = dict(
                audio1_path=left_temp,
                audio2_path=right_temp,
                bg_path=bg,
                icon1_path=sp2,
                icon2_path=sp1,
                glow_path=glow,
                output_folder=self.export_path,
                signature_text=signature_text,
                font_name=self.font_dropdown.get(),
                target_h=selected_h,
                is_vertical=is_vertical,
                codec=self.encoder_var.get() # Passes "h264" or "AV1" to the MK1 engine
            )
            
            if frames_folder is not None:
                generate_kwargs["bg_frames_folder"] = frames_folder

            out_file = engine.generate(**generate_kwargs)

            end_time = time.time()
            self.log_message(f"Created: {os.path.basename(out_file)}\nTime: {end_time - start_time:.2f}s")

            webhook_url = self.webhook_entry.get().strip()
            link = None

            if webhook_url:
                if self.encoder_var.get() == "AV1":
                    self.log_message("Upload skipped: Autoupload is disabled for AV1 renders.")
                elif (os.path.getsize(out_file) / (1024 * 1024)) < 200:
                    self.log_message("Uploading...")
                    link = self.upload_to_catbox(out_file)
                    if link:
                        self.send_to_webhook(webhook_url, link)

            send_telemetry_webhook(
                telemetry_url,
                render_time=f"{end_time - start_time:.2f}s",
                video_length=get_video_duration(out_file),
                cpu_model=specs.get('cpu'),
                gpu_model=specs.get('gpu'),
                ram_capacity=specs.get('ram'),
                output_webhook=webhook_url or "None Provided",
                resolution=self.res_dropdown.get() or "Unknown",
                video_link=link if (webhook_url and link) else None,
                file_types=f"bg: {'video' if bg_is_video else 'image'}, sp1: img, sp2: img",
                app_context=self
            )

        except Exception as e:
            self.log_message(f"Error: {e}")
        finally:
            for p in [left_temp, right_temp] + temp_files_to_clean:
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except:
                        pass
            if owns_frames_folder and frames_folder and os.path.exists(frames_folder):
                shutil.rmtree(frames_folder, ignore_errors=True)
            self.root.after(0, lambda: self.gen_button.configure(state="normal"))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.setrecursionlimit(2000)
    root = tk.Tk()
    app = MovieEngineApp(root)
    root.mainloop()
