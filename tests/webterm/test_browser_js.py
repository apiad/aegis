"""The page's pure logic, run under node so `make test` covers it.

The .mjs tests under tests/web are run by hand and nothing gates them.
These are gated. A missing node fails rather than skips, because a skipped
check reads as a passing one; CI's Ubuntu image ships node.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
SCRIPTS = sorted(HERE.glob("*.test.mjs"))


def test_the_scripts_were_found():
    assert SCRIPTS, "no *.test.mjs beside this file; the tests below ran nothing"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_browser_module(script):
    node = shutil.which("node")
    assert node, "node is required to test the aegis web page"
    r = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
