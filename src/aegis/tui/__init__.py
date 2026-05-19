__all__ = ["AegisApp"]


def __getattr__(name):
    if name == "AegisApp":
        from aegis.tui.app import AegisApp as _AegisApp
        return _AegisApp
    raise AttributeError(name)
