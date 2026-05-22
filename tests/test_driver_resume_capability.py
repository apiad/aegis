from aegis.drivers.claude import ClaudeDriver
from aegis.drivers.gemini import GeminiDriver
from aegis.drivers.opencode import OpenCodeDriver


def test_claude_supports_resume():
    assert ClaudeDriver().supports_resume is True


def test_gemini_does_not_support_resume_yet():
    assert GeminiDriver().supports_resume is False


def test_opencode_does_not_support_resume_yet():
    assert OpenCodeDriver().supports_resume is False
