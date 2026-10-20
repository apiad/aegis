import logging


def test_a_refused_tool_call_logs_no_rich_traceback():
    """fastmcp logs every ToolError with its traceback, and every refused op is
    one. Rendered rich, each cost about 0.5 s on the event loop (#366)."""
    import aegis.mcp_tools  # noqa: F401

    handlers = logging.getLogger("fastmcp").handlers
    assert handlers
    assert not any(getattr(h, "rich_tracebacks", False) for h in handlers)


def test_a_tool_errors_message_is_not_wrapped_by_rich():
    """Rich wraps a logged message in time growing with its length squared: a
    page's 4,000-character error held the event loop for seconds (#409)."""
    from rich.logging import RichHandler

    import aegis.mcp_tools  # noqa: F401

    handlers = logging.getLogger("fastmcp").handlers
    assert handlers
    assert not any(isinstance(h, RichHandler) for h in handlers)
