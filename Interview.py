import pyaudiowpatch as pyaudio
import numpy as np
import soundfile as sf
import threading
import subprocess
import os
import sys
import tkinter as tk
from datetime import datetime
from queue import Queue

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import filedialog, scrolledtext

class DualTrackMaster:
    def __init__(self, root):
        self.root = root
        self.root.title("Interview Recorder & Process Dashboard - PDA Studio")
        self.root.geometry("650x850") # Height for meters + buttons + logs
        self.root.iconbitmap("DefaultImages/PDA.ico")
        
        self.p = pyaudio.PyAudio()
        self.recording = False
        self.can_save_frames = False 
        self.log_queue = Queue()
        
        # Discovery
        self.all_devs = [self.p.get_device_info_by_index(i) for i in range(self.p.get_device_count())]
        self.desktop_list, self.mic_list = self.get_filtered_devices()
        
        # --- Settings Variables ---
        self.project_dir = tk.StringVar(value=os.getcwd())
        self.do_normalize = tk.BooleanVar(value=False)
        self.do_denoise = tk.BooleanVar(value=False)
        self.process_mode = tk.StringVar(value="Process both tracks")
        
        # Visual Assets Variables
        self.enable_visuals = tk.BooleanVar(value=False)
        self.img_speaker1 = tk.StringVar(value="")
        self.img_speaker2 = tk.StringVar(value="")
        self.img_bg = tk.StringVar(value="")

        self.selected_desk_idx = 0
        self.selected_mic_idx = 0

        # Meters Interpolation
        self.desk_target, self.mic_target = 0.0, 0.0
        self.desk_current, self.mic_current = 0.0, 0.0
        
        self.setup_ui()
        self.animate_meters()
        self.check_logs()
        self.log("System Ready. Check Settings to verify Audio/Image paths.")

    def log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {message}\n")

    def check_logs(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get()
            self.log_box.configure(state='normal')
            self.log_box.insert(tk.END, msg)
            self.log_box.see(tk.END)
            self.log_box.configure(state='disabled')
        self.root.after(100, self.check_logs)

    def get_filtered_devices(self):
        desktops, mics = [], []
        for dev in self.all_devs:
            if dev['maxInputChannels'] > 0:
                name = dev['name'].lower()
                if dev.get('isLoopbackDevice') or "loopback" in name:
                    desktops.append(dev)
                elif not dev.get('isLoopbackDevice'):
                    mics.append(dev)
        return desktops, mics

    def animate_meters(self):
        decay = 0.15 
        self.desk_current += (self.desk_target - self.desk_current) * decay
        self.mic_current += (self.mic_target - self.mic_current) * decay
        self.desk_meter.configure(value=self.desk_current * 100)
        self.mic_meter.configure(value=self.mic_current * 100)
        self.desk_target *= 0.8; self.mic_target *= 0.8
        self.root.after(20, self.animate_meters)

    def setup_ui(self):
        # Header
        header = tb.Frame(self.root, bootstyle=SECONDARY); header.pack(fill=X, padx=10, pady=10)
        tb.Label(header, text="STUDIO DASHBOARD", font=("Helvetica", 11, "bold")).pack(side=LEFT, padx=10)
        self.settings_btn = tb.Button(header, text="⚙ SETTINGS", bootstyle=OUTLINE, command=self.open_settings)
        self.settings_btn.pack(side=RIGHT, padx=10)

        # Meters
        main = tb.Frame(self.root, padding=20); main.pack(fill=X)
        self.desk_meter = tb.Progressbar(main, bootstyle=(INFO, STRIPED), length=500, mode=DETERMINATE)
        tb.Label(main, text="DESKTOP AUDIO").pack(anchor=W); self.desk_meter.pack(pady=(5, 10))
        
        self.mic_meter = tb.Progressbar(main, bootstyle=(SUCCESS, STRIPED), length=500, mode=DETERMINATE)
        tb.Label(main, text="MICROPHONE").pack(anchor=W); self.mic_meter.pack(pady=(5, 15))

        # Controls
        self.btn_toggle = tb.Button(main, text="START RECORDING", bootstyle=DANGER, width=25, command=self.toggle)
        self.btn_toggle.pack(pady=5)
        self.status = tb.Label(main, text="Ready", font=("Helvetica", 10, "italic")); self.status.pack(pady=5)

        # Logs
        log_frame = tb.Frame(self.root, padding=10)
        log_frame.pack(fill=BOTH, expand=True)
        tb.Label(log_frame, text="PIPELINE LOGS", font=("Helvetica", 8, "bold"), bootstyle="MUTED").pack(anchor=W)
        self.log_box = scrolledtext.ScrolledText(log_frame, height=10, font=("Consolas", 9), bg="#1a1a1a", fg="#00ff00", state='disabled')
        self.log_box.pack(fill=BOTH, expand=True, pady=5)

    def open_settings(self):
        win = tb.Toplevel(self.root); win.title("Global Settings"); win.geometry("580x700"); win.grab_set()
        container = tb.Frame(win, padding=20); container.pack(fill=BOTH, expand=True)
        
        # Audio Section
        tb.Label(container, text="Audio Hardware", font=("Helvetica", 10, "bold")).pack(anchor=W)
        desk_cb = tb.Combobox(container, values=[d['name'] for d in self.desktop_list], state="readonly")
        desk_cb.current(self.selected_desk_idx); desk_cb.pack(fill=X, pady=5)
        mic_cb = tb.Combobox(container, values=[m['name'] for m in self.mic_list], state="readonly")
        mic_cb.current(self.selected_mic_idx); mic_cb.pack(fill=X, pady=5)
        
        tb.Separator(container).pack(pady=15)
        
        # Post-Processing
        tb.Label(container, text="Post-Process Routine", font=("Helvetica", 10, "bold")).pack(anchor=W)
        mode_cb = tb.Combobox(container, textvariable=self.process_mode, state="readonly", 
                              values=["Process both tracks", "Process mic audio only", "Process desktop audio only"])
        mode_cb.pack(fill=X, pady=10)
        tb.Checkbutton(container, text="Normalize Audio", variable=self.do_normalize, bootstyle="round-toggle").pack(anchor=W, pady=2)
        tb.Checkbutton(container, text="Denoise Audio", variable=self.do_denoise, bootstyle="round-toggle").pack(anchor=W, pady=2)

        tb.Separator(container).pack(pady=15)

        # Visual Assets Section
        tb.Label(container, text="Visual Assets", font=("Helvetica", 10, "bold")).pack(anchor=W)
        tb.Checkbutton(container, text="Enable Video Generation Handoff", 
                       variable=self.enable_visuals, bootstyle="round-toggle",
                       command=lambda: self.refresh_asset_buttons(btn1, btn2, btn3)).pack(anchor=W, pady=10)
        
        asset_frame = tb.Frame(container)
        asset_frame.pack(fill=X)

        btn1 = tb.Button(asset_frame, text="[Speaker 1 Icon]", bootstyle=SECONDARY, width=25, command=lambda: self.pick_img(self.img_speaker2))
        btn1.pack(pady=5)
        btn2 = tb.Button(asset_frame, text="[Speaker 2 Icon]", bootstyle=SECONDARY, width=25, command=lambda: self.pick_img(self.img_speaker1))
        btn2.pack(pady=5)
        btn3 = tb.Button(asset_frame, text="[Background Image]", bootstyle=SECONDARY, width=25, command=lambda: self.pick_img(self.img_bg))
        btn3.pack(pady=5)

        self.refresh_asset_buttons(btn1, btn2, btn3)
        
        def save():
            self.selected_desk_idx, self.selected_mic_idx = desk_cb.current(), mic_cb.current()
            self.log("Settings saved and updated.")
            win.destroy()
        tb.Button(container, text="SAVE & CLOSE", bootstyle=SUCCESS, command=save).pack(pady=30)

    def pick_img(self, var):
        path = filedialog.askopenfilename(filetypes=[("Image files", "*.jpg *.png *.jpeg")])
        if path: 
            var.set(path)
            self.log(f"Asset selected: {os.path.basename(path)}")

    def refresh_asset_buttons(self, b1, b2, b3):
        state = NORMAL if self.enable_visuals.get() else DISABLED
        b1.config(state=state); b2.config(state=state); b3.config(state=state)

    def toggle(self):
        if not self.recording:
            self.recording = True
            self.can_save_frames = False
            self.desktop_frames, self.mic_frames = [], []
            self.btn_toggle.config(text="STOP RECORDING", bootstyle=WARNING)
            self.settings_btn.config(state=DISABLED)
            self.log("Recording session started...")
            
            threading.Thread(target=self._play_silent_heartbeat, daemon=True).start()
            threading.Thread(target=self._record_stream, args=(self.desktop_list[self.selected_desk_idx], self.desktop_frames, True), daemon=True).start()
            threading.Thread(target=self._record_stream, args=(self.mic_list[self.selected_mic_idx], self.mic_frames, False), daemon=True).start()
            self.root.after(400, self.fire_start)
        else:
            self.recording = False
            self.can_save_frames = False
            self.status.config(text="Processing Pipeline...", foreground="orange")
            self.log("Recording stopped. Running Save Task.")
            self.btn_toggle.config(state=DISABLED)
            threading.Thread(target=self.save_task, daemon=True).start()

    def fire_start(self):
        self.can_save_frames = True
        self.status.config(text="● RECORDING SYNCED", foreground="red")

    def _record_stream(self, dev_info, storage_list, is_desktop=True):
        try:
            rate, ch = int(dev_info['defaultSampleRate']), dev_info['maxInputChannels']
            stream = self.p.open(format=pyaudio.paFloat32, channels=ch, rate=rate, input=True, input_device_index=dev_info['index'])
            if is_desktop: self.d_rate, self.d_ch = rate, ch
            else: self.m_rate, self.m_ch = rate, ch
            while self.recording:
                data = stream.read(1024, exception_on_overflow=False)
                if self.can_save_frames:
                    chunk = np.frombuffer(data, dtype=np.float32)
                    storage_list.append(chunk)
                    peak = np.max(np.abs(chunk)) if chunk.size > 0 else 0
                    if is_desktop: self.desk_target = peak
                    else: self.mic_target = peak
            stream.stop_stream(); stream.close()
        except Exception as e:
            self.log(f"Recording Error: {e}")

    def save_task(self):
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        d_p = os.path.normpath(os.path.join(self.project_dir.get(), f"desktop_{ts}.wav"))
        m_p = os.path.normpath(os.path.join(self.project_dir.get(), f"mic_{ts}.wav"))
        
        self._process_save_mono(self.desktop_frames, self.d_ch, self.d_rate, d_p)
        self._process_save_mono(self.mic_frames, self.m_ch, self.m_rate, m_p)
        self.log(f"Files saved: {os.path.basename(m_p)} and {os.path.basename(d_p)}")
        
        # 1. Post-Process
        if self.do_normalize.get() or self.do_denoise.get():
            pp_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PostProcess.py")
            if os.path.exists(pp_script):
                self.log("Executing PostProcess.py...")
                cmd = [sys.executable, pp_script, m_p, d_p]
                if self.do_denoise.get(): cmd.append("--denoise")
                if self.do_normalize.get(): cmd.append("--normalize")
                mode = self.process_mode.get()
                if "mic" in mode: cmd.append("--skip2")
                elif "desktop" in mode: cmd.append("--skip1")
                subprocess.run(cmd)
                self.log("Post-processing finished.")

        # 2. Movie.py Logic
        if self.enable_visuals.get():
            movie_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Movie.py")
            if os.path.exists(movie_script):
                self.log("Sending files to Movie.py for video generation...")
                v_cmd = [
                    sys.executable, movie_script,
                    "--mic", m_p,
                    "--desktop", d_p,
                    "--bg", self.img_bg.get() or "DefaultImages/FreeBackground.jpg",
                    "--icon1", self.img_speaker1.get() or "DefaultImages/icon1.png",
                    "--icon2", self.img_speaker2.get() or "DefaultImages/icon2.png"
                ]
                subprocess.Popen(v_cmd)
            self.log("Video generation started. This WILL 1000% take a while depending on the length of the recording. PLEASE BE PATIENT.")

        self.root.after(0, self.finalize_ui)

    def _process_save_mono(self, frames, ch, rate, filename):
        if not frames: return
        data = np.concatenate(frames)
        data = data[:len(data)//ch*ch].reshape(-1, ch)
        if ch > 1: data = np.mean(data, axis=1)
        sf.write(filename, data, rate)

    def finalize_ui(self):
        self.status.config(text="Studio Pipeline Finished.", foreground="green")
        self.btn_toggle.config(text="START RECORDING", bootstyle=DANGER, state=NORMAL)
        self.settings_btn.config(state=NORMAL)

    def _play_silent_heartbeat(self):
        try:
            stream = self.p.open(format=pyaudio.paFloat32, channels=1, rate=44100, output=True)
            silence = np.zeros(1024, dtype=np.float32).tobytes()
            while self.recording: stream.write(silence)
            stream.stop_stream(); stream.close()
        except: pass

if __name__ == "__main__":
    root = tb.Window(themename="darkly")
    app = DualTrackMaster(root)
    root.mainloop()