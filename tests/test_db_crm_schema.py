"""Unit and integration tests for services/db.py and CRM schema migrations."""

import os
import unittest
from pathlib import Path
from unittest import mock

from services.db import (
    DEFAULT_DB_HOST,
    DEFAULT_DB_NAME,
    DEFAULT_DB_PASSWORD,
    DEFAULT_DB_PORT,
    DEFAULT_DB_USER,
    get_db_config,
    get_db_connection,
)


class DatabaseConfigTests(unittest.TestCase):
    """Test suite for database environment configuration and connection fallback."""

    def test_default_config_fallback(self):
        """Verify defaults are applied when environment variables are omitted."""
        with mock.patch.dict(os.environ, {}, clear=True):
            config = get_db_config()
            self.assertEqual(config["host"], DEFAULT_DB_HOST)
            self.assertEqual(config["port"], DEFAULT_DB_PORT)
            self.assertEqual(config["user"], DEFAULT_DB_USER)
            self.assertEqual(config["password"], DEFAULT_DB_PASSWORD)
            self.assertEqual(config["database"], DEFAULT_DB_NAME)

    def test_custom_environment_variables(self):
        """Verify custom environment variables override default values."""
        custom_env = {
            "REVOPS_DB_HOST": "db.internal.network",
            "REVOPS_DB_PORT": "3307",
            "REVOPS_DB_USER": "revops_app",
            "REVOPS_DB_PASSWORD": "secure-cluster-pwd",
            "REVOPS_DB_NAME": "RevOps_Astra_Prod",
        }
        with mock.patch.dict(os.environ, custom_env, clear=True):
            config = get_db_config()
            self.assertEqual(config["host"], "db.internal.network")
            self.assertEqual(config["port"], 3307)
            self.assertEqual(config["user"], "revops_app")
            self.assertEqual(config["password"], "secure-cluster-pwd")
            self.assertEqual(config["database"], "RevOps_Astra_Prod")

    def test_invalid_port_fallback(self):
        """Verify fallback to default port if port string is malformed."""
        with mock.patch.dict(os.environ, {"REVOPS_DB_PORT": "invalid-port"}, clear=True):
            config = get_db_config()
            self.assertEqual(config["port"], DEFAULT_DB_PORT)

    @mock.patch("services.db.mysql.connector.connect")
    def test_get_db_connection_delegates_to_connector(self, mock_connect):
        """Verify get_db_connection invokes mysql.connector.connect with resolved config."""
        mock_connect.return_value = mock.MagicMock()
        conn = get_db_connection()
        self.assertIsNotNone(conn)
        mock_connect.assert_called_once()
        called_kwargs = mock_connect.call_args.kwargs
        self.assertIn("host", called_kwargs)
        self.assertIn("user", called_kwargs)
        self.assertIn("database", called_kwargs)


class CRMSchemaMigrationFileTests(unittest.TestCase):
    """Test suite to validate SQL migration definitions for Phase 1 CRM tables."""

    @classmethod
    def setUpClass(cls):
        cls.migration_path = Path(__file__).resolve().parent.parent / "migrations" / "002_create_crm_tables.sql"
        cls.rollback_path = Path(__file__).resolve().parent.parent / "migrations" / "002_create_crm_tables_rollback.sql"

    def test_migration_file_exists_and_contains_required_tables(self):
        """Verify 002_create_crm_tables.sql defines the exact five required tables."""
        self.assertTrue(self.migration_path.exists())
        sql = self.migration_path.read_text(encoding="utf-8")

        required_tables = [
            "customer_accounts",
            "risk_assessments",
            "follow_ups",
            "proposed_actions",
            "audit_logs",
        ]

        for table in required_tables:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)

    def test_schema_syntax_and_key_constraints(self):
        """Verify schema defines primary keys, foreign keys, and indexes correctly."""
        sql = self.migration_path.read_text(encoding="utf-8")

        # Primary keys
        self.assertIn("PRIMARY KEY (account_id)", sql)
        self.assertIn("PRIMARY KEY (assessment_id)", sql)
        self.assertIn("PRIMARY KEY (followup_id)", sql)
        self.assertIn("PRIMARY KEY (proposal_id)", sql)
        self.assertIn("PRIMARY KEY (log_id)", sql)

        # Foreign keys to customer_accounts & approval_actions
        self.assertIn("FOREIGN KEY (account_id) REFERENCES customer_accounts(account_id)", sql)
        self.assertIn("FOREIGN KEY (approval_action_id) REFERENCES approval_actions(action_id)", sql)
        self.assertIn("FOREIGN KEY (action_id) REFERENCES approval_actions(action_id)", sql)

    def test_rollback_file_exists_and_contains_drop_statements(self):
        """Verify rollback drops tables cleanly in correct dependency order."""
        self.assertTrue(self.rollback_path.exists())
        sql = self.rollback_path.read_text(encoding="utf-8")

        required_tables = [
            "audit_logs",
            "proposed_actions",
            "follow_ups",
            "risk_assessments",
            "customer_accounts",
        ]

        for table in required_tables:
            self.assertIn(f"DROP TABLE IF EXISTS {table}", sql)


if __name__ == "__main__":
    unittest.main()
