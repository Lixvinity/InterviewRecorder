import asyncio
import os
import sys
import subprocess
import numpy as np
import math
import time
import multiprocessing
from playwright.async_api import async_playwright
from pydub import AudioSegment
from scipy.ndimage import gaussian_filter1d

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

# --- CRITICAL: Link to your pw-browsers folder ---
# This ensures Playwright looks in your project folder instead of AppData
BROWSER_STORAGE = resource_path("pw-browsers")
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = BROWSER_STORAGE

class run_video_generation:
    def __init__(self, width=1920, height=1080, fps=24):
        self.width = width
        self.height = height
        self.fps = fps
        
        # Look for ffmpeg.exe in the root of the bundle
        self.ffmpeg_exe = resource_path("ffmpeg.exe")
        
        total_cores = multiprocessing.cpu_count()
        self.cores = max(1, int(total_cores * 0.35))
        
        self.codec = self._detect_best_codec()
        
        print(f"🛠 Hardware Profile: Using {self.cores}/{total_cores} cores")
        print(f"🚀 Encoder Selected: {self.codec}")
        print(f"📂 FFmpeg Path: {self.ffmpeg_exe}")
        print(f"🌐 Expected Browser Storage: {BROWSER_STORAGE}")

    def _detect_best_codec(self):
        try:
            nv_check = subprocess.run(
                [self.ffmpeg_exe, "-encoders"], 
                capture_output=True, 
                text=True, 
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            if "h264_nvenc" in nv_check.stdout:
                return "h264_nvenc"
            if "h264_amf" in nv_check.stdout:
                return "h264_amf"
        except Exception:
            pass
        return "libx264"

    def ensure_playwright_installed(self):
        print("🔍 Checking browser dependencies...")
        
        # Check if the folder exists and isn't empty
        if os.path.exists(BROWSER_STORAGE) and os.listdir(BROWSER_STORAGE):
            print(f"✅ Local browsers folder detected.")
            return

        try:
            from playwright._impl._driver import compute_driver_executable, get_driver_env
            
            env = get_driver_env()
            env["PLAYWRIGHT_BROWSERS_PATH"] = BROWSER_STORAGE
            
            driver_executable, driver_cli = compute_driver_executable()
            
            print("📥 Downloading Chromium to local project folder...")
            subprocess.run(
                [str(driver_executable), str(driver_cli), "install", "chromium"], 
                env=env,
                check=True, 
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            print("✅ Browser dependencies ready.")
        except Exception as e:
            print(f"⚠️ Playwright check/install failed: {e}")

    # --- Added target_h here as requested ---
    def generate(self, audio1_path, audio2_path, bg_path, icon1_path, icon2_path,
                 glow_path, output_folder, signature_text, font_name, target_h=720):
        
        self.ensure_playwright_installed()
        out_name = f"render_{int(time.time())}.mp4"
        final_output = os.path.join(output_folder, out_name)

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        return asyncio.run(self._async_generate(
            audio1_path, audio2_path, bg_path, icon1_path, icon2_path,
            final_output, signature_text, font_name, target_h
        ))

    async def _async_generate(self, a1, a2, bg, i1, i2, out_path, signature, font, target_h):
        print("📊 Analyzing audio channels...")
        s2 = AudioSegment.from_file(a1)
        s1 = AudioSegment.from_file(a2)
        total_f = int((max(len(s1), len(s2)) / 1000.0) * self.fps)
        
        def get_v(seg):
            ms = 1000 / self.fps
            v = np.zeros(total_f)
            limit = min(total_f, int(len(seg)/ms))
            v[:limit] = [seg[int(i*ms):int((i+1)*ms)].rms for i in range(limit)]
            if v.max() > 0: v /= v.max()
            return gaussian_filter1d(v, sigma=1.2)

        v1, v2 = get_v(s1), get_v(s2)

        paths = {
            'html': resource_path(os.path.join("DefaultImages", "movie.html")),
            'bg_img': bg,
            'h_img': i1,
            'g_img': i2
        }

        chunk_len = math.ceil(total_f / self.cores)
        tasks = []
        for i in range(self.cores):
            start = i * chunk_len
            end = min((i+1)*chunk_len, total_f)
            if start < end:
                tasks.append(self.render_chunk(
                    i, start, end, v1, v2, paths, signature, font, target_h
                ))

        print(f"🔥 Rendering chunks on {len(tasks)} workers...")
        chunks_data = [None] * len(tasks)
        for task in asyncio.as_completed(tasks):
            res = await task
            if res and res[1]:
                chunks_data[res[0]] = res[1]

        valid_chunks = [c for c in chunks_data if c is not None]
        
        parts_file = f"parts_{int(time.time())}.txt"
        with open(parts_file, "w", encoding='utf-8') as f:
            for c in valid_chunks:
                f.write(f"file '{os.path.abspath(c).replace('\\', '/')}'\n")

        subprocess.run([
            self.ffmpeg_exe, '-y', '-f', 'concat', '-safe', '0', '-i', parts_file,
            '-i', a1, '-i', a2,
            '-filter_complex', '[1:a][2:a]amix=inputs=2:duration=longest[aout]',
            '-map', '0:v', '-map', '[aout]', '-c:v', 'copy', '-c:a', 'aac', out_path
        ], stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

        for f in valid_chunks + [parts_file]:
            if os.path.exists(f): os.remove(f)

        print(f"✅ Video saved to: {out_path}")
        return out_path

    async def render_chunk(self, chunk_id, start_f, end_f, vol1, vol2, paths, sig_text, font_name, target_h):
        chunk_name = f"part_{chunk_id}.mp4"
        SENSITIVITY, MIN_OUTLINE, MAX_OUTLINE = 1.5, 0, 40
        SCALE_AMOUNT = 0.04
        range_val = MAX_OUTLINE - MIN_OUTLINE

        # Dynamic Bitrate Calculation (Roughly 4Mbps for 1080p, 2Mbps for 720p, etc.)
        # This ensures file size drops with resolution.
        bitrate = f"{int((target_h / 1080) * 4000)}k"

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                
                if chunk_id == 0:
                    print(f"🧪 [TEST] Browser Executable: {p.chromium.executable_path}")
                
                context = await browser.new_page(viewport={'width': self.width, 'height': self.height})
                
                html_url = f"file:///{os.path.abspath(paths['html']).replace('\\', '/')}"
                await context.goto(html_url)

                bg_p = f"file:///{os.path.abspath(paths['bg_img']).replace('\\', '/')}"
                h_p = f"file:///{os.path.abspath(paths['h_img']).replace('\\', '/')}"
                g_p = f"file:///{os.path.abspath(paths['g_img']).replace('\\', '/')}"

                await context.evaluate(f"""() => {{
                    document.querySelector('.background-img').src = '{bg_p}';
                    const s = document.querySelectorAll('.speaker');
                    if(s[0]) s[0].src = '{h_p}';
                    if(s[1]) s[1].src = '{g_p}';
                    window.speakers = s;
                    const sig = document.querySelector('#signature');
                    if(sig) {{
                        sig.innerText = "{sig_text}";
                        sig.style.fontFamily = "{font_name}";
                    }}
                }}""")

                await context.add_style_tag(content=f"""
                    .speaker {{
                        outline-width: calc({MIN_OUTLINE}px + (var(--pulse, 0) * {SENSITIVITY} * {range_val}px)) !important;
                        transform: scale(calc(1 + (var(--pulse, 0) * {SCALE_AMOUNT}))) !important;
                        outline-style: solid !important;
                        outline-color: rgba(70, 220, 70, clamp(0, (var(--pulse) - 0.02) * 100, 1)) !important;
                        transition: transform 0.04s linear, outline-width 0.04s linear;
                    }}
                """)

                # Base command with scaling
                cmd = [
                    self.ffmpeg_exe, '-y', '-f', 'image2pipe', '-vcodec', 'mjpeg', '-r', str(self.fps),
                    '-i', '-', '-vf', f'scale=-2:{target_h}', '-c:v', self.codec
                ]
                
                # Encoder-specific optimization for file size
                if "nvenc" in self.codec:
                    # Use Constant Quantization for NVENC
                    cmd += ['-rc', 'vbr', '-cq', '28', '-b:v', bitrate, '-maxrate', bitrate, '-preset', 'p1', '-pix_fmt', 'yuv420p']
                elif "amf" in self.codec:
                    cmd += ['-b:v', bitrate, '-pix_fmt', 'yuv420p']
                else:
                    # Use CRF for libx264 (Lower file size, good quality)
                    # 23 is default, 28 is smaller/lower quality, 18 is larger/higher quality
                    cmd += ['-crf', '26', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-b:v', bitrate]

                cmd.append(chunk_name)
                
                proc = subprocess.Popen(
                    cmd, 
                    stdin=subprocess.PIPE, 
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )

                for i in range(start_f, end_f):
                    v1_val, v2_val = float(vol1[i]), float(vol2[i])
                    v1_val = v1_val if v1_val > 0.005 else 0
                    v2_val = v2_val if v2_val > 0.005 else 0

                    await context.evaluate(f"""() => {{
                        if(window.speakers[0]) window.speakers[0].style.setProperty('--pulse', {v1_val});
                        if(window.speakers[1]) window.speakers[1].style.setProperty('--pulse', {v2_val});
                    }}""")
                    
                    frame = await context.screenshot(type='jpeg', quality=85)
                    proc.stdin.write(frame)

                proc.stdin.close()
                proc.wait()
                await browser.close()
                return chunk_id, chunk_name
        except Exception as e:
            print(f"Worker {chunk_id} error: {e}")
            return chunk_id, None
