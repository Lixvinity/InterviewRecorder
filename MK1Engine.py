import asyncio
import os
import sys
import subprocess
import numpy as np
import math
import time
import multiprocessing
import psutil
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


class run_video_generation:
    def __init__(self, width=1920, height=1080, fps=24):
        self.width = width
        self.height = height
        self.fps = fps
        self.ffmpeg_exe = FFMPEG_PATH

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

        print(f"📊 System RAM: {total_ram_gb:.2f} GB")
        print(f"🛠 Hardware Profile: Using {self.cores}/{total_cores} cores")
        print(f"🚀 Encoder Selected: {self.codec}")

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
            print(f"⚠️ Playwright check/install failed: {e}")

    def generate(self, audio1_path, audio2_path, bg_path, icon1_path, icon2_path,
                 glow_path, output_folder, signature_text, font_name, target_h=720,
                 bg_frames_folder=None):
        """
        bg_frames_folder : str | None
            Path to a folder of pre-extracted JPEG frames (frame_000001.jpg …).
            When provided the background animates through those frames like a
            flipbook.  When None, bg_path is used as a static image.
        """
        self.ensure_playwright_installed()
        out_name = f"render_{int(time.time())}.mp4"
        final_output = os.path.join(output_folder, out_name)

        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        return asyncio.run(self._async_generate(
            audio1_path, audio2_path, bg_path, icon1_path, icon2_path,
            final_output, signature_text, font_name, target_h, bg_frames_folder
        ))

    async def _async_generate(self, a1, a2, bg, i1, i2, out_path,
                               signature, font, target_h, bg_frames_folder):
        print("📊 Analyzing audio channels...")
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
            print(f"🎞 Flipbook mode: {len(frame_index)} frames available.")
        else:
            print("🖼 Static background mode.")

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

        print(f"🔥 Rendering {total_chunks} chunks on {self.cores} workers...")

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
            print(f"🔄 {failed_queue.qsize()} chunks were rejected. Rescuing...")
            while not failed_queue.empty():
                c_id, c_start, c_end = await failed_queue.get()
                res = None
                while not res or not res[1]:
                    print(f"⚠️ Retrying failed chunk {c_id}...")
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

        print("🎬 Concatenating chunks...")
        subprocess.run([
            self.ffmpeg_exe, '-y', '-f', 'concat', '-safe', '0', '-i', parts_file,
            '-i', a1, '-i', a2,
            '-filter_complex', '[1:a][2:a]amix=inputs=2:duration=longest[aout]',
            '-map', '0:v', '-map', '[aout]', '-c:v', 'copy', '-c:a', 'aac', out_path
        ], stderr=subprocess.DEVNULL,
           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

        for f in valid_chunks + [parts_file]:
            if os.path.exists(f):
                os.remove(f)

        print(f"✅ Video saved to: {out_path}")
        return out_path

    async def worker_routine(self, worker_id, assigned_chunks, vol1, vol2,
                             paths, sig_text, font_name, target_h, failed_queue,
                             frame_index):
        completed_chunks = []
        CHUNK_TIMEOUT = 45

        for chunk_id, start_f, end_f in assigned_chunks:
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
                except Exception:
                    await asyncio.sleep(2)

            if not success:
                await failed_queue.put((chunk_id, start_f, end_f))

        return completed_chunks

    async def render_chunk(self, chunk_id, start_f, end_f, vol1, vol2,
                           paths, sig_text, font_name, target_h, frame_index):
        chunk_name = f"part_{chunk_id}.mp4"
        SENSITIVITY, MIN_OUTLINE, MAX_OUTLINE = 1.5, 0, 40
        SCALE_AMOUNT = 0.04
        range_val = MAX_OUTLINE - MIN_OUTLINE
        bitrate = f"{int((target_h / 1080) * 4000 * 1.15)}k"

        # Flipbook mode when we have pre-extracted frames, static otherwise.
        use_flipbook = len(frame_index) > 0
        total_frames_available = len(frame_index)  # 0 in static mode

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

                # Static background path — used in static mode and as the
                # initial src in flipbook mode (overwritten per-frame below).
                bg_p = f"file:///{os.path.abspath(paths['bg_img']).replace(chr(92), '/')}"
                h_p  = f"file:///{os.path.abspath(paths['h_img']).replace(chr(92), '/')}"
                g_p  = f"file:///{os.path.abspath(paths['g_img']).replace(chr(92), '/')}"

                # Initial page setup — always use the static <img> element;
                # the video element is never touched.
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

                cmd = [
                    self.ffmpeg_exe, '-y',
                    '-f', 'image2pipe', '-vcodec', 'mjpeg', '-r', str(self.fps),
                    '-i', '-',
                    '-vf', f'scale=-2:{target_h}',
                    '-c:v', self.codec
                ]

                if "nvenc" in self.codec:
                    cmd += ['-rc', 'vbr', '-cq', '28', '-b:v', bitrate,
                            '-maxrate', bitrate, '-preset', 'p1', '-pix_fmt', 'yuv420p']
                elif "amf" in self.codec:
                    cmd += ['-b:v', bitrate, '-pix_fmt', 'yuv420p']
                else:
                    cmd += ['-crf', '26', '-preset', 'ultrafast',
                            '-pix_fmt', 'yuv420p', '-b:v', bitrate]

                cmd.append(chunk_name)

                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )

                for i in range(start_f, end_f):
                    v1_val = float(vol1[i]) if float(vol1[i]) > 0.005 else 0
                    v2_val = float(vol2[i]) if float(vol2[i]) > 0.005 else 0

                    if use_flipbook:
                        # Clamp frame index so short videos loop / hold on last frame.
                        frame_idx = i % total_frames_available
                        frame_path = frame_index[frame_idx].replace('\\', '/')
                        frame_url  = f"file:///{frame_path}"

                        await context.evaluate(f"""() => {{
                            if (window.speakers[0]) window.speakers[0].style.setProperty('--pulse', {v1_val});
                            if (window.speakers[1]) window.speakers[1].style.setProperty('--pulse', {v2_val});
                            if (window.bgImg) window.bgImg.src = '{frame_url}';
                        }}""")
                    else:
                        # Static image — just update the pulse values.
                        await context.evaluate(f"""() => {{
                            if (window.speakers[0]) window.speakers[0].style.setProperty('--pulse', {v1_val});
                            if (window.speakers[1]) window.speakers[1].style.setProperty('--pulse', {v2_val});
                        }}""")

                    frame = await context.screenshot(type='jpeg', quality=85)
                    proc.stdin.write(frame)

                proc.stdin.close()
                proc.wait()
                await browser.close()
                return chunk_id, chunk_name

        except Exception:
            if os.path.exists(chunk_name):
                try:
                    os.remove(chunk_name)
                except Exception:
                    pass
            return chunk_id, None
