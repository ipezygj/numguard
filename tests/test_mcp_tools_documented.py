"""Every MCP tool must be findable in the README.

Eleven of the thirty-four were not — the whole on-chain and agent-verification
family. A tool an agent cannot find is a tool it cannot call, and no behaviour
test notices, because all eleven work.

The decorator here carries arguments (`@mcp.tool(annotations=_ann(...))`), and a
naive `@mcp\.tool\([^)]*\)` pattern stops at the first inner paren and matches
nothing — which is how an earlier sweep reported "0 tools, none undocumented"
and looked like an all-clear. This takes the first `def` after each decorator
instead.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER = ROOT / "numguard" / "mcp_server.py"
README = ROOT / "README.md"


def _tools():
    src = SERVER.read_text(encoding="utf-8")
    found = []
    for chunk in src.split("@mcp.tool")[1:]:
        m = re.search(r"\ndef (\w+)\(", chunk)
        if m:
            found.append(m.group(1))
    return sorted(set(found))


def test_the_parser_finds_tools_at_all():
    """Guards the guard: a regex that matches nothing would pass every other test here."""
    tools = _tools()
    assert len(tools) > 10, f"only {len(tools)} tools found — the decorator shape probably changed"


@pytest.mark.parametrize("tool", _tools())
def test_tool_is_named_in_the_readme(tool):
    assert tool in README.read_text(encoding="utf-8"), (
        f"MCP tool {tool} appears nowhere in README.md — an agent choosing a tool reads that, "
        "so an unlisted tool is an uncallable one"
    )
