import asyncio
import os
import sys
import subprocess
import numpy as np
import math
import time
import multiprocessing
import psutil
import platform
from playwright.async_api import async_playwright
from pydub import AudioSegment
from scipy.ndimage import gaussian_filter1d

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

BROWSER_STORAGE = resource_path("pw-browsers")
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = BROWSER_STORAGE
FFMPEG_PATH = resource_path("ffmpeg.exe")
FFPROBE_PATH = resource_path("ffprobe.exe")

AudioSegment.converter = FFMPEG_PATH
AudioSegment.ffprobe = FFPROBE_PATH

project_bin_dir = os.path.dirname(FFMPEG_PATH)
os.environ["PATH"] = project_bin_dir + os.pathsep + os.environ.get("PATH", "")


def _build_frame_index(frames_folder):
    """
    Scans frames_folder for frame_XXXXXX.jpg files and returns them as a
    sorted list of absolute paths.  Returns an empty list if the folder is
    None or contains no matching files.
    """
    if not frames_folder or not os.path.isdir(frames_folder):
        return []
    files = sorted(
        f for f in os.listdir(frames_folder) if f.endswith('.jpg')
    )
    return [os.path.join(frames_folder, f) for f in files]


# GPU encoder candidates, ordered by preference.
# Each entry: (codec, vendor_label, platform_guard)
# platform_guard = None means all platforms; 'darwin' = macOS only, etc.
_GPU_CANDIDATES = [
    ("h264_nvenc",        "NVIDIA NVENC",       None),
    ("h264_amf",          "AMD AMF",            None),
    ("h264_qsv",          "Intel QSV",          None),
    ("h264_videotoolbox", "Apple VideoToolbox", "darwin"),
]


