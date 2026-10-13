import logging


def test_a_refused_tool_call_logs_no_rich_traceback():
    """fastmcp logs every ToolError with its traceback, and every refused op is
    one. Rendered rich, each cost about 0.5 s on the event loop (#366)."""
    import aegis.mcp  # noqa: F401

    handlers = logging.getLogger("fastmcp").handlers
    assert handlers
    assert not any(getattr(h, "rich_tracebacks", False) for h in handlers)
