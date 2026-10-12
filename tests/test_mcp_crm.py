"""Automated tests for the RevOps_Astra MCP Server and Client."""

import asyncio
import unittest

from mcp_server.client import RevOpsMCPClient


class MCPCRMTests(unittest.TestCase):
    """Test suite for MCP CRM tools and ClientSession interactions."""

    @classmethod
    def setUpClass(cls):
        cls.client = RevOpsMCPClient()

    def test_1_server_starts_and_discovers_tools(self):
        """Verify the MCP server starts and exposes expected tools with descriptions."""
        tools = asyncio.run(self.client.discover_tools())
        tool_names = [t["name"] for t in tools]

        self.assertIn("get_customer_record", tool_names)
        self.assertIn("list_customer_records", tool_names)
        self.assertIn("update_customer_record", tool_names)

        # Ensure descriptions and input schemas are defined
        for tool in tools:
            self.assertTrue(len(tool["description"]) > 10)
            self.assertIsNotNone(tool["input_schema"])

    def test_2_get_customer_record_valid(self):
        """Verify read-only retrieval of a valid customer record."""
        res = asyncio.run(self.client.get_customer("ACC-001"))
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("account_id"), "ACC-001")

        customer = res.get("customer", {})
        self.assertIn("company_name", customer)
        self.assertIn("industry", customer)
        self.assertIn("health_score", customer)
        self.assertIn("owner_email", customer)
        self.assertIn("customer_status", customer)

    def test_3_get_customer_record_invalid_inputs(self):
        """Verify input validation on bad formats and non-existent IDs."""
        # Malformed ID
        res_bad_format = asyncio.run(self.client.get_customer("INVALID_ID"))
        self.assertFalse(res_bad_format.get("success"))
        self.assertIn("Invalid account_id format", res_bad_format.get("error", ""))

        # Empty ID
        res_empty = asyncio.run(self.client.get_customer("   "))
        self.assertFalse(res_empty.get("success"))
        self.assertIn("non-empty string", res_empty.get("error", ""))

        # Non-existent ID with valid format
        res_not_found = asyncio.run(self.client.get_customer("ACC-999"))
        self.assertFalse(res_not_found.get("success"))
        self.assertIn("not found", res_not_found.get("error", "").lower())

    def test_4_list_customer_records_and_pagination(self):
        """Verify read-only paginated listing of customer records."""
        res = asyncio.run(self.client.list_customers(limit=5, offset=0))
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("returned_count"), 5)
        self.assertEqual(len(res.get("customers", [])), 5)
        self.assertGreater(res.get("total_records", 0), 5)

        # Invalid limits
        res_bad_limit = asyncio.run(self.client.list_customers(limit=0))
        self.assertFalse(res_bad_limit.get("success"))
        self.assertIn("limit", res_bad_limit.get("error", "").lower())

        res_excessive_limit = asyncio.run(self.client.list_customers(limit=51))
        self.assertFalse(res_excessive_limit.get("success"))
        self.assertIn("limit", res_excessive_limit.get("error", "").lower())

    def test_5_update_customer_record_rejects_unallowlisted_fields(self):
        """Verify update tool rejects fields not on the strict allowlist."""
        res = asyncio.run(
            self.client.update_customer("ACC-001", {"annual_revenue": 1000000})
        )
        self.assertFalse(res.get("success"))
        self.assertIn("Unauthorized fields detected", res.get("error", ""))
        self.assertIn("annual_revenue", res.get("error", ""))

    def test_6_update_customer_record_enforces_action_queue_approval(self):
        """Verify update tool validates fields and enforces Action Queue approval requirement."""
        res = asyncio.run(
            self.client.update_customer(
                "ACC-001",
                {"status": "At Risk", "notes": "Customer reported support friction."},
            )
        )
        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("status"), "BLOCKED_PENDING_APPROVAL")
        self.assertTrue(res.get("requires_approval"))
        self.assertEqual(res.get("safety_policy"), "NO_DIRECT_EXECUTION")
        self.assertIn("Action Queue", res.get("message", ""))
        self.assertEqual(res.get("validated_updates", {}).get("status"), "At Risk")


if __name__ == "__main__":
    unittest.main()
