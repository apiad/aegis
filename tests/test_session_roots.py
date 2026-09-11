def test_project_root_ignores_process_cwd(tmp_path, monkeypatch):
    """session.py:85,91 used `project_root or Path.cwd()`, so a session
    built without an explicit root silently adopted the process cwd —
    wrong the moment two instances share a process."""
    from aegis.core.session import AgentSession
    import inspect
    sig = inspect.signature(AgentSession.__init__)
    param = sig.parameters["project_root"]
    assert param.default is inspect.Parameter.empty, (
        "project_root must be required; a default reintroduces the cwd "
        "fallback this task removes")
