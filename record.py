import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText
from pathlib import Path
import time
import threading
import numpy as np
import sounddevice as sd
import pyaudiowpatch as pyaudio
import soundfile as sf
import os
import subprocess
import queue
import requests  # Added for remote fetching
from tkinter.simpledialog import askstring # Added for URL input

# --- Configuration ---
home = Path.home()
documents = home / "Documents"
recordings_folder = documents / "recordings"
recordings_folder.mkdir(parents=True, exist_ok=True)

LOG_STYLES = {
    INFO: "#539BF5",
    SUCCESS: "#28a745",
    WARNING: "#ffc107",
    DANGER: "#dc3545"
}

# --- Globals ---
mic_stream = None
desktop_pa = None
desktop_stream = None
is_recording = False
log_queue = queue.Queue()

# Buffers
mic_frames = []
desktop_frames = []

# CRITICAL: These store the ACTUAL hardware rates and channels detected
hardware_mic_rate = 44100
hardware_desktop_rate = 44100
hardware_desktop_channels = 2  # Default, will be updated by hardware

# Meter Animation Globals
desk_target, mic_target = 0.0, 0.0
desk_current, mic_current = 0.0, 0.0

# --- UI Helper Functions ---
def log_message(message, style="INFO"):
    log_queue.put((message, style))

def process_log_queue():
    while not log_queue.empty():
        msg, style = log_queue.get()
        status_log.text.config(state="normal")
        status_log.text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n", style)
        status_log.text.see("end")
        status_log.text.config(state="disabled")
    root.after(100, process_log_queue)

def calculate_volume(indata):
    if len(indata) == 0: return 0
    peak = np.max(np.abs(indata))
    return min(peak * 100, 100)

def animate_meters():
    global desk_current, mic_current, desk_target, mic_target
    decay = 0.15 
    desk_current += (desk_target - desk_current) * decay
    mic_current += (mic_target - mic_current) * decay
    DesktopAudio.configure(value=desk_current)
    MicProgress.configure(value=mic_current)
    desk_target *= 0.8 # Natural bleed-off
    mic_target *= 0.8
    root.after(20, animate_meters)

# --- New Feature: Remote Text Fetcher ---
def fetch_remote_txt():
    url = (r"https://raw.githubusercontent.com/Lixvinity/InterviewRecorder/refs/heads/main/news.txt")
    if not url:
        return

    def download_task():
        try:
            log_message(f"Attempting to fetch latest info", INFO)
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            content = response.text
            log_message(content, INFO)
        except Exception as e:
            log_message(f"Fetch failed: {e}", DANGER)

    threading.Thread(target=download_task, daemon=True).start()

# --- Audio Callbacks ---
def mic_callback(indata, frames, time_info, status):
    global mic_target, mic_frames
    mic_target = calculate_volume(indata)
    if is_recording:
        mic_frames.append(indata.copy())

def desktop_callback(in_data, frame_count, time_info, status):
    global desk_target, desktop_frames
    audio_data = np.frombuffer(in_data, dtype=np.float32).reshape(-1, hardware_desktop_channels)
    desk_target = calculate_volume(audio_data)
    if is_recording:
        desktop_frames.append(audio_data.copy())
    return (in_data, pyaudio.paContinue)

# --- Recording & Processing Logic ---
def toggle_recording():
    global is_recording, mic_frames, desktop_frames
    if not is_recording:
        mic_frames, desktop_frames = [], []
        is_recording = True
        record_button.configure(text="STOP RECORDING", bootstyle=DANGER)
        log_message("Recording LIVE...", WARNING)
    else:
        is_recording = False
        record_button.configure(text="PROCESSING...", state=DISABLED)
        log_message("Stopping. Syncing rates and channels...", INFO)
        threading.Thread(target=process_audio_files, daemon=True).start()

def process_audio_files():
    try:
        ts = time.strftime("%Y%m%d-%H%M%S")
        if not mic_frames or not desktop_frames:
            log_message("Export Failed: One of the buffers is empty.", DANGER)
            return

        m_data = np.concatenate(mic_frames)
        d_data = np.concatenate(desktop_frames)

        mic_temp = recordings_folder / f"temp_m_{ts}.wav"
        desk_temp = recordings_folder / f"temp_d_{ts}.wav"
        final_mp3 = recordings_folder / f"INTERVIEW_{ts}.mp3"

        log_message(f"Writing temp files (Mic: {hardware_mic_rate}Hz, Desk: {hardware_desktop_rate}Hz [{hardware_desktop_channels}Ch])", INFO)
        sf.write(mic_temp, m_data, hardware_mic_rate)
        sf.write(desk_temp, d_data, hardware_desktop_rate)

        merge_cmd = [
            'ffmpeg', '-y',
            '-i', str(mic_temp),
            '-i', str(desk_temp),
            '-filter_complex', 
            "[0:a]aresample=44100:async=1,pan=mono|c0=c0[l]; "
            "[1:a]aresample=44100:async=1,pan=mono|c0=c0[r]; "
            "[l][r]join=inputs=2:channel_layout=stereo[out]",
            '-map', '[out]', '-b:a', '192k', str(final_mp3)
        ]
        
        subprocess.run(merge_cmd, check=True, capture_output=True)
        log_message(f"Success! Saved: {final_mp3.name}", SUCCESS)
        mic_temp.unlink(); desk_temp.unlink()

    except Exception as e:
        log_message(f"Critical Process Error: {e}", DANGER)
    finally:
        root.after(0, lambda: record_button.configure(text="Start Recording", bootstyle=SUCCESS, state=NORMAL))

