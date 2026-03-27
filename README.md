# MedSystem – Discharge Summary Generator

An AI-powered medical discharge summary generator that converts doctor voice dictations into professionally formatted PDF discharge summaries.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![Flask](https://img.shields.io/badge/Flask-Web_Framework-green?logo=flask)

## Features

- **Voice-to-PDF**: Upload audio dictations (.mp3, .wav) and automatically generate structured discharge summaries using AI (OpenAI Whisper + Groq LLM).
- **Smart Templates**: Save and reuse discharge summary templates for common diagnoses.
- **PDF Management**: View, edit, regenerate, print, and download discharge summaries.
- **User Management**: Multi-user support with admin controls, role-based access, and audit logging.
- **HIPAA-Compliant Design**: Session timeouts, account lockouts, password policies, and comprehensive audit trails.
- **Desktop App**: Standalone Windows executable (`.exe`) powered by PyWebView — no Python installation required.
- **Usage Dashboard**: Weekly activity charts, summary counts, and recent activity tracking.

## Tech Stack

| Component       | Technology                    |
|-----------------|-------------------------------|
| Backend         | Flask (Python)                |
| AI Transcription| OpenAI Whisper                |
| AI Structuring  | Groq (LLaMA)                 |
| PDF Generation  | ReportLab                    |
| Database        | SQLite                       |
| Desktop App     | PyWebView + PyInstaller       |
| Security        | Flask-WTF (CSRF), bcrypt      |

## Quick Start

### Prerequisites
- Python 3.10+
- [FFmpeg](https://ffmpeg.org/download.html) (required for audio processing)
- [Groq API Key](https://console.groq.com/) (for AI-powered summary generation)

### Installation

```bash
# Clone the repository
git clone https://github.com/kishor78945/doctor-ui.git
cd doctor-ui

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configuration

1. **FFmpeg**: Update the FFmpeg path in `app.py` (line 30) to match your system:
   ```python
   os.environ["PATH"] = r"C:\path\to\ffmpeg\bin;" + os.environ.get("PATH", "")
   ```

2. **Groq API Key**: Set the environment variable:
   ```bash
   # Windows
   set GROQ_API_KEY=your_api_key_here

   # Linux/Mac
   export GROQ_API_KEY=your_api_key_here
   ```

### Running the Web App

```bash
python app.py
```
Open your browser and navigate to `http://127.0.0.1:5000`

**Default Login:** `admin` / `admin123`

### Building the Desktop App (Windows)

```bash
# Install desktop dependencies
pip install -r requirements_desktop.txt

# Build the executable
build_exe.bat
```

The standalone `.exe` will be created in the `dist/` folder.

## Project Structure

```
doctor-ui/
├── app.py                    # Main Flask application
├── db.py                     # Database models and helpers
├── desktop_app.py            # Desktop app launcher (PyWebView)
├── dischargesummary.py       # AI discharge summary generation (Groq)
├── import_pdfs.py            # PDF import utility
├── requirements.txt          # Web app dependencies
├── requirements_desktop.txt  # Desktop app dependencies
├── build_exe.bat             # Release build script
├── build_debug.bat           # Debug build script
└── templates/                # HTML templates
    ├── index.html            # Dashboard
    ├── login.html            # Login page
    ├── summaries.html        # All summaries list
    ├── summary.html          # Summary detail view
    ├── templates.html        # Template management
    ├── profile.html          # User profile
    ├── manage_users.html     # Admin: user management
    ├── audit_logs.html       # Admin: audit logs
    └── ...
```

## Disclaimer

This project is built for **educational purposes only**.
