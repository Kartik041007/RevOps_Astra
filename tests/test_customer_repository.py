"""Comprehensive automated tests for services/customer_repository.py,
seed_customer_accounts script, and MySQL MCP provider integration.
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from mcp_server.server import (
    ALLOWED_STATUSES,
    ALLOWED_UPDATE_FIELDS,
    get_crm_provider_mode,
    get_customer_record as mcp_get_customer,
    list_customer_records as mcp_list_customers,
    update_customer_record as mcp_update_customer,
)
from scripts.seed_customer_accounts import seed_customer_accounts
from services.customer_repository import (
    CustomerNotFoundError,
    CustomerRepository,
)


class FakeCursor:
    """Mock database cursor simulating MySQL dictionary cursor."""

    def __init__(self, conn: FakeConnection):
        self.conn = conn
        self.closed = False
        self.last_query = ""
        self.last_params: tuple = ()
        self._fetchone_result = None
        self._fetchall_result = []

    def execute(self, sql: str, params: tuple = ()):
        self.last_query = sql.strip()
        self.last_params = params
        self.conn.executed_queries.append((self.last_query, params))

        norm_sql = " ".join(sql.split())

        # Simulate SELECT queries based on connection mock data
        if "FROM customer_accounts WHERE account_id = %s" in norm_sql:
            acc_id = params[0] if params else None
            record = self.conn.accounts.get(acc_id)
            if record:
                self._fetchone_result = dict(record)
            else:
                self._fetchone_result = None
        elif "COUNT(*) AS total FROM customer_accounts" in norm_sql:
            self._fetchone_result = {"total": len(self.conn.accounts)}
        elif "SELECT account_id FROM customer_accounts" in norm_sql and "WHERE" not in norm_sql:
            self._fetchall_result = [{"account_id": aid} for aid in self.conn.accounts.keys()]
        elif "SELECT account_id, company_name" in norm_sql and "ORDER BY account_id" in norm_sql:
            limit, offset = params
            acc_list = sorted(self.conn.accounts.values(), key=lambda x: x["account_id"])
            sliced = acc_list[offset : offset + limit]
            self._fetchall_result = [dict(a) for a in sliced]
        elif "UPDATE customer_accounts SET" in norm_sql:
            acc_id = params[-1]
            if acc_id in self.conn.accounts:
                target = self.conn.accounts[acc_id]
                set_part = norm_sql.split("SET")[1].split("WHERE")[0]
                cols = [c.split("=")[0].strip() for c in set_part.split(",")]
                for col, val in zip(cols, params[:-1]):
                    target[col] = val
        elif "INSERT INTO audit_logs" in norm_sql:
            self.conn.audit_logs.append({
                "account_id": params[0],
                "action_id": params[1],
                "actor": params[2],
                "changes": params[3],
                "message": params[4],
            })
        elif "INSERT INTO customer_accounts" in sql:
            self.conn.accounts[params[0]] = {
                "account_id": params[0],
                "company_name": params[1],
                "industry": params[2],
                "region": params[3],
                "account_owner": params[4],
                "owner_email": params[5],
                "customer_status": params[6],
                "plan_tier": params[7],
                "arr_usd": params[8],
                "health_score": params[9],
                "notes": params[10],
                "last_contact_date": params[11],
            }

    def fetchone(self):
        return self._fetchone_result

    def fetchall(self):
        return self._fetchall_result

    def close(self):
        self.closed = True


class FakeConnection:
    """Mock MySQL connection managing transactions and table state."""

    def __init__(self, initial_accounts: dict | None = None):
        self.accounts = initial_accounts or {}
        self.audit_logs = []
        self.executed_queries = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self, dictionary: bool = True):
        return FakeCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class CustomerRepositoryTests(unittest.TestCase):
    """Test suite for services/customer_repository.py."""

    def setUp(self):
        self.sample_account = {
            "account_id": "ACC-001",
            "company_name": "Northstar Components",
            "industry": "Manufacturing",
            "region": "West",
            "account_owner": "Kabir Singh",
            "owner_email": "kabir.singh@revops-mock.internal",
            "customer_status": "Active",
            "plan_tier": "Enterprise",
            "arr_usd": Decimal("9747.17"),
            "health_score": 30,
            "notes": "Usage declined 18.4%.",
            "last_contact_date": "2026-09-25",
            "created_at": "2026-09-01 10:00:00",
            "updated_at": "2026-09-25 12:00:00",
        }
        self.fake_conn = FakeConnection(initial_accounts={"ACC-001": self.sample_account})
        self.repo = CustomerRepository(connection_factory=lambda: self.fake_conn)

    def test_get_customer_record_success(self):
        """Retrieve existing customer record with synchronized status and float arr_usd."""
        res = self.repo.get_customer_record("ACC-001")
        self.assertIsNotNone(res)
        self.assertEqual(res["account_id"], "ACC-001")
        self.assertEqual(res["company_name"], "Northstar Components")
        self.assertEqual(res["customer_status"], "Active")
        self.assertEqual(res["status"], "Active")
        self.assertIsInstance(res["arr_usd"], float)
        self.assertAlmostEqual(res["arr_usd"], 9747.17)
        self.assertTrue(self.fake_conn.closed)

    def test_get_customer_record_not_found(self):
        """Return None when requested account ID does not exist."""
        res = self.repo.get_customer_record("ACC-999")
        self.assertIsNone(res)
        self.assertTrue(self.fake_conn.closed)

    def test_get_customer_record_invalid_inputs(self):
        """Reject malformed or empty account IDs with ValueError."""
        with self.assertRaises(ValueError):
            self.repo.get_customer_record("")
        with self.assertRaises(ValueError):
            self.repo.get_customer_record("INVALID_FORMAT")
        with self.assertRaises(ValueError):
            self.repo.get_customer_record("ACC-1")

    def test_list_customer_records_pagination(self):
        """List customer records with limit and offset."""
        # Add a second account
        acc2 = dict(self.sample_account)
        acc2["account_id"] = "ACC-002"
        self.fake_conn.accounts["ACC-002"] = acc2

        records, total = self.repo.list_customer_records(limit=1, offset=0)
        self.assertEqual(total, 2)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["account_id"], "ACC-001")

        records2, total2 = self.repo.list_customer_records(limit=1, offset=1)
        self.assertEqual(len(records2), 1)
        self.assertEqual(records2[0]["account_id"], "ACC-002")

    def test_list_customer_records_invalid_pagination(self):
        """Reject invalid pagination limits and offsets."""
        with self.assertRaises(ValueError):
            self.repo.list_customer_records(limit=0)
        with self.assertRaises(ValueError):
            self.repo.list_customer_records(limit=51)
        with self.assertRaises(ValueError):
            self.repo.list_customer_records(limit=10, offset=-1)

    def test_update_customer_record_success_and_audit(self):
        """Update allowlisted fields, commit transaction, and create audit log entry."""
        updates = {
            "status": "At Risk",
            "notes": "Updated executive outreach notes.",
            "owner_email": "new.owner@example.com",
        }
        res = self.repo.update_customer_record(
            account_id="ACC-001",
            field_updates=updates,
            action_id="11111111-2222-3333-4444-555555555555",
            actor="TEST_USER",
        )
        self.assertIsNotNone(res)
        self.assertEqual(res["customer_status"], "At Risk")
        self.assertEqual(res["status"], "At Risk")
        self.assertEqual(res["notes"], "Updated executive outreach notes.")
        self.assertEqual(res["owner_email"], "new.owner@example.com")
        self.assertTrue(self.fake_conn.committed)

        # Verify audit log was recorded
        self.assertEqual(len(self.fake_conn.audit_logs), 1)
        audit = self.fake_conn.audit_logs[0]
        self.assertEqual(audit["account_id"], "ACC-001")
        self.assertEqual(audit["action_id"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(audit["actor"], "TEST_USER")
        changes = json.loads(audit["changes"])
        self.assertIn("customer_status", changes)
        self.assertEqual(changes["customer_status"]["new"], "At Risk")

    def test_update_customer_record_rejects_unallowlisted_fields(self):
        """Reject field updates outside approved CRM fields."""
        with self.assertRaises(ValueError) as ctx:
            self.repo.update_customer_record(
                account_id="ACC-001",
                field_updates={"arr_usd": 999999.0},
            )
        self.assertIn("Unauthorized fields", str(ctx.exception))

    def test_update_customer_record_validates_email_and_status(self):
        """Reject invalid email formats and unapproved statuses."""
        with self.assertRaises(ValueError):
            self.repo.update_customer_record("ACC-001", {"owner_email": "not-an-email"})

        with self.assertRaises(ValueError):
            self.repo.update_customer_record("ACC-001", {"status": "NonexistentStatus"})

    def test_update_customer_record_not_found(self):
        """Raise CustomerNotFoundError when updating nonexistent account."""
        with self.assertRaises(CustomerNotFoundError):
            self.repo.update_customer_record("ACC-999", {"notes": "Valid note"})

    def test_update_customer_record_rollback_on_failure(self):
        """Ensure connection rollback is executed if an exception occurs during update."""
        faulty_conn = FakeConnection(initial_accounts={"ACC-001": self.sample_account})

        def faulty_execute(sql, params=()):
            if "UPDATE customer_accounts" in sql:
                raise RuntimeError("Simulated DB lock conflict")
            FakeCursor(faulty_conn).execute(sql, params)

        with mock.patch.object(FakeCursor, "execute", side_effect=faulty_execute):
            repo = CustomerRepository(connection_factory=lambda: faulty_conn)
            with self.assertRaises(RuntimeError):
                repo.update_customer_record("ACC-001", {"notes": "Test fail"})
            self.assertTrue(faulty_conn.rolled_back)


class SeedCustomerAccountsTests(unittest.TestCase):
    """Test suite for scripts/seed_customer_accounts.py."""

    def test_seed_dry_run(self):
        """Dry-run mode reads and validates baseline CSV without calling DB."""
        counts = seed_customer_accounts(dry_run=True)
        self.assertGreater(counts["total"], 0)
        self.assertEqual(counts["inserted"], counts["total"])
        self.assertEqual(counts["failed"], 0)

    def test_seed_idempotency_does_not_overwrite(self):
        """Seeding into DB does not overwrite existing records on rerun."""
        fake_conn = FakeConnection(initial_accounts={})
        counts1 = seed_customer_accounts(dry_run=False, connection_factory=lambda: fake_conn)
        self.assertGreater(counts1["inserted"], 0)
        self.assertEqual(counts1["skipped"], 0)

        # Rerun seeding against same connection with populated accounts
        fake_conn2 = FakeConnection(initial_accounts=dict(fake_conn.accounts))
        counts2 = seed_customer_accounts(dry_run=False, connection_factory=lambda: fake_conn2)
        self.assertEqual(counts2["inserted"], 0)
        self.assertEqual(counts2["skipped"], counts1["inserted"])


class MCPProviderModeTests(unittest.TestCase):
    """Test suite for MCP server CRM provider selection and error behavior."""

    def test_provider_mode_default_is_mock(self):
        """Default provider mode is mock when CRM_PROVIDER is unset."""
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_crm_provider_mode(), "mock")

    def test_provider_mode_explicit_mysql(self):
        """Provider mode switches to mysql when CRM_PROVIDER=mysql."""
        with mock.patch.dict(os.environ, {"CRM_PROVIDER": "mysql"}, clear=True):
            self.assertEqual(get_crm_provider_mode(), "mysql")

    def test_provider_mode_fallback_on_unrecognized_value(self):
        """Unrecognized CRM_PROVIDER string safely defaults to mock."""
        with mock.patch.dict(os.environ, {"CRM_PROVIDER": "unknown_provider"}, clear=True):
            self.assertEqual(get_crm_provider_mode(), "mock")

    def test_mysql_mode_error_when_db_unavailable(self):
        """When CRM_PROVIDER=mysql and DB is unavailable, return clear error without silent mock fallback."""
        with mock.patch.dict(os.environ, {"CRM_PROVIDER": "mysql"}, clear=True):
            with mock.patch("mcp_server.server._get_mysql_repo") as mock_repo_getter:
                mock_repo = mock.MagicMock()
                mock_repo.get_customer_record.side_effect = ConnectionError("Could not connect to MySQL server")
                mock_repo_getter.return_value = mock_repo

                res = mcp_get_customer("ACC-001")
                self.assertFalse(res["success"])
                self.assertEqual(res["provider"], "mysql")
                self.assertIn("MySQL CRM provider error", res["error"])

    def test_mysql_mode_delegates_to_repository(self):
        """In MySQL mode, MCP tools query CustomerRepository and return provider receipt."""
        with mock.patch.dict(os.environ, {"CRM_PROVIDER": "mysql"}, clear=True):
            with mock.patch("mcp_server.server._get_mysql_repo") as mock_repo_getter:
                mock_repo = mock.MagicMock()
                mock_repo.get_customer_record.return_value = {
                    "account_id": "ACC-001",
                    "company_name": "MySQL Backed Co",
                    "customer_status": "Active",
                    "status": "Active",
                    "owner_email": "owner@mysql.internal",
                    "notes": "MySQL verified notes",
                }
                mock_repo_getter.return_value = mock_repo

                res = mcp_get_customer("ACC-001")
                self.assertTrue(res["success"])
                self.assertEqual(res["provider"], "mysql")
                self.assertEqual(res["customer"]["company_name"], "MySQL Backed Co")

    def test_mysql_mode_update_executes_with_action_id(self):
        """In MySQL mode, update_customer_record with action_id updates repo and returns mysql receipt."""
        with mock.patch.dict(os.environ, {"CRM_PROVIDER": "mysql"}, clear=True):
            with mock.patch("mcp_server.server._get_mysql_repo") as mock_repo_getter:
                mock_repo = mock.MagicMock()
                mock_repo.get_customer_record.return_value = {
                    "account_id": "ACC-001",
                    "customer_status": "Active",
                }
                mock_repo.update_customer_record.return_value = {
                    "account_id": "ACC-001",
                    "customer_status": "At Risk",
                    "status": "At Risk",
                    "notes": "Updated via MySQL MCP",
                }
                mock_repo_getter.return_value = mock_repo

                res = mcp_update_customer(
                    account_id="ACC-001",
                    field_updates={"status": "At Risk", "notes": "Updated via MySQL MCP"},
                    action_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                )
                self.assertTrue(res["success"])
                self.assertEqual(res["status"], "EXECUTED")
                self.assertEqual(res["provider"], "mysql")
                self.assertEqual(res["mode"], "mysql")
                self.assertEqual(res["integration"], "mcp_mysql_crm")

    def test_live_mysql_mode_reads_acc_001_matching_db(self):
        """Read ACC-001 through MCP tool in MySQL mode and verify it matches database record."""
        from services.db import get_db_connection
        try:
            conn = get_db_connection()
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT * FROM customer_accounts WHERE account_id = 'ACC-001'")
            raw_db_row = cur.fetchone()
            cur.close()
            conn.close()
        except Exception as exc:
            self.skipTest(f"Live MySQL database is not accessible: {exc}")

        if not raw_db_row:
            self.skipTest("Live MySQL database does not contain ACC-001.")

        # Read ACC-001 via MCP tool under CRM_PROVIDER=mysql
        with mock.patch.dict(os.environ, {"CRM_PROVIDER": "mysql"}, clear=False):
            res = mcp_get_customer("ACC-001")
            self.assertTrue(res.get("success"), f"MCP get_customer failed: {res}")
            self.assertEqual(res.get("provider"), "mysql")
            cust = res.get("customer", {})
            self.assertEqual(cust.get("account_id"), "ACC-001")
            self.assertEqual(cust.get("company_name"), raw_db_row["company_name"])
            self.assertEqual(cust.get("customer_status"), raw_db_row["customer_status"])
            self.assertEqual(cust.get("status"), raw_db_row["customer_status"])
            self.assertEqual(cust.get("owner_email"), raw_db_row["owner_email"])
            self.assertAlmostEqual(float(cust.get("arr_usd")), float(raw_db_row["arr_usd"]))

    def test_live_mysql_mcp_client_subprocess_reads_acc_001(self):
        """Read ACC-001 through live subprocess MCP client in MySQL mode."""
        from mcp_server.client import RevOpsMCPClient
        with mock.patch.dict(os.environ, {"CRM_PROVIDER": "mysql"}, clear=False):
            client = RevOpsMCPClient()
            res = asyncio.run(client.get_customer("ACC-001"))
            self.assertTrue(res.get("success"), f"Subprocess MCP get_customer failed: {res}")
            self.assertEqual(res.get("provider"), "mysql")
            cust = res.get("customer", {})
            self.assertEqual(cust.get("account_id"), "ACC-001")
            self.assertEqual(cust.get("customer_status"), "Active")


if __name__ == "__main__":
    unittest.main()
