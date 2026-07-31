"""A published package must say where it lives and where to report a problem.

numguard's PyPI page carried no project URLs at all — someone who found the
package could not reach the repository, the docs, or an issue tracker. It showed
up only by comparing the three siblings; a single page looks fine on its own.

Offline: it reads pyproject, and never asks the network whether a URL resolves.
"""
import pathlib

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # py3.10
    tomllib = pytest.importorskip("tomli", reason="needs tomllib (3.11+) or tomli")

PYPROJECT = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
REQUIRED = ("Homepage", "Source", "Issues")


def _urls():
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"].get("urls", {})


def test_urls_table_exists():
    assert _urls(), "pyproject declares no [project.urls] — the PyPI page will link nowhere"


@pytest.mark.parametrize("key", REQUIRED)
def test_required_url_is_declared(key):
    urls = _urls()
    assert key in urls, f"[project.urls] has no {key}: {sorted(urls)}"
    assert urls[key].startswith("https://"), f"{key} is not an https URL: {urls[key]!r}"
