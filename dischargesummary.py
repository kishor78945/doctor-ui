import os
import json
from groq import Groq

# Initialize Groq client
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# JSON schema as text (for the LLM prompt)
DISCHARGE_SCHEMA_TEXT = r"""
{
  "patient_info": {
    "name": "",
    "age": "",
    "sex": "",
    "hospital_id": "",
    "ward": "",
    "bed_number": "",
    "date_of_admission": "",
    "date_of_discharge": ""
  },
  "diagnosis": {
    "provisional_diagnosis": "",
    "final_diagnosis": ""
  },
  "history": {
    "presenting_complaints": "",
    "history_of_presenting_illness": "",
    "past_medical_history": ""
  },
  "hospital_course": "",
  "procedures": [
    {
      "procedure_name": "",
      "date": "",
      "surgeon": "",
      "anaesthetist": "",
      "key_findings": "",
      "complications": ""
    }
  ],
  "investigations": "",
  "vitals_at_discharge": {
    "bp": "",
    "pulse": "",
    "temperature": "",
    "spo2": ""
  },
  "medications": [
    {
      "drug_name": "",
      "dose": "",
      "route": "",
      "frequency": "",
      "duration": "",
      "remarks": ""
    }
  ],
  "discharge_advice": {
    "diet": "",
    "activity": "",
    "wound_care": "",
    "warning_signs": "",
    "other_instructions": ""
  },
  "follow_up": {
    "date": "",
    "department": "",
    "doctor": "",
    "special_instructions": ""
  },
  "doctor": {
    "name": "",
    "designation": "",
    "registration_number": ""
  }
}
"""


def build_discharge_prompt(transcript: str) -> str:
    """
    Build the full prompt sent to the LLM:
    - instructions
    - schema
    - doctor's dictation
    """
    return f"""
You are a medical documentation assistant in an Indian hospital.

Your job is to convert a doctor's free-text discharge dictation into a structured
discharge summary using the JSON schema provided below.

RULES (VERY IMPORTANT):
- Do NOT invent information that is not clearly present in the dictation.
- If some field is not mentioned, fill it with an empty string "".
- Copy medication names and doses exactly as spoken.
- Do NOT add your own medical advice or change treatment details.
- Output ONLY valid JSON according to the schema. No extra text, no comments.

JSON SCHEMA (keys must be exactly as below, only fill values):

{DISCHARGE_SCHEMA_TEXT}

DOCTOR'S DICTATION:
\"\"\" 
{transcript}
\"\"\"
"""


def generate_discharge_json_from_transcript(transcript: str) -> dict:
    """
    Send transcript to Llama via Groq and get back a Python dict
    matching the discharge summary schema.
    """
    prompt = build_discharge_prompt(transcript)

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are a precise medical documentation assistant."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,   # deterministic
    )

    # --------- CLEAN & PARSE MODEL OUTPUT HERE (INDENTED INSIDE FUNCTION) ---------
    raw_text = response.choices[0].message.content.strip()

    # Some models wrap JSON in ```json ... ``` code fences.
    # We need to strip those off before parsing.
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        # remove opening fence
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # remove closing fence if present
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw_text = "\n".join(lines).strip()

    # As an extra safety: keep only from first '{' to last '}'
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1:
        raw_text = raw_text[start:end+1]

    # Now parse JSON
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print("Failed to parse JSON from cleaned model output.")
        print("CLEANED output was:")
        print(raw_text)
        raise e

    return data
    # -----------------------------------------------------------------------------


def main():
    fake_transcript = """
    55-year-old male, Mr Rajesh, admitted on 2nd December 2025 with acute abdominal pain,
    diagnosed as acute appendicitis. Laparoscopic appendectomy performed on 3rd December 2025
    by Dr Kumar under general anaesthesia. Post-operative course was uneventful.
    He is being discharged today, 6th December 2025.

    Medications on discharge:
    Tablet Augmentin 625 mg twice daily for 5 days.
    Tablet Paracetamol 650 mg when required for pain, maximum three times a day.
    Follow-up after 7 days in General Surgery OPD for wound check and suture removal.
    """

    discharge_data = generate_discharge_json_from_transcript(fake_transcript)

    print("Final diagnosis:", discharge_data["diagnosis"]["final_diagnosis"])
    print("First medication:", discharge_data["medications"][0]["drug_name"])
    print("Follow-up:", discharge_data["follow_up"])


if __name__ == "__main__":
    main()
