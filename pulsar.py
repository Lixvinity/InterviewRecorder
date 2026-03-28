import numpy as np
import random
import subprocess
import os
import platform
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import gaussian_filter1d
from moviepy import ImageClip, AudioFileClip, CompositeVideoClip, VideoClip, clips_array, CompositeAudioClip

class VideoGenerator:
    def __init__(self, width=1920, height=1080, target_h=540, fps=15):
        self.width = width
        self.height = height
        self.target_h = target_h
        self.output_fps = fps
        self.canvas_fps = 30
        self.icon_size = 400

        # TUNING PARAMETERS
        self.pulse_max = 0.5 
        self.sigma = 2

        self.codec, self.q_flag = self._get_gpu_config()

    def _get_gpu_config(self):
        system = platform.system()
        try:
            if system == "Windows":
                cmd = "powershell -command \"Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name\""
                gpu_info = subprocess.check_output(cmd, shell=True).decode().upper()
            elif system == "Linux":
                gpu_info = subprocess.check_output("lspci | grep VGA", shell=True).decode().upper()
            elif system == "Darwin":
                return "h264_videotoolbox", "-q:v"
            else:
                gpu_info = ""

            if "NVIDIA" in gpu_info: return "h264_nvenc", "-cq"
            if "AMD" in gpu_info or "RADEON" in gpu_info: return "h264_amf", "-qp_cb"
            if "INTEL" in gpu_info: return "h264_qsv", "-global_quality"
        except:
            pass
        return "libx264", "-crf"

    def _render_text_to_array(self, text, font_name, size=60):
        img = Image.new("RGBA", (self.width, 200), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        try:
            font_path = font_name + ".ttf" if not font_name.endswith(".ttf") else font_name
            font = ImageFont.truetype(font_path, size)
        except:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((self.width - w) / 2, (200 - h) / 2), text, font=font, fill="white", stroke_width=2, stroke_fill="black")
        return np.array(img)

    def _process_icon_to_array(self, input_path):
        with Image.open(input_path).convert("RGBA") as img:
            min_dim = min(img.size)
            left, top = (img.width - min_dim) / 2, (img.height - min_dim) / 2
            img = img.crop((left, top, left + min_dim, top + min_dim))
            img = img.resize((self.icon_size, self.icon_size), Image.Resampling.LANCZOS)
            mask = Image.new('L', img.size, 0)
            ImageDraw.Draw(mask).ellipse((0, 0, img.width, img.height), fill=255)
            img.putalpha(mask)
            return np.array(img)

    def _get_volume_map(self, audio_clip):
        """Analyzes a single audio clip and returns a smoothed volume array."""
        print(f"🔊 Analyzing Audio: {audio_clip.filename}")
        times = np.arange(0, audio_clip.duration, 1 / self.canvas_fps)
        volumes = np.array([np.abs(audio_clip.get_frame(t)).max() for t in times])
        
        max_v = volumes.max() if volumes.max() > 0 else 1
        return gaussian_filter1d(volumes / max_v, sigma=self.sigma)

    def generate(self, audio1_path, audio2_path, bg_path, icon1_path, icon2_path, glow_path, output_folder, signature_text="SIGNATURE", font_name="arial"):
        os.makedirs(output_folder, exist_ok=True)

        # Process Assets
        icon1_arr = self._process_icon_to_array(icon1_path)
        icon2_arr = self._process_icon_to_array(icon2_path)
        glow_raw  = Image.open(glow_path).convert("RGBA")

        # Load Audio Files
        audio1 = AudioFileClip(audio1_path)
        audio2 = AudioFileClip(audio2_path)
        
        # Use the longer duration for the video
        max_duration = max(audio1.duration, audio2.duration)
        
        # Build independent volume maps
        vol1 = self._get_volume_map(audio1)
        vol2 = self._get_volume_map(audio2)

        c_left  = (self.width * 0.28, self.height * 0.58)
        c_right = (self.width * 0.70, self.height * 0.58)
        max_glow_canvas = int(self.icon_size * (1 + self.pulse_max + 0.1))

        def make_glow_clip(vol_map, center, duration):
            def frame_fn(t):
                idx = min(int(t * self.canvas_fps), len(vol_map) - 1)
                scale = 1 + (vol_map[idx] * self.pulse_max)
                current_size = int(self.icon_size * scale)

                container = Image.new("RGBA", (max_glow_canvas, max_glow_canvas), (0, 0, 0, 0))
                glow_resized = glow_raw.resize((current_size, current_size), Image.Resampling.BILINEAR)
                offset = (max_glow_canvas - current_size) // 2
                container.paste(glow_resized, (offset, offset), glow_resized)
                return np.array(container)

            pos_x = center[0] - max_glow_canvas / 2
            pos_y = center[1] - max_glow_canvas / 2
            return VideoClip(frame_fn, duration=duration).with_position((pos_x, pos_y))

        # Create pulsing glows
        glow1 = make_glow_clip(vol1, c_left, audio1.duration)
        glow2 = make_glow_clip(vol2, c_right, audio2.duration)

        # Background and Static Elements
        bg = ImageClip(bg_path).with_duration(max_duration).resized(width=self.width)
        icon1 = ImageClip(icon1_arr).with_duration(max_duration).with_position(
            (c_left[0]  - self.icon_size / 2, c_left[1]  - self.icon_size / 2))
        icon2 = ImageClip(icon2_arr).with_duration(max_duration).with_position(
            (c_right[0] - self.icon_size / 2, c_right[1] - self.icon_size / 2))

        txt_arr  = self._render_text_to_array(signature_text, font_name)
        txt_clip = ImageClip(txt_arr).with_duration(max_duration).with_position(('center', self.height * 0.82))

        # Combine Audio
        mixed_audio = CompositeAudioClip([audio1, audio2])

        # Composite Video
        video = CompositeVideoClip(
            [bg, glow1, glow2, icon1, icon2, txt_clip],
            size=(self.width, self.height)
        ).with_audio(mixed_audio).with_duration(max_duration)

        output_path = os.path.join(output_folder, f"out_{random.randint(1000, 9999)}.mp4")
        video.resized(height=self.target_h).write_videofile(
            output_path, fps=self.output_fps, codec=self.codec,
            ffmpeg_params=[self.q_flag, "24", "-pix_fmt", "yuv420p"]
        )

        audio1.close()
        audio2.close()
        video.close()
        return output_path