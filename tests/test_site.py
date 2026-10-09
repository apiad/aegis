"""The landing page's mockups are styled by a copy of the client's CSS."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "site_css.py"


def test_the_landing_page_styles_its_mockups_with_the_clients_css_as_it_is():
    """A copy that falls behind draws the mockups as the UI used to look. The
    fix is `make site-css`."""
    spec = importlib.util.spec_from_file_location("site_css", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.OUT.read_text() == mod.render(), (
        "site/client.css is stale: run make site-css"
    )
