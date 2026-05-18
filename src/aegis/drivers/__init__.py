from aegis.drivers.base import HarnessDriver, HarnessSession
from aegis.drivers.claude import ClaudeDriver

DRIVERS: dict[str, type[HarnessDriver]] = {"claude-code": ClaudeDriver}


def get_driver(harness: str) -> HarnessDriver:
    return DRIVERS[harness]()


__all__ = ["DRIVERS", "get_driver", "HarnessDriver", "HarnessSession",
           "ClaudeDriver"]
