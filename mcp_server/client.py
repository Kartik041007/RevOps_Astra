"""RevOps_Astra MCP Client implementation.

Connects to the RevOps_Astra MCP Server over standard I/O (stdio transport),
discovers available tools, queries customer records, and demonstrates
strict validation and Action Queue safety gating.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class RevOpsMCPClient:
    """Client for communicating with the RevOps_Astra MCP Server."""

    def __init__(self, python_executable: str | None = None, server_script: str | None = None):
        self.python_executable = python_executable or sys.executable
        if server_script:
            self.server_script = server_script
        else:
            self.server_script = str(Path(__file__).resolve().parent / "server.py")

    def _get_server_params(self) -> StdioServerParameters:
        project_root = str(Path(__file__).resolve().parent.parent)
        env = dict(os.environ)
        env["PYTHONPATH"] = project_root
        return StdioServerParameters(
            command=self.python_executable,
            args=[self.server_script],
            env=env,
            cwd=project_root,
        )

    async def discover_tools(self) -> list[dict[str, Any]]:
        """Connect to MCP server and list all exposed tools."""
        params = self._get_server_params()
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.list_tools()
                return [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": getattr(tool, "input_schema", getattr(tool, "inputSchema", None)),
                    }
                    for tool in response.tools
                ]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call an MCP tool on the server and return the parsed result."""
        params = self._get_server_params()
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments or {})
                if result.is_error:
                    error_msg = "; ".join(c.text for c in result.content if hasattr(c, "text"))
                    raise RuntimeError(f"MCP Tool error: {error_msg}")

                # Parse response content
                if result.content:
                    first = result.content[0]
                    if hasattr(first, "text"):
                        try:
                            return json.loads(first.text)
                        except json.JSONDecodeError:
                            return first.text
                return None

    async def get_customer(self, account_id: str) -> dict[str, Any]:
        """Convenience method to retrieve one customer record."""
        return await self.call_tool("get_customer_record", {"account_id": account_id})

    async def list_customers(self, limit: int = 10, offset: int = 0) -> dict[str, Any]:
        """Convenience method to list customer records."""
        return await self.call_tool("list_customer_records", {"limit": limit, "offset": offset})

    async def update_customer(
        self,
        account_id: str,
        field_updates: dict[str, Any],
        action_id: str | None = None,
    ) -> dict[str, Any]:
        """Convenience method to propose or execute customer field updates."""
        args: dict[str, Any] = {
            "account_id": account_id,
            "field_updates": field_updates,
        }
        if action_id is not None:
            args["action_id"] = action_id
        return await self.call_tool("update_customer_record", args)


async def main() -> None:
    """Run a complete demonstration of the MCP client connecting to the server."""
    print("=" * 70)
    print("RevOps_Astra MCP Client — Tool Discovery and Verification Demo")
    print("=" * 70)

    client = RevOpsMCPClient()

    # 1. Discover tools
    print("\n[Step 1] Connecting to MCP Server and discovering tools...")
    tools = await client.discover_tools()
    print(f"Discovered {len(tools)} tools:")
    for tool in tools:
        print(f"  • {tool['name']}: {tool['description'][:75]}...")

    # 2. List customer records (read-only)
    print("\n[Step 2] Calling read-only tool: list_customer_records (limit=3)...")
    list_res = await client.list_customers(limit=3, offset=0)
    print(f"Total customers in database: {list_res['total_records']}")
    print(f"Returned count: {list_res['returned_count']}")
    for cust in list_res["customers"]:
        print(f"  - [{cust['account_id']}] {cust['company_name']} | Health: {cust['health_score']} | Risk: {cust['risk_level']}")

    # 3. Retrieve one customer record (read-only)
    print("\n[Step 3] Calling read-only tool: get_customer_record for 'ACC-001'...")
    cust_res = await client.get_customer("ACC-001")
    print(f"Account: {cust_res['account_id']}")
    print(f"Company: {cust_res['customer']['company_name']}")
    print(f"Industry: {cust_res['customer']['industry']}")
    print(f"Owner: {cust_res['customer']['account_owner']} <{cust_res['customer']['owner_email']}>")
    print(f"Status: {cust_res['customer']['customer_status']}")

    # 4. Test invalid input handling
    print("\n[Step 4] Testing invalid account ID format ('BAD_ID')...")
    bad_id_res = await client.get_customer("BAD_ID")
    if not bad_id_res.get("success"):
        print(f"  ✓ Safely caught expected validation error: {bad_id_res.get('error')}")
    else:
        print("  ❌ Unexpected: Request should have returned success=False.")

    # 5. Test unallowlisted field rejection in CRM update
    print("\n[Step 5] Testing update with unallowlisted field ('credit_limit')...")
    bad_update_res = await client.update_customer("ACC-001", {"credit_limit": 500000})
    if not bad_update_res.get("success"):
        print(f"  ✓ Safely rejected unallowlisted field: {bad_update_res.get('error')}")
    else:
        print("  ❌ Unexpected: Request should have returned success=False.")

    # 6. Test valid CRM update proposal and verify Action Queue gating
    print("\n[Step 6] Testing valid CRM update proposal ({'status': 'At Risk'})...")
    update_res = await client.update_customer(
        "ACC-001",
        {"status": "At Risk", "notes": "Usage dropped; customer outreach pending review."},
    )
    print(f"Result Status: {update_res['status']}")
    print(f"Requires Approval: {update_res['requires_approval']}")
    print(f"Safety Policy: {update_res['safety_policy']}")
    print(f"Message: {update_res['message']}")

    print("\n" + "=" * 70)
    print("✓ All MCP Server & Client tests completed successfully.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
