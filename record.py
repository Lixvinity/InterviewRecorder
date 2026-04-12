import tkinter as tk
from tkinter import messagebox
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
import shutil
import queue
import requests
import multiprocessing
from tkinter.simpledialog import askstring
import scipy.signal as sps
from concurrent.futures import ProcessPoolExecutor

# --- PyInstaller Path Helper ---
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# FIX (🟠): Only define deep_filter_exe once, at module level.
# The original code redundantly redefined it inside process_audio_files.
deep_filter_exe = resource_path("deep-filter.exe")

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
is_processing = False
log_queue = queue.Queue()

# FIX (🔴): Queue for dispatching GUI calls from background threads to the
# main thread. Tkinter is not thread-safe, so messagebox must never be called
# directly from a daemon thread.
gui_call_queue = queue.Queue()

mic_frames = []
desktop_frames = []

hardware_mic_rate = 44100
hardware_desktop_rate = 44100
hardware_desktop_channels = 2

desk_target, mic_target = 0.0, 0.0
desk_current, mic_current = 0.0, 0.0

def run_denoise_task(task_info):
    """Worker to denoise a single full file."""
    target_file, exe, out_dir, flags = task_info
    subprocess.run(
        [str(exe), str(target_file), '--output-dir', str(out_dir)],
        check=True, capture_output=True, creationflags=flags
    )
    return Path(out_dir) / Path(target_file).name

# --- Thread-safe GUI dialog helper ---
def ask_retry_from_thread(filename):
    """
    FIX (🔴): Safe replacement for calling messagebox directly from a background
    thread. Posts the dialog request to gui_call_queue so the main thread
    executes it, then blocks until the user responds.
    """
    result_event = threading.Event()
    result_holder = [None]

    def show_dialog():
        result_holder[0] = messagebox.askretrycancel(
            "File in Use",
            f"Close VLC/Media Players and click 'Retry' to finish saving {filename}."
        )
        result_event.set()

    gui_call_queue.put(show_dialog)
    result_event.wait()
    return result_holder[0]

def process_gui_calls(root_widget):
    """Drain any pending GUI calls posted by background threads."""
    while not gui_call_queue.empty():
        fn = gui_call_queue.get()
        fn()
    root_widget.after(100, lambda: process_gui_calls(root_widget))

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
        # FIX (🟡): Block a new recording from starting while a previous
        # processing job is still running, preventing concurrent collisions.
        if is_processing:
            log_message("Please wait — previous recording is still being processed.", WARNING)
            return

        mic_frames, desktop_frames = [], []
        is_recording = True
        record_btn.configure(text="STOP RECORDING", bootstyle=DANGER)
        log_message("Recording LIVE...", WARNING)
    else:
        is_recording = False

        m_frames_copy = list(mic_frames)
        d_frames_copy = list(desktop_frames)
        mic_frames, desktop_frames = [], []

        # FIX (🔴): Guard against empty buffers before spawning the processing
        # thread. np.concatenate([]) raises ValueError on an empty sequence.
        if not m_frames_copy or not d_frames_copy:
            log_message("Recording was too short — no audio captured.", WARNING)
            record_btn.configure(text="Start Recording", bootstyle=SUCCESS, state=NORMAL)
            return

        # FIX (🟡): Disable the button while processing rather than re-enabling
        # it immediately. It is re-enabled inside process_audio_files → finally.
        record_btn.configure(text="Start Recording", bootstyle=SUCCESS, state=DISABLED)
        log_message("Stopped. Initializing background processing job...", INFO)

        threading.Thread(
            target=process_audio_files,
            args=(m_frames_copy, d_frames_copy, record_btn),
            daemon=True
        ).start()


