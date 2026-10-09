"""Write site/client.css: the client's own stylesheets with the #a2 scope
rewritten to .a2, so the landing page's mockups are the real UI's rules applied
to hand-written markup. tests/test_site.py fails when the copy falls behind.

    uv run python scripts/site_css.py
"""

from pathlib import Path

ROOT = Path(__file__).parents[1]
CLIENT = ROOT / "src" / "aegis" / "client"
OUT = ROOT / "site" / "client.css"


def render() -> str:
    parts = [CLIENT / "css" / "fonts.css", CLIENT / "css" / "base.css"]
    parts += sorted((CLIENT / "themes").glob("*.css"))
    text = "".join(p.read_text() for p in parts)
    return text.replace("#a2", ".a2").replace("/static/fonts/", "fonts/")


if __name__ == "__main__":
    OUT.write_text(render())
    print(f"wrote {OUT.relative_to(ROOT)}")
