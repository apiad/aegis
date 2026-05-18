from __future__ import annotations

from dataclasses import dataclass

from textual.theme import Theme

INK = Theme(
    name="aegis-ink",
    dark=True,
    background="#0e0e0d",
    surface="#141412",
    panel="#1a1a17",
    foreground="#DCD9CF",
    primary="#E0A872",
    accent="#E0A872",
    success="#9DB07E",
    warning="#E0A872",
    error="#C56B5C",
    variables={
        "aegis-muted": "#76736a",
        "aegis-faint": "#4a4843",
        "aegis-rule": "#26241f",
        "aegis-userbg": "#24241f",
    },
)

THEMES: dict[str, Theme] = {"ink": INK}
DEFAULT_THEME = "aegis-ink"


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


def aegis_colors(theme: Theme) -> AegisColors:
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
    )
