from aegis.drivers.base import HarnessDriver, HarnessSession
from aegis.drivers.claude import ClaudeDriver
from aegis.drivers.copilot import CopilotDriver
from aegis.drivers.gemini import GeminiDriver
from aegis.drivers.opencode import OpenCodeDriver

# Provider name → driver class. Provider names match the strings users
# pass as `Agent(harness=...)` (the legacy string shape) and the
# `Provider.name` attribute on the new object shape (see config.Provider
# subclasses: ClaudeCode, GeminiCLI, OpenCode, CopilotCLI).
DRIVERS: dict[str, type[HarnessDriver]] = {
    "claude-code": ClaudeDriver,
    "gemini":      GeminiDriver,
    "opencode":    OpenCodeDriver,
    "copilot":     CopilotDriver,
}


def get_driver(harness: str) -> HarnessDriver:
    return DRIVERS[harness]()


__all__ = ["DRIVERS", "get_driver", "HarnessDriver", "HarnessSession",
           "ClaudeDriver", "CopilotDriver", "GeminiDriver", "OpenCodeDriver"]
