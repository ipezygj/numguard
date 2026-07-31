"""The README's install command must name this package, by the name PyPI knows.

Install lines rot in a specific way: the distribution gets published, and the
instruction keeps pointing at a git URL. Nothing fails — the docs are not
executed — and the person who finds out is a stranger whose very first command
needs git and a toolchain. This README pointed at a git URL for months after
numguard was on PyPI.

Offline by design: it compares the README against pyproject, and never asks
the network whether a name resolves.
"""
import pathlib
import re

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # py3.10
    tomllib = pytest.importorskip("tomli", reason="needs tomllib (3.11+) or tomli")

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
PYPROJECT = ROOT / "pyproject.toml"

# `pip install "name[extra]"`, `pip install name==1.2`, `pip install git+https://…`
INSTALL = re.compile(r'pip install\s+(?:-[^\s]+\s+)*["\']?([^\s"\'#]+)')


def _dist_name():
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]["name"]


def _normalize(name):
    """PEP 503: - _ . are equivalent and case does not matter."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _targets():
    return INSTALL.findall(README.read_text(encoding="utf-8"))


def test_readme_has_an_install_line():
    assert _targets(), "README shows no `pip install` command at all"


@pytest.mark.parametrize("target", _targets())
def test_install_line_names_this_package(target):
    assert not target.startswith(("git+", "http://", "https://")), (
        f"README installs from a URL ({target}) — publish to PyPI and name the package, "
        "or readers need git and a toolchain to try it"
    )
    assert "@" not in target, f"README pins a direct reference ({target}) rather than the PyPI name"

    base = target.split("[")[0].split("==")[0].split(">=")[0].split("~=")[0]
    assert _normalize(base) == _normalize(_dist_name()), (
        f"README says `pip install {base}` but pyproject publishes {_dist_name()!r}"
    )
