"""Process-monitor substrate: poll agent-supplied bash, wake the agent."""
from aegis.monitor.manager import MonitorManager
from aegis.monitor.schema import Monitor, MonitorView

__all__ = ["MonitorManager", "Monitor", "MonitorView"]
