from aegis.drivers.claude import ClaudeDriver
from aegis.drivers.gemini import GeminiDriver
from aegis.drivers.opencode import OpenCodeDriver


def test_claude_supports_resume():
    assert ClaudeDriver().supports_resume is True


def test_gemini_supports_resume():
    """ACP defines loadSession; the AcpDriver advertises it and start()
    invokes load_session when the saved session_id is provided. If the
    spawned agent doesn't actually implement it, the resumed tab
    surfaces a clear failure banner — but the driver-level capability
    is True."""
    assert GeminiDriver().supports_resume is True


def test_opencode_supports_resume():
    assert OpenCodeDriver().supports_resume is True
