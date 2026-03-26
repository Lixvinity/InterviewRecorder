import argparse
import os
import numpy as np
import noisereduce as nr
from pydub import AudioSegment

def process_and_overwrite(file_path, target_dbfs, do_denoise, do_normalize, skip_this_file):
    """Loads, cleans, and/or normalizes, then overwrites the original file."""
    if skip_this_file:
        print(f"\n--- Skipping modification for: {file_path} (Untouched)")
        return

    print(f"\n--- Processing: {file_path}")
    
    extension = os.path.splitext(file_path)[1].lower().replace('.', '')
    format_type = extension if extension in ['mp3', 'wav', 'ogg', 'flac'] else 'wav'
    
    audio = AudioSegment.from_file(file_path)
    sr = audio.frame_rate
    channels = audio.channels
    
    # 1. Noise Reduction
    if do_denoise:
        print("--- Running noise reduction...")
        samples = np.array(audio.get_array_of_samples())
        if channels == 2:
            samples = samples.reshape((-1, 2)).T

        reduced_noise = nr.reduce_noise(y=samples, sr=sr, stationary=True)

        if channels == 2:
            reduced_noise = reduced_noise.T.reshape(-1)
        
        final_samples = reduced_noise.astype(np.int16).tobytes()
        audio = audio._spawn(final_samples)
    else:
        print("--- Skipping noise reduction.")

    # 2. Dynamic Normalization
    if do_normalize:
        safe_target = min(target_dbfs, -0.1) 
        print(f"--- Normalizing to {safe_target:.2f} dBFS...")
        change_in_gain = safe_target - audio.dBFS
        audio = audio.apply_gain(change_in_gain)
    else:
        print("--- Skipping normalization.")

    # 3. Overwrite original file
    audio.export(file_path, format=format_type)
    print(f"--- Done: {file_path}")

def main():
    parser = argparse.ArgumentParser(description="Process audio with optional skipping of file modification.")
    parser.add_argument("file1", help="First audio file")
    parser.add_argument("file2", help="Second audio file")
    
    # Processing Flags
    parser.add_argument("--denoise", action="store_true", help="Enable noise reduction")
    parser.add_argument("--normalize", action="store_true", help="Enable volume normalization")
    
    # Skip Flags
    parser.add_argument("--skip1", action="store_true", help="Do NOT modify file1 (but still use it for volume averaging)")
    parser.add_argument("--skip2", action="store_true", help="Do NOT modify file2 (but still use it for volume averaging)")
    
    args = parser.parse_args()

    if not os.path.exists(args.file1) or not os.path.exists(args.file2):
        print("Error: One or both files not found.")
        return

    # Phase 1: Analyze BOTH files regardless of skip status
    print("--- Analyzing both tracks for volume sync...")
    audio1 = AudioSegment.from_file(args.file1)
    audio2 = AudioSegment.from_file(args.file2)
    
    target_loudness = (audio1.dBFS + audio2.dBFS) / 2
    print(f"--- Combined Average: {target_loudness:.2f} dBFS")

    # Phase 2: Process only the files that aren't skipped
    process_and_overwrite(args.file1, target_loudness, args.denoise, args.normalize, args.skip1)
    process_and_overwrite(args.file2, target_loudness, args.denoise, args.normalize, args.skip2)

    print("\n--- All tasks completed.")

if __name__ == "__main__":
    main()