# --- Audio Engine Setup ---
def restart_desktop_meter(event=None):
    global desktop_pa, desktop_stream, hardware_desktop_rate, hardware_desktop_channels
    try:
        if desktop_stream:
            desktop_stream.stop_stream(); desktop_stream.close()
        if not desktop_pa: desktop_pa = pyaudio.PyAudio()
        selection = desktopaudio_dropdown.get()
        if not selection: return
        idx = int(selection.split(':')[0])
        info = desktop_pa.get_device_info_by_index(idx)
        hardware_desktop_rate = int(info['defaultSampleRate'])
        hardware_desktop_channels = int(info['maxInputChannels'])
        desktop_stream = desktop_pa.open(
            format=pyaudio.paFloat32,
            channels=hardware_desktop_channels,
            rate=hardware_desktop_rate,
            input=True,
            input_device_index=idx,
            stream_callback=desktop_callback
        )
        desktop_stream.start_stream()
        log_message(f"Desktop linked at {hardware_desktop_rate}Hz, {hardware_desktop_channels}Ch", INFO)
    except Exception as e:
        log_message(f"Desktop Init Error: {e}", DANGER)

def restart_mic_meter(event=None):
    global mic_stream, hardware_mic_rate
    try:
        if mic_stream:
            mic_stream.stop(); mic_stream.close()
        selection = mic_dropdown.get()
        if not selection: return
        idx = int(selection.split(':')[0])
        info = sd.query_devices(idx)
        hardware_mic_rate = int(info['default_samplerate'])
        mic_stream = sd.InputStream(
            device=idx,
            samplerate=hardware_mic_rate,
            channels=1,
            callback=mic_callback
        )
        mic_stream.start()
        log_message(f"Mic linked at {hardware_mic_rate}Hz", INFO)
    except Exception as e:
        log_message(f"Mic Init Error: {e}", WARNING)

def get_devices():
    pa = pyaudio.PyAudio()
    i_devs, o_devs = [], []
    wasapi_idx = next((i for i in range(pa.get_host_api_count()) if "WASAPI" in pa.get_host_api_info_by_index(i)['name']), 0)
    for i in range(pa.get_device_count()):
        dev = pa.get_device_info_by_index(i)
        if dev['hostApi'] != wasapi_idx: continue
        name = f"{i}: {dev['name']}"
        if dev.get('isLoopbackDevice') or "loopback" in dev['name'].lower():
            o_devs.append(name)
        elif dev['maxInputChannels'] > 0:
            i_devs.append(name)
    pa.terminate()
    root.after(0, lambda: finalize_ui(i_devs, o_devs))

def finalize_ui(i, o):
    mic_dropdown['values'] = i
    desktopaudio_dropdown['values'] = o
    if i: mic_dropdown.set(i[0]); restart_mic_meter()
    if o: desktopaudio_dropdown.set(o[0]); restart_desktop_meter()

def launch_media_explorer():
    log_message("Launching custom Media Explorer...", INFO)
    try:
        import MediaExplorer
        MediaExplorer.MediaExplorer(root, str(recordings_folder))
    except ImportError:
        log_message("Error: MediaExplorer.py not found.", DANGER)
    except Exception as e:
        log_message(f"Launch Error: {e}", DANGER)

# --- UI Setup ---
root = ttk.Window(themename="darkly")
root.title("Interview Recorder Pro")
root.geometry("700x550")

# Header
ttk.Label(root, text="INTERVIEW DASHBOARD", font=("Helvetica", 18, "bold")).pack(pady=20)

# Controls
ctrl_frame = ttk.Frame(root, padding=20)
ctrl_frame.pack(fill=X)
ctrl_frame.columnconfigure(1, weight=1)

# Desktop Row
ttk.Label(ctrl_frame, text="Desktop Audio").grid(row=0, column=0, sticky="w")
desktopaudio_dropdown = ttk.Combobox(ctrl_frame, state="readonly")
desktopaudio_dropdown.grid(row=1, column=0, padx=(0, 20), pady=(0, 15), sticky="ew")
desktopaudio_dropdown.bind("<<ComboboxSelected>>", restart_desktop_meter)
DesktopAudio = ttk.Progressbar(ctrl_frame, bootstyle=INFO)
DesktopAudio.grid(row=1, column=1, sticky="ew", pady=(0, 15))

# Mic Row
ttk.Label(ctrl_frame, text="Microphone").grid(row=2, column=0, sticky="w")
mic_dropdown = ttk.Combobox(ctrl_frame, state="readonly")
mic_dropdown.grid(row=3, column=0, padx=(0, 20), pady=(0, 15), sticky="ew")
mic_dropdown.bind("<<ComboboxSelected>>", restart_mic_meter)
MicProgress = ttk.Progressbar(ctrl_frame, bootstyle=SUCCESS)
MicProgress.grid(row=3, column=1, sticky="ew", pady=(0, 15))

# Buttons
btn_frame = ttk.Frame(root, padding=20)
btn_frame.pack(fill=X)
ttk.Button(btn_frame, text="📂 Browse Media", command=launch_media_explorer, bootstyle=OUTLINE).pack(side=LEFT, padx=5)
record_button = ttk.Button(btn_frame, text="Start Recording", command=toggle_recording, bootstyle=SUCCESS)
record_button.pack(side=RIGHT, fill=X, expand=True, padx=5)

# Logs
status_log = ScrolledText(root, height=8, padding=20)
status_log.pack(fill=BOTH, expand=True)
for name, color in LOG_STYLES.items(): status_log.text.tag_configure(name, foreground=color)

fetch_remote_txt()

# Start
threading.Thread(target=get_devices, daemon=True).start()
animate_meters()
process_log_queue()
root.mainloop()