def process_audio_files(m_frames, d_frames, record_btn=None):
    global is_processing
    is_processing = True
    to_cleanup = []

    def get_actual_dn_file(directory, base_path):
        stem = base_path.stem
        for f in directory.iterdir():
            if f.name.startswith(stem) and f.suffix.lower() == '.wav':
                return f
        return directory / base_path.name

    try:
        ts = f"{time.strftime('%Y%m%d-%H%M%S')}"
        ffmpeg_exe = resource_path("ffmpeg.exe")
        # Uses the module-level deep_filter_exe — no local redefinition needed.
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

        temp_dir = recordings_folder / "temp"
        temp_dir.mkdir(exist_ok=True)
        dn_dir = recordings_folder / "denoised"
        dn_dir.mkdir(exist_ok=True)

        # 1. SAVE ORIGINAL STEREO MP3
        log_message("Step 1: Saving original stereo MP3...", INFO)
        raw_mic  = temp_dir / f"raw_mic_{ts}.wav"
        raw_desk = temp_dir / f"raw_desk_{ts}.wav"
        sf.write(raw_mic,  np.concatenate(m_frames), hardware_mic_rate)
        sf.write(raw_desk, np.concatenate(d_frames),  hardware_desktop_rate)
        to_cleanup.extend([raw_mic, raw_desk])

        original_mp3 = recordings_folder / f"INTERVIEW_{ts}.mp3"
        stereo_cmd = [
            ffmpeg_exe, '-y', '-i', str(raw_mic), '-i', str(raw_desk),
            '-filter_complex', '[0:a][1:a]join=inputs=2:channel_layout=stereo[out]',
            '-map', '[out]', '-b:a', '192k', str(original_mp3)
        ]
        subprocess.run(stereo_cmd, check=True, capture_output=True, creationflags=creation_flags)

        # 2. COPY AND SPLIT CHANNELS
        log_message("Step 2: Copying track and splitting channels...", INFO)
        work_mp3  = temp_dir / f"work_copy_{ts}.mp3"
        work_mic  = temp_dir / f"work_mic_{ts}.wav"
        work_desk = temp_dir / f"work_desk_{ts}.wav"
        shutil.copy2(original_mp3, work_mp3)
        to_cleanup.append(work_mp3)

        split_cmd = [
            ffmpeg_exe, '-y', '-i', str(work_mp3),
            '-filter_complex', '[0:a]pan=mono|c0=FL[left];[0:a]pan=mono|c0=FR[right]',
            '-map', '[left]',  str(work_mic),
            '-map', '[right]', str(work_desk)
        ]
        subprocess.run(split_cmd, check=True, capture_output=True, creationflags=creation_flags)
        to_cleanup.extend([work_mic, work_desk])

        # 3. DENOISE
        log_message("Step 3: Processing (DeepFilter)...", WARNING)
        with ProcessPoolExecutor(max_workers=2) as executor:
            task_args = [
                (work_mic,  deep_filter_exe, dn_dir, creation_flags),
                (work_desk, deep_filter_exe, dn_dir, creation_flags)
            ]
            list(executor.map(run_denoise_task, task_args))

        actual_dn_mic  = get_actual_dn_file(dn_dir, work_mic)
        actual_dn_desk = get_actual_dn_file(dn_dir, work_desk)
        to_cleanup.extend([actual_dn_mic, actual_dn_desk])

        # 4. MERGE BACK TO MP3
        log_message("Step 4: Merging processed audio back to stereo MP3...", INFO)
        processed_tmp_mp3 = temp_dir / f"PROCESSED_{ts}.mp3"
        # FIX (🟡): Add to cleanup list now so it is removed even if the user
        # cancels the retry dialog, preventing orphaned temp files.
        to_cleanup.append(processed_tmp_mp3)

        merge_cmd = [
            ffmpeg_exe, '-y', '-i', str(actual_dn_mic), '-i', str(actual_dn_desk),
            '-filter_complex', '[0:a][1:a]join=inputs=2:channel_layout=stereo[out]',
            '-map', '[out]', '-b:a', '192k', str(processed_tmp_mp3)
        ]
        subprocess.run(merge_cmd, check=True, capture_output=True, creationflags=creation_flags)

        # 5. REPLACE ORIGINAL
        log_message("Step 5: Replacing original file...", INFO)
        while True:
            try:
                if processed_tmp_mp3.exists():
                    shutil.move(str(processed_tmp_mp3), str(original_mp3))
                    # FIX (🟡): Success log and break are now inside the `if`
                    # block — they only run when the move actually happened.
                    log_message(f"Success! {original_mp3.name} is ready.", SUCCESS)
                    break
                else:
                    # Processed file is missing — something silently failed upstream.
                    log_message("Processed file missing. Original MP3 retained.", DANGER)
                    break
            except PermissionError:
                # FIX (🔴): Use the thread-safe helper instead of calling
                # messagebox directly from this background thread.
                retry = ask_retry_from_thread(original_mp3.name)
                if not retry:
                    log_message("Save cancelled. Original unprocessed MP3 retained.", DANGER)
                    break

    except Exception as e:
        log_message(f"Critical Error: {e}", DANGER)
        log_message("Error occurred. Original MP3 kept for safety.", WARNING)

    finally:
        for f in to_cleanup:
            try:
                Path(f).unlink(missing_ok=True)
            except Exception:
                pass
        is_processing = False
        # FIX (🟡): Re-enable the record button once processing is fully done.
        if record_btn is not None:
            record_btn.configure(state=NORMAL)
        log_message("Ready.", SUCCESS)

