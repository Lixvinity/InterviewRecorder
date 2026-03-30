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
import sys
import subprocess
import queue
import requests
import multiprocessing  # Required for PyInstaller + Subprocesses
from tkinter.simpledialog import askstring

# --- PyInstaller Path Helper ---
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

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
mic_frames = []
desktop_frames = []

hardware_mic_rate = 44100
hardware_desktop_rate = 44100
hardware_desktop_channels = 2 

desk_target, mic_target = 0.0, 0.0
desk_current, mic_current = 0.0, 0.0

# --- UI Helper Functions ---
def log_message(message, style="INFO"):
    log_queue.put((message, style))

def process_log_queue(status_log_widget, root_widget):
    while not log_queue.empty():
        msg, style = log_queue.get()
        status_log_widget.text.config(state="normal")
        status_log_widget.text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n", style)
        status_log_widget.text.see("end")
        status_log_widget.text.config(state="disabled")
    root_widget.after(100, lambda: process_log_queue(status_log_widget, root_widget))

def calculate_volume(indata):
    if len(indata) == 0: return 0
    peak = np.max(np.abs(indata))
    return min(peak * 100, 100)

def animate_meters(root_widget, d_prog, m_prog):
    global desk_current, mic_current, desk_target, mic_target
    decay = 0.15 
    desk_current += (desk_target - desk_current) * decay
    mic_current += (mic_target - mic_current) * decay
    d_prog.configure(value=desk_current)
    m_prog.configure(value=mic_current)
    desk_target *= 0.8 
    mic_target *= 0.8
    root_widget.after(20, lambda: animate_meters(root_widget, d_prog, m_prog))

def fetch_remote_txt():
    url = "https://raw.githubusercontent.com/Lixvinity/InterviewRecorder/refs/heads/main/news.txt"
    def download_task():
        try:
            log_message("Attempting to fetch latest info", INFO)
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            log_message(response.text, INFO)
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
def toggle_recording(record_btn, root_widget):
    global is_recording, mic_frames, desktop_frames
    if not is_recording:
        mic_frames, desktop_frames = [], []
        is_recording = True
        record_btn.configure(text="STOP RECORDING", bootstyle=DANGER)
        log_message("Recording LIVE...", WARNING)
    else:
        is_recording = False
        record_btn.configure(text="PROCESSING...", state=DISABLED)
        log_message("Stopping. Syncing rates and channels...", INFO)
        threading.Thread(target=process_audio_files, args=(record_btn, root_widget), daemon=True).start()

def process_audio_files(record_btn, root_widget):
    try:
        ts = time.strftime("%Y%m%d-%H%M%S")
        if not mic_frames or not desktop_frames:
            log_message("Export Failed: Buffers empty.", DANGER)
            return

        m_data = np.concatenate(mic_frames)
        d_data = np.concatenate(desktop_frames)

        mic_temp = recordings_folder / f"temp_m_{ts}.wav"
        desk_temp = recordings_folder / f"temp_d_{ts}.wav"
        final_mp3 = recordings_folder / f"INTERVIEW_{ts}.mp3"

        sf.write(mic_temp, m_data, hardware_mic_rate)
        sf.write(desk_temp, d_data, hardware_desktop_rate)

        # CRITICAL: Use the bundled ffmpeg
        ffmpeg_exe = resource_path("ffmpeg.exe")
        
        merge_cmd = [
            ffmpeg_exe, '-y',
            '-i', str(mic_temp),
            '-i', str(desk_temp),
            '-filter_complex', 
            "[0:a]aresample=44100:async=1,pan=mono|c0=c0[l]; "
            "[1:a]aresample=44100:async=1,pan=mono|c0=c0[r]; "
            "[l][r]join=inputs=2:channel_layout=stereo[out]",
            '-map', '[out]', '-b:a', '192k', str(final_mp3)
        ]
        
        # Hide the console window for ffmpeg when running as EXE
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        subprocess.run(merge_cmd, check=True, capture_output=True, creationflags=creation_flags)
        
        log_message(f"Success! Saved: {final_mp3.name}", SUCCESS)
        mic_temp.unlink(); desk_temp.unlink()

    except Exception as e:
        log_message(f"Critical Process Error: {e}", DANGER)
    finally:
        root_widget.after(0, lambda: record_btn.configure(text="Start Recording", bootstyle=SUCCESS, state=NORMAL))

# --- Audio Engine Setup ---
def restart_desktop_meter(dropdown, d_prog):
    global desktop_pa, desktop_stream, hardware_desktop_rate, hardware_desktop_channels
    try:
        if desktop_stream:
            desktop_stream.stop_stream(); desktop_stream.close()
        if not desktop_pa: desktop_pa = pyaudio.PyAudio()
        selection = dropdown.get()
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
        log_message(f"Desktop linked: {hardware_desktop_rate}Hz", INFO)
    except Exception as e:
        log_message(f"Desktop Init Error: {e}", DANGER)

