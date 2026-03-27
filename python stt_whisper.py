import os
import whisper
from dischargesummary import generate_discharge_json_from_transcript

# Force-add ffmpeg bin folder to PATH just for this Python process
os.environ["PATH"] = r"C:\ffmpeg\ffmpeg-2025-12-04-git-d6458f6a8b-full_build\bin;" + os.environ.get("PATH", "")

import whisper

def transcribe_audio(file_path: str) -> str:
    model = whisper.load_model("small")
    result = model.transcribe(file_path, language="en")
    return result["text"]

def main():
    audio_file = "sample_doctor_note.mp3"  # your recording
    transcript = transcribe_audio(audio_file)

    print("Transcript:\n", transcript)

    # NEW: send transcript to LLM → get structured JSON
    discharge_data = generate_discharge_json_from_transcript(transcript)

    print("\n--- Structured Discharge Summary ---")
    print("Final diagnosis:", discharge_data["diagnosis"]["final_diagnosis"])
    print("First medication:", discharge_data["medications"][0]["drug_name"])
    print("Follow-up:", discharge_data["follow_up"])


if __name__ == "__main__":
    main()