# --- Audio Engine Setup ---
def restart_desktop_meter(dropdown, d_prog):
    global desktop_pa, desktop_stream, hardware_desktop_rate, hardware_desktop_channels
    try:
        if desktop_stream:
            desktop_stream.stop_stream()
            desktop_stream.close()
        if not desktop_pa:
            desktop_pa = pyaudio.PyAudio()
        selection = dropdown.get()
        if not selection:
            return
        idx = int(selection.split(':')[0])
        info = desktop_pa.get_device_info_by_index(idx)
        hardware_desktop_rate     = int(info['defaultSampleRate'])
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
            mic_stream.stop()
            mic_stream.close()
        selection = dropdown.get()
        if not selection:
            return
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
    wasapi_idx = next(
        (i for i in range(pa.get_host_api_count())
         if "WASAPI" in pa.get_host_api_info_by_index(i)['name']), 0
    )
    for i in range(pa.get_device_count()):
        dev = pa.get_device_info_by_index(i)
        if dev['hostApi'] != wasapi_idx:
            continue
        name = f"{i}: {dev['name']}"
        if dev.get('isLoopbackDevice') or "loopback" in dev['name'].lower():
            o_devs.append(name)
        elif dev['maxInputChannels'] > 0:
            i_devs.append(name)
    pa.terminate()
    root_widget.after(0, lambda: finalize_ui(i_devs, o_devs, mic_drop, desk_drop, d_prog, m_prog))

def finalize_ui(i, o, mic_drop, desk_drop, d_prog, m_prog):
    mic_drop['values']  = i
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

def on_closing(root_widget):
    global is_processing, desktop_pa, desktop_stream, mic_stream
    if is_processing:
        confirm = messagebox.askyesno(
            "Processing in Progress",
            "Audio processing is currently running in the background. "
            "Closing now might corrupt the final file and leave background processes running.\n\n"
            "Are you sure you want to exit immediately?"
        )
        if not confirm:
            return

    # FIX (🟡): Properly terminate the PyAudio instance and close all streams
    # before exiting to release WASAPI handles cleanly.
    try:
        if desktop_stream:
            desktop_stream.stop_stream()
            desktop_stream.close()
        if desktop_pa:
            desktop_pa.terminate()
    except Exception:
        pass

    try:
        if mic_stream:
            mic_stream.stop()
            mic_stream.close()
    except Exception:
        pass

    root_widget.destroy()
    os._exit(0)

# --- Main Entry Point ---
def main():
    multiprocessing.freeze_support()

    root = ttk.Window(themename="darkly")
    root.title("Podcast Assistant Dashboard")
    root.geometry("700x550")
    root.protocol("WM_DELETE_WINDOW", lambda: on_closing(root))

    icon_path = resource_path(os.path.join("DefaultImages", "PDA.ico"))
    if os.path.exists(icon_path):
        try:
            root.iconbitmap(icon_path)
        except Exception:
            pass

    ttk.Label(root, text="INTERVIEW DASHBOARD", font=("Helvetica", 18, "bold")).pack(pady=20)

    ctrl_frame = ttk.Frame(root, padding=20)
    ctrl_frame.pack(fill=X)
    # FIX (⚪): Give both columns weight so the comboboxes also expand
    # horizontally with the window, not just the progressbars.
    ctrl_frame.columnconfigure(0, weight=1)
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

    fetch_remote_txt()
    threading.Thread(target=get_devices, args=(root, mic_dropdown, desktop_dropdown, DesktopAudioProg, MicProgressProg), daemon=True).start()
    animate_meters(root, DesktopAudioProg, MicProgressProg)
    process_log_queue(status_log_widget, root)
    # FIX (🔴): Start the GUI call dispatcher so thread-safe dialogs work.
    process_gui_calls(root)

    root.mainloop()

if __name__ == "__main__":
    main()
