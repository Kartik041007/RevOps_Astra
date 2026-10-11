
"""Test ASTRA AI's local Mock CRM MCP server."""

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parent.parent
SERVER_FILE = ROOT / "ai" / "mock_crm_server.py"

ACTION_ID = "ACT-043431C9D3"
ACCOUNT_ID = "ACC-008"


def print_result(title, result):
    print(f"\n--- {title} ---")

    for item in result.content:
        if hasattr(item, "text"):
            print(item.text)

    if result.isError:
        raise RuntimeError(f"{title} failed.")


async def main():
    if not SERVER_FILE.exists():
        raise FileNotFoundError(
            f"MCP server not found: {SERVER_FILE}"
        )

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_FILE)],
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}

            print("Connected to ASTRA Mock CRM MCP server.")

            required_tools = {
                "update_customer_after_approval",
                "get_customer_record",
            }

            if not required_tools.issubset(tool_names):
                raise RuntimeError(
                    "Required MCP tools are missing."
                )

            # Execute only the action that was human-approved.
            result = await session.call_tool(
                "update_customer_after_approval",
                arguments={"action_id": ACTION_ID},
            )
            print_result("CRM UPDATE RESULT", result)

            # Read the customer again to verify the saved values.
            result = await session.call_tool(
                "get_customer_record",
                arguments={"account_id": ACCOUNT_ID},
            )
            print_result("VERIFIED CUSTOMER RECORD", result)

            print("\nMCP test completed.")


if __name__ == "__main__":
    asyncio.run(main())
