"""
utils.py — Shared helpers used across multiple modules.
"""

import os
from pathlib import Path
from google import genai

VOICE_PROFILE = Path(__file__).parent.parent / "assets" / "voice_profile.txt"
MODEL = "gemini-2.5-flash"

# Confirmation signals that indicate a successful ATS submission
SUCCESS_SIGNALS = [
    "thank you",
    "thanks for applying",
    "application received",
    "application submitted",
    "successfully applied",
    "we'll be in touch",
    "your application has been",
    "application complete",
    "we have received",
]


def get_gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY not set in .env")
    return genai.Client(api_key=api_key)


def load_voice() -> str:
    if VOICE_PROFILE.exists():
        return VOICE_PROFILE.read_text(encoding="utf-8")
    return ""
