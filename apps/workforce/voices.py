"""Voices an AI employee can speak with, and the languages it can work in.

Django never imports the voice stack, so the list is kept here. A test
(tests/voice/test_employee_calls.py) checks every voice against the Sarvam
plugin the worker uses, so a voice the worker does not have cannot be chosen.
"""

TTS_MODEL = "bulbul:v3"
DEFAULT_PACE = 1.0
PACE_CHOICES = (
    ("0.8", "Slower"),
    ("0.9", "A little slower"),
    ("1.0", "Normal"),
    ("1.1", "A little faster"),
    ("1.2", "Faster"),
)

VOICES = (
    ("kavitha", "Kavitha (female)"),
    ("kavya", "Kavya (female)"),
    ("pooja", "Pooja (female)"),
    ("priya", "Priya (female)"),
    ("ritu", "Ritu (female)"),
    ("roopa", "Roopa (female)"),
    ("shreya", "Shreya (female)"),
    ("simran", "Simran (female)"),
    ("neha", "Neha (female)"),
    ("ishita", "Ishita (female)"),
    ("aditya", "Aditya (male)"),
    ("rahul", "Rahul (male)"),
    ("rohan", "Rohan (male)"),
    ("dev", "Dev (male)"),
    ("varun", "Varun (male)"),
    ("kabir", "Kabir (male)"),
    ("amit", "Amit (male)"),
    ("shubh", "Shubh (male)"),
)
VOICE_NAMES = frozenset(name for name, _ in VOICES)

# Employee.language -> the language code the worker uses for speech in and out.
# Telugu + English uses Telugu recognition, which keeps English words as spoken.
SPEECH_LANGUAGE = {"te-en": "te-IN", "te": "te-IN", "en": "en-IN"}