def restart_mic_meter(dropdown, m_prog):
    global mic_stream, hardware_mic_rate
    try:
        if mic_stream:
            mic_stream.stop(); mic_stream.close()
        selection = dropdown.get()
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
        log_message(f"Mic linked: {hardware_mic_rate}Hz", INFO)
    except Exception as e:
        log_message(f"Mic Init Error: {e}", WARNING)

def get_devices(root_widget, mic_drop, desk_drop, d_prog, m_prog):
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
    root_widget.after(0, lambda: finalize_ui(i_devs, o_devs, mic_drop, desk_drop, d_prog, m_prog))

def finalize_ui(i, o, mic_drop, desk_drop, d_prog, m_prog):
    mic_drop['values'] = i
    desk_drop['values'] = o
    if i: 
        mic_drop.set(i[0])
        restart_mic_meter(mic_drop, m_prog)
    if o: 
        desk_drop.set(o[0])
        restart_desktop_meter(desk_drop, d_prog)

def launch_media_explorer(parent_root):
    log_message("Launching custom Media Explorer...", INFO)
    try:
        import MediaExplorer
        MediaExplorer.MediaExplorer(parent_root, str(recordings_folder))
    except Exception as e:
        log_message(f"Launch Error: {e}", DANGER)

# --- Main Entry Point ---
def main():
    # 1. Critical for PyInstaller + Subprocesses
    multiprocessing.freeze_support()
    
    # 2. Setup the Root Window
    root = ttk.Window(themename="darkly")
    root.title("Podcast Assistant Dashboard")
    root.geometry("700x550")

    # Use resource_path for the icon
    icon_path = resource_path(os.path.join("DefaultImages", "PDA.ico"))
    if os.path.exists(icon_path):
        try:
            root.iconbitmap(icon_path)
        except:
            pass

    ttk.Label(root, text="INTERVIEW DASHBOARD", font=("Helvetica", 18, "bold")).pack(pady=20)

    # UI Construction
    ctrl_frame = ttk.Frame(root, padding=20)
    ctrl_frame.pack(fill=X)
    ctrl_frame.columnconfigure(1, weight=1)

    ttk.Label(ctrl_frame, text="Desktop Audio").grid(row=0, column=0, sticky="w")
    desktop_dropdown = ttk.Combobox(ctrl_frame, state="readonly")
    desktop_dropdown.grid(row=1, column=0, padx=(0, 20), pady=(0, 15), sticky="ew")
    
    DesktopAudioProg = ttk.Progressbar(ctrl_frame, bootstyle=INFO)
    DesktopAudioProg.grid(row=1, column=1, sticky="ew", pady=(0, 15))
    desktop_dropdown.bind("<<ComboboxSelected>>", lambda e: restart_desktop_meter(desktop_dropdown, DesktopAudioProg))

    ttk.Label(ctrl_frame, text="Microphone").grid(row=2, column=0, sticky="w")
    mic_dropdown = ttk.Combobox(ctrl_frame, state="readonly")
    mic_dropdown.grid(row=3, column=0, padx=(0, 20), pady=(0, 15), sticky="ew")
    
    MicProgressProg = ttk.Progressbar(ctrl_frame, bootstyle=SUCCESS)
    MicProgressProg.grid(row=3, column=1, sticky="ew", pady=(0, 15))
    mic_dropdown.bind("<<ComboboxSelected>>", lambda e: restart_mic_meter(mic_dropdown, MicProgressProg))

    btn_frame = ttk.Frame(root, padding=20)
    btn_frame.pack(fill=X)
    ttk.Button(btn_frame, text="📂 Browse Media", command=lambda: launch_media_explorer(root), bootstyle=OUTLINE).pack(side=LEFT, padx=5)
    
    record_btn = ttk.Button(btn_frame, text="Start Recording", command=lambda: toggle_recording(record_btn, root), bootstyle=SUCCESS)
    record_btn.pack(side=RIGHT, fill=X, expand=True, padx=5)

    status_log_widget = ScrolledText(root, height=8, padding=20)
    status_log_widget.pack(fill=BOTH, expand=True)
    for name, color in LOG_STYLES.items(): 
        status_log_widget.text.tag_configure(name, foreground=color)

    # Start logic
    fetch_remote_txt()
    threading.Thread(target=get_devices, args=(root, mic_dropdown, desktop_dropdown, DesktopAudioProg, MicProgressProg), daemon=True).start()
    animate_meters(root, DesktopAudioProg, MicProgressProg)
    process_log_queue(status_log_widget, root)
    
    root.mainloop()

if __name__ == "__main__":
    main()
