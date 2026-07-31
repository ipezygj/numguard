"""Every declared console script must actually resolve to something callable.

`[project.scripts]` is a string, and nothing checks it until a user installs the
package and runs the command. A rename in the module then fails only in the
packaged install — never in the test suite, which imports the module directly.
That gap is not hypothetical: three of my MCP listings failed at launch this way,
with the import error visible only in a build log.

This walks the declared entry points the way the generated wrapper does — import
the module, follow the dotted attribute path, check it is callable.
"""
import importlib
import pathlib

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # py3.10
    tomllib = pytest.importorskip("tomli", reason="needs tomllib (3.11+) or tomli")

PYPROJECT = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"


def _scripts():
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f).get("project", {}).get("scripts", {})


def test_at_least_one_script_declared():
    assert _scripts(), "pyproject declares no console scripts — did the table get renamed?"


@pytest.mark.parametrize("name,target", sorted(_scripts().items()))
def test_console_script_resolves(name, target):
    module_path, _, attr_path = target.partition(":")
    assert attr_path, f"{name} = {target!r} has no ':attr' part"

    module = importlib.import_module(module_path)

    obj = module
    walked = module_path
    for part in attr_path.split("."):
        assert hasattr(obj, part), f"{name}: {walked} has no attribute {part!r}"
        obj = getattr(obj, part)
        walked = f"{walked}.{part}"

    assert callable(obj), f"{name} points at {walked}, which is not callable"
