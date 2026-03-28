import asyncio

import os

import subprocess

import numpy as np

import math

import time

from playwright.async_api import async_playwright

from pydub import AudioSegment

from scipy.ndimage import gaussian_filter1d


class run_video_generation:

    def __init__(self, width=1920, height=1080, target_h=720, fps=15):

        self.width = width

        self.height = height

        self.fps = fps

        self.cores = 12

        self.codec = "h264_nvenc"  # Use "libx264" if no Nvidia GPU


    def generate(self, audio1_path, audio2_path, bg_path, icon1_path, icon2_path,

                 glow_path, output_folder, signature_text, font_name):

       

        out_name = f"render_{int(time.time())}.mp4"

        final_output = os.path.join(output_folder, out_name)


        if not os.path.exists(output_folder):

            os.makedirs(output_folder)


        return asyncio.run(self._async_generate(

            audio1_path, audio2_path, bg_path, icon1_path, icon2_path,

            final_output, signature_text, font_name

        ))


    async def _async_generate(self, a1, a2, bg, i1, i2, out_path, signature, font):

        print("📊 Analyzing audio channels...")

        s2 = AudioSegment.from_file(a1)

        s1 = AudioSegment.from_file(a2)

        total_f = int((max(len(s1), len(s2)) / 1000.0) * self.fps)

       

        def get_v(seg):

            ms = 1000 / self.fps

            v = np.zeros(total_f)

            limit = min(total_f, int(len(seg)/ms))

            v[:limit] = [seg[i*ms:(i+1)*ms].rms for i in range(limit)]

            if v.max() > 0: v /= v.max()

            return gaussian_filter1d(v, sigma=1.2)


        v1, v2 = get_v(s1), get_v(s2)


        paths = {

            'html': r"DefaultImages\movie.html",

            'bg_img': bg,

            'h_img': i1,

            'g_img': i2

        }


        chunk_len = math.ceil(total_f / self.cores)

        tasks = []

        for i in range(self.cores):

            tasks.append(self.render_chunk(

                i, i*chunk_len, min((i+1)*chunk_len, total_f),

                v1, v2, paths, signature, font

            ))


        print(f"🔥 Rendering on {self.cores} cores...")

        chunks_data = [None] * self.cores

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

            'ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', parts_file,

            '-i', a1, '-i', a2,

            '-filter_complex', '[1:a][2:a]amix=inputs=2:duration=longest[aout]',

            '-map', '0:v', '-map', '[aout]', '-c:v', 'copy', '-c:a', 'aac', out_path

        ], stderr=subprocess.DEVNULL)


        for f in valid_chunks + [parts_file]:

            if os.path.exists(f): os.remove(f)


        print(f"✅ Video saved to: {out_path}")

        return out_path


    async def render_chunk(self, chunk_id, start_f, end_f, vol1, vol2, paths, sig_text, font_name):

        chunk_name = f"part_{chunk_id}.mp4"

       

        SENSITIVITY = 1.5

        MIN_OUTLINE = 0

        MAX_OUTLINE = 40

        SCALE_AMOUNT = 0.04

        range_val = MAX_OUTLINE - MIN_OUTLINE


        try:

            async with async_playwright() as p:

                browser = await p.chromium.launch(headless=True)

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


                # --- UPDATED CSS LOGIC ---

                # Added outline-color with a clamp to kill visibility at low volumes

                await context.add_style_tag(content=f"""

                    .speaker {{

                        outline-width: calc({MIN_OUTLINE}px + (var(--pulse, 0) * {SENSITIVITY} * {range_val}px)) !important;

                        transform: scale(calc(1 + (var(--pulse, 0) * {SCALE_AMOUNT}))) !important;

                        outline-style: solid !important;

                       

                        /* Kill alpha if pulse is below 0.01 (1%) */

                        outline-color: rgba(70, 220, 70, clamp(0, (var(--pulse) - 0.02) * 100, 1)) !important;

                       

                        transition: transform 0.04s linear, outline-width 0.04s linear;

                    }}

                """)


                cmd = [

                    'ffmpeg', '-y', '-f', 'image2pipe', '-vcodec', 'mjpeg', '-r', str(self.fps),

                    '-i', '-', '-c:v', self.codec, '-preset', 'p1', '-pix_fmt', 'yuv420p', chunk_name

                ]

                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)


                for i in range(start_f, end_f):

                    v1_val, v2_val = float(vol1[i]), float(vol2[i])


                    # --- UPDATED PYTHON LOGIC ---

                    # Hard-clamp values to absolute zero if they are negligible

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