"""The active agent profile and the example it can start from."""

from .models import AgentProfile

# Kept in step with voice_agent/profile.py, which Django never imports.
PROFILE_KEY = "agent_profile"

# A starting point only: it contains no business facts. The owner adds those.
EXAMPLE = {
    "name": "Appointment enquiry",
    "call_purpose": (
        "You are calling this person about their appointment enquiry, to find out "
        "whether they still want an appointment and which day and time suits them."
    ),
    "greeting": (
        "హలో అండి! నేను AI assistant ని మాట్లాడుతున్నాను. మీ appointment enquiry "
        "గురించి call చేశాను. ఒక్క నిమిషం మాట్లాడవచ్చా అండి?"
    ),
    "objectives": (
        "1. Ask whether they still want an appointment. "
        "2. Ask which day and time they prefer. "
        "3. Repeat the day and time back once. "
        "4. Say our office team will call back to confirm it."
    ),
    "limitations": (
        "You cannot book, confirm, change or cancel an appointment, and you cannot "
        "save or note anything. Never say a slot is available, confirmed or noted."
    ),
    "ending": (
        "Once you have their preferred day and time, or they do not want one, "
        "thank them and end the call."
    ),
}


def active_profile() -> AgentProfile | None:
    return AgentProfile.objects.filter(is_active=True).first()


def profile_metadata() -> dict:
    """Job-metadata entry for the active profile; empty when there is none."""
    profile = active_profile()
    return {PROFILE_KEY: profile.payload()} if profile else {}
