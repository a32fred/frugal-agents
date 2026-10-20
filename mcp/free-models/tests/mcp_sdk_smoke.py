"""Compatibility smoke test using the official MCP Python SDK.

The production server has no runtime dependencies. This script is a dev/CI
check only and exits 0 with a SKIP message when the SDK is not installed.

    python -m pip install mcp
    python mcp/free-models/tests/mcp_sdk_smoke.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server.py"
EXPECTED_TOOLS = ["free_agent", "free_ask", "free_cascade"]


async def main() -> int:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        print("SKIP: official mcp SDK not installed (pip install mcp)")
        return 0

    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            listing = await session.list_tools()

            names = sorted(tool.name for tool in listing.tools)
            assert names == EXPECTED_TOOLS, names
            assert initialized.server_info.name == "free-models"
            assert initialized.protocol_version

            for tool in listing.tools:
                assert tool.input_schema["type"] == "object"
                assert tool.annotations is not None, tool.name

            print(
                "OK: official MCP SDK handshake · "
                f"protocol={initialized.protocol_version} · tools={names}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
