from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from textual.theme import Theme as TextualTheme

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "themes"
_DEFAULT_USER_DIR = Path(".aegis") / "themes"


@dataclass(frozen=True)
class AegisColors:
    ready: str
    working: str
    error: str
    accent: str
    muted: str
    ok: str
    err: str
    user: str
    user_bg: str
    ink: str = ""        # default foreground / "ink" of the page
    work: str = ""       # alias for working — used by queue dashboard


def aegis_colors(theme: TextualTheme) -> AegisColors:
    fg = theme.foreground or "#DCD9CF"
    variables = theme.variables or {}

    def var(key: str) -> str:
        return variables.get(key) or fg

    return AegisColors(
        ready=theme.success or fg,
        working=theme.warning or fg,
        error=theme.error or fg,
        accent=theme.accent or fg,
        muted=var("aegis-muted"),
        ok=theme.success or fg,
        err=theme.error or fg,
        user=theme.accent or fg,
        user_bg=var("aegis-userbg"),
        ink=theme.foreground or fg,
        work=theme.warning or fg,
    )


@dataclass(frozen=True)
class AegisTheme:
    name: str
    dark: bool
    colors: dict[str, str]
    variables: dict[str, str]

    def to_textual_theme(self) -> TextualTheme:
        c = self.colors
        return TextualTheme(
            name=self.name,
            dark=self.dark,
            background=c["background"],
            surface=c["surface"],
            panel=c["panel"],
            foreground=c["foreground"],
            primary=c["primary"],
            accent=c["accent"],
            success=c["success"],
            warning=c["warning"],
            error=c["error"],
            variables=dict(self.variables),
        )

    def to_aegis_colors(self) -> AegisColors:
        return aegis_colors(self.to_textual_theme())

    def to_css_variables(self) -> str:
        c = self.colors
        v = self.variables
        lines = [
            ("--aegis-bg", c["background"]),
            ("--aegis-surface", c["surface"]),
            ("--aegis-panel", c["panel"]),
            ("--aegis-fg", c["foreground"]),
            ("--aegis-primary", c["primary"]),
            ("--aegis-accent", c["accent"]),
            ("--aegis-ready", c["success"]),
            ("--aegis-working", c["warning"]),
            ("--aegis-error", c["error"]),
            ("--aegis-ok", c["success"]),
            ("--aegis-err", c["error"]),
            ("--aegis-user", c["accent"]),
            ("--aegis-muted", v.get("aegis-muted", c["foreground"])),
            ("--aegis-faint", v.get("aegis-faint", c["foreground"])),
            ("--aegis-rule", v.get("aegis-rule", c["foreground"])),
            ("--aegis-user-bg", v.get("aegis-userbg", c["background"])),
        ]
        body = "\n".join(f"  {k}: {val};" for k, val in lines)
        return ":root {\n" + body + "\n}\n"


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_theme(name: str, user_dir: Path | None = None) -> AegisTheme:
    base_path = _DATA_DIR / f"{name}.yaml"
    if not base_path.exists():
        raise FileNotFoundError(
            f"no bundled theme named {name!r} at {base_path}")
    data = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}

    overlay_dir = user_dir if user_dir is not None else _DEFAULT_USER_DIR
    overlay_path = overlay_dir / f"{name}.yaml"
    if overlay_path.exists():
        overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8")) or {}
        data = _deep_merge(data, overlay)

    return AegisTheme(
        name=data["name"],
        dark=bool(data.get("dark", True)),
        colors=dict(data["colors"]),
        variables=dict(data.get("variables", {})),
    )


def list_theme_names() -> list[str]:
    return sorted(p.stem for p in _DATA_DIR.glob("*.yaml"))