class run_video_generation:
    def __init__(self, width=1920, height=1080, fps=24, log_callback=None):
        self.width = width
        self.height = height
        self.fps = fps
        self.ffmpeg_exe = FFMPEG_PATH
        self.log = log_callback or print

        total_cores = multiprocessing.cpu_count()
        total_ram_gb = psutil.virtual_memory().total / (1024**3)

        if total_ram_gb > 16:
            self.cores = max(1, int(total_cores * 0.40))
        elif total_ram_gb >= 15.5:
            self.cores = max(1, int(total_cores * 0.35))
        elif total_ram_gb >= 7.5:
            self.cores = max(1, int(total_cores * 0.25))
        else:
            self.cores = min(2, total_cores)

        self.codec = self._detect_best_codec()

        self.log(f"📊 System RAM: {total_ram_gb:.2f} GB")
        self.log(f"🛠 Hardware Profile: Using {self.cores}/{total_cores} cores")
        self.log(f"🚀 Encoder Selected: {self.codec}")

    def _detect_best_codec(self):
        """
        1. Query ffmpeg for available encoders.
        2. Walk GPU candidates in priority order, skipping those gated to
           another platform.
        3. Validate each listed candidate with a real 1-frame test encode.
        4. Return the first that passes, or fall back to libx264.
        """
        try:
            result = subprocess.run(
                [self.ffmpeg_exe, "-encoders"],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            available = result.stdout + result.stderr
        except Exception as e:
            self.log(f"⚠️ Could not query FFmpeg encoders: {e}")
            available = ""

        # Log which GPU codecs are actually listed in this ffmpeg build
        found_in_binary = [c for c, _, _ in _GPU_CANDIDATES if c in available]
        if found_in_binary:
            self.log(f"🔎 GPU codecs present in ffmpeg binary: {', '.join(found_in_binary)}")
        else:
            self.log("🔎 No GPU codecs found in ffmpeg binary — binary may lack hardware encoder support")

        current_platform = platform.system().lower()  # 'windows', 'linux', 'darwin'

        for codec, label, platform_guard in _GPU_CANDIDATES:
            # Skip encoders that only work on a specific OS
            if platform_guard and current_platform != platform_guard:
                continue

            if codec not in available:
                self.log(f"⏭ {label} ({codec}): not in ffmpeg binary — skipping")
                continue

            self.log(f"🔍 Found {label} encoder ({codec}) — validating…")
            ok, reason = self._test_encoder(codec)
            if ok:
                self.log(f"✅ {label} hardware encoder confirmed: {codec}")
                return codec
            else:
                self.log(f"⚠️ {label} smoke test FAILED — reason: {reason}")

        self.log("ℹ️ No working hardware encoder found; using CPU encoder: libx264")
        return "libx264"

    def _test_encoder(self, codec: str):
        """
        Validates a codec by encoding a single synthetic frame.
        Upped resolution to 256x256 to avoid 'Invalid Argument' errors 
        caused by hardware minimum size constraints.
        """
        extra = {
            "h264_nvenc":        ["-preset", "p1"],
            "h264_amf":          [], # AMF is very sensitive to extra params
            "h264_qsv":          ["-global_quality", "28"],
            "h264_videotoolbox": ["-q:v", "50"],
        }.get(codec, [])

        cmd = [
            self.ffmpeg_exe, "-y",
            "-f", "lavfi", "-i", "color=black:size=256x256:rate=1", # Increased size
            "-frames:v", "1",
            "-c:v", codec,
            "-pix_fmt", "yuv420p", # Standard format
        ] + extra + ["-f", "null", "-"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            if result.returncode == 0:
                return True, None
            
            stderr_lines = [
                l.strip() for l in
                result.stderr.decode("utf-8", errors="ignore").splitlines()
                if l.strip()
            ]
            # Capture more lines for better debugging
            reason = " | ".join(stderr_lines[-3:]) if stderr_lines else f"exit code {result.returncode}"
            return False, reason
        except subprocess.TimeoutExpired:
            return False, "timed out after 10 s"
        except Exception as e:
            return False, str(e)

    def _codec_is_gpu(self, codec):
        return codec in ("h264_nvenc", "h264_amf", "h264_qsv", "h264_videotoolbox")

    def _build_ffmpeg_cmd(self, codec, target_h, bitrate):
        cmd = [
            self.ffmpeg_exe, '-y',
            '-f', 'image2pipe', '-vcodec', 'mjpeg', '-r', str(self.fps),
            '-i', '-',
            '-vf', f'scale=-2:{target_h}',
            '-c:v', codec
        ]

        if codec == 'h264_nvenc':
            cmd += [
                '-rc', 'vbr', '-cq', '28',
                '-b:v', bitrate, '-maxrate', bitrate,
                '-preset', 'p1',
                '-pix_fmt', 'yuv420p',
            ]
        elif codec == 'h264_amf':
            cmd += [
                '-rc', 'cqp', '-qp_i', '28', '-qp_p', '28',
                '-b:v', bitrate,
                '-pix_fmt', 'yuv420p',
            ]
        elif codec == 'h264_qsv':
            cmd += [
                '-global_quality', '28',
                '-b:v', bitrate,
                '-pix_fmt', 'yuv420p',
            ]
        elif codec == 'h264_videotoolbox':
            cmd += [
                '-q:v', '50',
                '-b:v', bitrate,
                '-pix_fmt', 'yuv420p',
            ]
        else:  # libx264 CPU fallback
            cmd += [
                '-crf', '26', '-preset', 'ultrafast',
                '-pix_fmt', 'yuv420p', '-b:v', bitrate,
            ]

        return cmd

    def ensure_playwright_installed(self):
        if os.path.exists(BROWSER_STORAGE) and os.listdir(BROWSER_STORAGE):
            return
        try:
            from playwright._impl._driver import compute_driver_executable, get_driver_env
            env = get_driver_env()
            env["PLAYWRIGHT_BROWSERS_PATH"] = BROWSER_STORAGE
            driver_executable, driver_cli = compute_driver_executable()
            subprocess.run(
                [str(driver_executable), str(driver_cli), "install", "chromium"],
                env=env, check=True, capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
        except Exception as e:
            self.log(f"⚠️ Playwright check/install failed: {e}")

    def generate(self, audio1_path, audio2_path, bg_path, icon1_path, icon2_path,
                 glow_path, output_folder, signature_text, font_name, target_h=720,
                 bg_frames_folder=None, is_vertical=False):
        """
        bg_frames_folder : str | None
            Path to a folder of pre-extracted JPEG frames (frame_000001.jpg …).
            When provided the background animates through those frames like a
            flipbook.  When None, bg_path is used as a static image.
        is_vertical : bool
            Flag from the previous script to indicate if the final video needs
            to be rotated 90 degrees to the left.
        """
        self.ensure_playwright_installed()
        out_name = f"render_{int(time.time())}.mp4"
        final_output = os.path.join(output_folder, out_name)

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        return asyncio.run(self._async_generate(
            audio1_path, audio2_path, bg_path, icon1_path, icon2_path,
            final_output, signature_text, font_name, target_h, bg_frames_folder, is_vertical
        ))

    async def _async_generate(self, a1, a2, bg, i1, i2, out_path,
                               signature, font, target_h, bg_frames_folder, is_vertical):
        self.log("📊 Analyzing audio channels...")
        s2 = AudioSegment.from_file(a1)
        s1 = AudioSegment.from_file(a2)
        total_f = int((max(len(s1), len(s2)) / 1000.0) * self.fps)

        def get_v(seg):
            ms = 1000 / self.fps
            v = np.zeros(total_f)
            limit = min(total_f, int(len(seg) / ms))
            v[:limit] = [seg[int(i * ms):int((i + 1) * ms)].rms for i in range(limit)]
            if v.max() > 0:
                v /= v.max()
            return gaussian_filter1d(v, sigma=1.2)

        v1, v2 = get_v(s1), get_v(s2)

        # Build the sorted frame list once and share it with every worker.
        # Empty list → static image mode.
        frame_index = _build_frame_index(bg_frames_folder)
        if frame_index:
            self.log(f"🎞 Flipbook mode: {len(frame_index)} frames available.")
        else:
            self.log("🖼 Static background mode.")

        paths = {
            'html': resource_path(os.path.join("DefaultImages", "movie.html")),
            'bg_img': bg,
            'h_img': i1,
            'g_img': i2,
        }

        FRAMES_PER_CHUNK = 120
        total_chunks = math.ceil(total_f / FRAMES_PER_CHUNK)
        failed_queue = asyncio.Queue()

        all_chunks = []
        for c_id in range(total_chunks):
            start = c_id * FRAMES_PER_CHUNK
            end = min((c_id + 1) * FRAMES_PER_CHUNK, total_f)
            all_chunks.append((c_id, start, end))

        workers_assignment = [[] for _ in range(self.cores)]
        for i, chunk_info in enumerate(all_chunks):
            workers_assignment[i % self.cores].append(chunk_info)

        self.log(f"🔥 Rendering {total_chunks} chunks on {self.cores} workers...")
        self.log("💓 Heartbeat: worker tasks launched")

        tasks = []
        for worker_id in range(self.cores):
            tasks.append(self.worker_routine(
                worker_id, workers_assignment[worker_id], v1, v2,
                paths, signature, font, target_h, failed_queue, frame_index
            ))

        worker_results = await asyncio.gather(*tasks)

        chunks_data = []
        for res_list in worker_results:
            chunks_data.extend(res_list)

        # PERSISTENT RESCUE LOOP
        if not failed_queue.empty():
            self.log(f"🔄 {failed_queue.qsize()} chunks were rejected. Rescuing...")
            while not failed_queue.empty():
                c_id, c_start, c_end = await failed_queue.get()
                res = None
                while not res or not res[1]:
                    self.log(f"⚠️ Retrying failed chunk {c_id}...")
                    res = await self.render_chunk(
                        c_id, c_start, c_end, v1, v2,
                        paths, signature, font, target_h, frame_index
                    )
                    if not res or not res[1]:
                        await asyncio.sleep(2)
                chunks_data.append(res)

        # SORTING BY ID TO PREVENT DESYNC
        chunks_data.sort(key=lambda x: x[0])
        valid_chunks = [c[1] for c in chunks_data if c is not None and c[1] is not None]

        # VALIDATION
        if len(valid_chunks) != total_chunks:
            raise RuntimeError(
                f"❌ Desync Prevention: Expected {total_chunks} chunks, got {len(valid_chunks)}."
            )

        parts_file = f"parts_{int(time.time())}.txt"
        with open(parts_file, "w", encoding='utf-8') as f:
            for c in valid_chunks:
                f.write(f"file '{os.path.abspath(c).replace(chr(92), '/')}'\n")

        self.log("🎬 Concatenating chunks...")

        if is_vertical:
            self.log("🔄 Applying 90-degree left rotation for vertical format...")
            # transpose=2 is the FFmpeg filter for 90 degrees counter-clockwise (left)
            filter_complex_str = '[1:a][2:a]amix=inputs=2:duration=longest[aout];[0:v]transpose=2[vout]'
            
            # Since we are applying a video filter, we cannot copy the stream. 
            # Re-calculating bitrate to maintain quality via hardware encode burn-in.
            bitrate = f"{int((target_h / 1080) * 4000 * 1.15)}k"
            
            cmd = [
                self.ffmpeg_exe, '-y', '-f', 'concat', '-safe', '0', '-i', parts_file,
                '-i', a1, '-i', a2,
                '-filter_complex', filter_complex_str,
                '-map', '[vout]', '-map', '[aout]',
                '-c:v', self.codec, '-b:v', bitrate, '-c:a', 'aac', out_path
            ]
        else:
            filter_complex_str = '[1:a][2:a]amix=inputs=2:duration=longest[aout]'
            cmd = [
                self.ffmpeg_exe, '-y', '-f', 'concat', '-safe', '0', '-i', parts_file,
                '-i', a1, '-i', a2,
                '-filter_complex', filter_complex_str,
                '-map', '0:v', '-map', '[aout]', '-c:v', 'copy', '-c:a', 'aac', out_path
            ]

        subprocess.run(cmd, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

        for f in valid_chunks + [parts_file]:
            if os.path.exists(f):
                os.remove(f)

        self.log(f"✅ Video saved to: {out_path}")
        return out_path

    async def worker_routine(self, worker_id, assigned_chunks, vol1, vol2,
                             paths, sig_text, font_name, target_h, failed_queue,
                             frame_index):
        completed_chunks = []
        CHUNK_TIMEOUT = 45

        for idx, (chunk_id, start_f, end_f) in enumerate(assigned_chunks, start=1):
            success = False
            attempts = 0
            while not success and attempts < 3:
                attempts += 1
                try:
                    res = await asyncio.wait_for(
                        self.render_chunk(
                            chunk_id, start_f, end_f, vol1, vol2,
                            paths, sig_text, font_name, target_h, frame_index
                        ),
                        timeout=CHUNK_TIMEOUT
                    )
                    if res and res[1]:
                        completed_chunks.append(res)
                        success = True
                        self.log(f"💓 Worker {worker_id}: completed chunk {idx}/{len(assigned_chunks)}")
                except Exception as e:
                    self.log(f"💔 Worker {worker_id}: chunk {chunk_id} attempt {attempts} failed with error: {e}. retrying...")
                    await asyncio.sleep(2)

            if not success:
                self.log(f"⚠️ Worker {worker_id}: chunk {chunk_id} failed after 3 attempts")
                await failed_queue.put((chunk_id, start_f, end_f))

        self.log(f"💓 Worker {worker_id} finished; completed {len(completed_chunks)} chunks")
        return completed_chunks

    async def render_chunk(self, chunk_id, start_f, end_f, vol1, vol2,
                           paths, sig_text, font_name, target_h, frame_index):
        chunk_name = f"part_{chunk_id}.mp4"
        SENSITIVITY, MIN_OUTLINE, MAX_OUTLINE = 1.5, 0, 40
        SCALE_AMOUNT = 0.04
        range_val = MAX_OUTLINE - MIN_OUTLINE
        bitrate = f"{int((target_h / 1080) * 4000 * 1.15)}k"

        use_flipbook = len(frame_index) > 0
        total_frames_available = len(frame_index)

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--disable-dev-shm-usage", "--no-sandbox"]
                )
                context = await browser.new_page(
                    viewport={'width': self.width, 'height': self.height}
                )

                html_url = f"file:///{os.path.abspath(paths['html']).replace(chr(92), '/')}"
                await context.goto(html_url)

                bg_p = f"file:///{os.path.abspath(paths['bg_img']).replace(chr(92), '/')}"
                h_p  = f"file:///{os.path.abspath(paths['h_img']).replace(chr(92), '/')}"
                g_p  = f"file:///{os.path.abspath(paths['g_img']).replace(chr(92), '/')}"

                await context.evaluate(f"""() => {{
                    const bgImg = document.querySelector('.background-img');
                    if (bgImg) bgImg.src = '{bg_p}';

                    const s = document.querySelectorAll('.speaker');
                    if (s[0]) s[0].src = '{h_p}';
                    if (s[1]) s[1].src = '{g_p}';
                    window.speakers = s;
                    window.bgImg = bgImg;

                    const sig = document.querySelector('#signature');
                    if (sig) {{
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
                        transition: none !important;
                    }}
                """)

                codec = self.codec
                self.log(f"🎯 chunk {chunk_id}: encoding with {codec}")
                cmd = self._build_ffmpeg_cmd(codec, target_h, bitrate) + [chunk_name]

                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )

                try:
                    for i in range(start_f, end_f):
                        v1_val = float(vol1[i]) if float(vol1[i]) > 0.005 else 0
                        v2_val = float(vol2[i]) if float(vol2[i]) > 0.005 else 0

                        if use_flipbook:
                            frame_idx = i % total_frames_available
                            frame_url = f"file:///{frame_index[frame_idx].replace(chr(92), '/')}"
                            await context.evaluate(f"""() => {{
                                if (window.speakers[0]) window.speakers[0].style.setProperty('--pulse', {v1_val});
                                if (window.speakers[1]) window.speakers[1].style.setProperty('--pulse', {v2_val});
                                if (window.bgImg) window.bgImg.src = '{frame_url}';
                            }}""")
                        else:
                            await context.evaluate(f"""() => {{
                                if (window.speakers[0]) window.speakers[0].style.setProperty('--pulse', {v1_val});
                                if (window.speakers[1]) window.speakers[1].style.setProperty('--pulse', {v2_val});
                            }}""")

                        frame = await context.screenshot(type='jpeg', quality=85)
                        proc.stdin.write(frame)

                    proc.stdin.close()
                    _, stderr = proc.communicate()

                    if proc.returncode != 0:
                        err = stderr.decode('utf-8', errors='ignore')[:500]
                        self.log(f"❌ chunk {chunk_id}: encode failed (rc={proc.returncode}): {err}")
                        await browser.close()
                        return chunk_id, None

                    self.log(f"✅ chunk {chunk_id}: encoded successfully")

                except Exception as e:
                    self.log(f"💥 chunk {chunk_id}: rendering loop error: {e}")
                    proc.kill()
                    if os.path.exists(chunk_name):
                        try:
                            os.remove(chunk_name)
                        except Exception as e2:
                            self.log(f"⚠️ chunk {chunk_id}: cleanup failed: {e2}")
                    await browser.close()
                    return chunk_id, None

                await browser.close()
                return chunk_id, chunk_name

        except Exception as exc:
            self.log(f"❌ chunk {chunk_id}: unexpected exception: {exc}")
            if os.path.exists(chunk_name):
                try:
                    os.remove(chunk_name)
                except Exception as e:
                    self.log(f"⚠️ chunk {chunk_id}: cleanup failed: {e}")
            return chunk_id, None
