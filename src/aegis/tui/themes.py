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

# Warm Parchment — mockup A (.playground/aegis-mockups/A-warm-parchment.html).
# Aged-paper warmth: clay accent, gold warning, olive ready, rust error.
PARCHMENT = Theme(
    name="aegis-parchment",
    dark=True,
    background="#1c1a16",
    surface="#201e19",
    panel="#23211c",
    foreground="#E9E2D2",
    primary="#D97757",
    accent="#D97757",
    success="#9CAE78",
    warning="#E3B341",
    error="#E0775F",
    variables={
        "aegis-muted": "#8c8676",
        "aegis-faint": "#5c574a",
        "aegis-rule": "#3a362d",
        "aegis-userbg": "#2b281f",
    },
)

# Cool Slate — mockup B (.playground/aegis-mockups/B-cool-slate.html).
# Cool blue-grey field with a warm amber accent; teal ready, rose error.
SLATE = Theme(
    name="aegis-slate",
    dark=True,
    background="#10141b",
    surface="#13171f",
    panel="#161b24",
    foreground="#CDD6E3",
    primary="#E0A35E",
    accent="#E0A35E",
    success="#5FB39A",
    warning="#E0A35E",
    error="#E07A86",
    variables={
        "aegis-muted": "#6b7686",
        "aegis-faint": "#454e5c",
        "aegis-rule": "#27303d",
        "aegis-userbg": "#1e2530",
    },
)

THEMES: dict[str, Theme] = {"ink": INK, "parchment": PARCHMENT,
                            "slate": SLATE}
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
