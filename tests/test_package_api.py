"""The package must advertise the version it is, and export what it promises.

`numguard.__version__` said 0.1.0 while pyproject shipped 0.1.2 — two releases
stale, and the one question a user asks the package about itself. Nothing catches
that by testing behaviour, because every function works.
"""
import pathlib

import pytest

import numguard

try:
    import tomllib
except ModuleNotFoundError:  # py3.10
    tomllib = pytest.importorskip("tomli", reason="needs tomllib (3.11+) or tomli")

PYPROJECT = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"


def _declared_version():
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def test_version_matches_pyproject():
    assert numguard.__version__ == _declared_version(), (
        f"numguard.__version__ is {numguard.__version__} but pyproject ships "
        f"{_declared_version()} — the package is telling users the wrong thing about itself"
    )


def test_all_is_declared_and_non_empty():
    assert getattr(numguard, "__all__", None), "numguard declares no __all__"


@pytest.mark.parametrize("name", sorted(numguard.__all__))
def test_every_promised_name_exists(name):
    assert hasattr(numguard, name), f"__all__ promises {name}, which the package does not have"
