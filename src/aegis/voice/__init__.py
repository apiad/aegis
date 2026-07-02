"""Optional push-to-talk voice input (harp-backed). Import-safe without
harp/sounddevice installed — deps are probed lazily."""
from aegis.voice.availability import unavailable_reason, voice_available
from aegis.voice.session import VoiceSession

__all__ = ["VoiceSession", "voice_available", "unavailable_reason"]
