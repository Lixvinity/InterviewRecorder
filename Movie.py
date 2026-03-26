import argparse
import numpy as np
import random
import onnxruntime as ort
import subprocess
import os
from PIL import Image, ImageOps, ImageDraw
from scipy.ndimage import gaussian_filter1d
from moviepy import (
    ImageClip, 
    AudioFileClip, 
    CompositeVideoClip, 
    CompositeAudioClip
)

# --- CONFIG ---
CANVAS_FPS = 30      
OUTPUT_FPS = 15      
SIGMA = 2            
ICON_SIZE = 400      
PULSE_MAX = 0.5      
WIDTH, HEIGHT = 1920, 1080  
TARGET_H = 540       
QUALITY_FACTOR = "26"

# --- CENTERS ---
CENTER_LEFT = (530, 630)
CENTER_RIGHT = (1330, 630)

def process_icon(input_path, output_name, size=(1080, 1080)):
    """Converts any image into a 1080x1080 circular transparent PNG."""
    print(f"🎨 Processing Icon: {input_path} -> {output_name}")
    img = Image.open(input_path).convert("RGBA")
    
    # Square center crop
    w, h = img.size
    min_dim = min(w, h)
    left = (w - min_dim) / 2
    top = (h - min_dim) / 2
    img = img.crop((left, top, left + min_dim, top + min_dim))
    
    # Resize to 1080x1080
    img = img.resize(size, Image.Resampling.LANCZOS)
    
    # Create circular mask
    mask = Image.new('L', size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0) + size, fill=255)
    
    # Apply transparency
    img.putalpha(mask)
    img.save(output_name, "PNG")
    return output_name

def get_gpu_config():
    providers = ort.get_available_providers()
    if 'DmlExecutionProvider' in providers:
        try:
            gpu_info = subprocess.check_output("wmic path win32_VideoController get name", shell=True).decode().upper()
            if "NVIDIA" in gpu_info: return "h264_nvenc", "-cq"
            if "AMD" in gpu_info or "RADEON" in gpu_info: return "h264_amf", "-qp_cb"
            if "INTEL" in gpu_info: return "h264_qsv", "-global_quality"
        except: pass
        return "h264_amf", "-qp_cb" 
    return "libx264", "-crf"

def build_smooth_volume(audio, fps=CANVAS_FPS):
    print(f"🔊 Analyzing Audio for Pulse: {audio.filename}")
    times = np.arange(0, audio.duration, 1 / fps)
    volumes = np.array([np.abs(audio.get_frame(t)).max() for t in times])
    max_v = volumes.max() if volumes.max() > 0 else 1
    return gaussian_filter1d(volumes / max_v, sigma=SIGMA)

def get_transform(t, vol_array, center):
    idx = min(int(t * CANVAS_FPS), len(vol_array) - 1)
    scale = 1 + (vol_array[idx] * PULSE_MAX)
    curr_size = ICON_SIZE * scale
    pos = (center[0] - (curr_size / 2), center[1] - (curr_size / 2))
    return {"pos": pos, "scale": scale}

def create_video():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mic", required=True)
    parser.add_argument("--desktop", required=True)
    parser.add_argument("--bg", required=True)
    parser.add_argument("--icon1", required=True)
    parser.add_argument("--icon2", required=True)
    args = parser.parse_args()
    MAX_THREADS = os.cpu_count() or 1
    # --- PRE-PROCESS ICONS ---
    icon1_processed = process_icon(args.icon1, "temp_icon1_circle.png")
    icon2_processed = process_icon(args.icon2, "temp_icon2_circle.png")

    gpu_codec, q_flag = get_gpu_config()

    print("🚀 Loading Assets & Swapping Tracks...")
    # Icon 1 (Left) reacts to Desktop | Icon 2 (Right) reacts to Mic
    aud_left_source = AudioFileClip(args.desktop)
    aud_right_source = AudioFileClip(args.mic)
    
    final_audio = CompositeAudioClip([aud_left_source, aud_right_source])
    duration = final_audio.duration

    left_vols = build_smooth_volume(aud_left_source)
    right_vols = build_smooth_volume(aud_right_source)

    bg = ImageClip(args.bg).with_duration(duration).resized(width=WIDTH)

    # GLOWS (Pulsing backlights)
    glow1 = (ImageClip("DefaultImages/blurb.png").with_duration(duration)
             .resized(height=ICON_SIZE)
             .with_position(lambda t: get_transform(t, left_vols, CENTER_LEFT)["pos"])
             .resized(lambda t: get_transform(t, left_vols, CENTER_LEFT)["scale"]))

    glow2 = (ImageClip("DefaultImages/blurb.png").with_duration(duration)
             .resized(height=ICON_SIZE)
             .with_position(lambda t: get_transform(t, right_vols, CENTER_RIGHT)["pos"])
             .resized(lambda t: get_transform(t, right_vols, CENTER_RIGHT)["scale"]))

    # ICONS (Pre-processed circular PNGs)
    icon1 = (ImageClip(icon1_processed).with_duration(duration).resized(height=ICON_SIZE)
             .with_position((CENTER_LEFT[0] - ICON_SIZE/2, CENTER_LEFT[1] - ICON_SIZE/2)))
    
    icon2 = (ImageClip(icon2_processed).with_duration(duration).resized(height=ICON_SIZE)
             .with_position((CENTER_RIGHT[0] - ICON_SIZE/2, CENTER_RIGHT[1] - ICON_SIZE/2)))

    # Master Composite
    full_video = CompositeVideoClip([bg, glow1, glow2, icon1, icon2], size=(WIDTH, HEIGHT)).with_audio(final_audio)
    final_output = full_video.resized(height=TARGET_H)

    print(f"🎬 Exporting: {TARGET_H}p @ {OUTPUT_FPS}fps | GPU: {gpu_codec}")

    final_output.write_videofile(
        f"interview_{random.randint(10000, 999999)}.mp4",
        fps=OUTPUT_FPS,
        codec=gpu_codec,
        audio_codec="aac",
        audio_bitrate="96k", 
        ffmpeg_params=[
            "-ac", "1", 
            q_flag, QUALITY_FACTOR, 
            "-pix_fmt", "yuv420p",
            "-threads", str(MAX_THREADS) # <--- Also tell FFmpeg explicitly
        ]
    )

    # Cleanup temp files
    try:
        os.remove(icon1_processed)
        os.remove(icon2_processed)
    except: pass

if __name__ == "__main__":
    create_video()