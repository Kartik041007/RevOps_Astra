"""MySQL-backed customer repository for RevOps_Astra CRM.

Implements database access for customer accounts and audit logs using
services.db and mysql-connector-python without requiring SQLAlchemy or ORMs.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

import mysql.connector
from services.db import get_db_connection

logger = logging.getLogger(__name__)

ACCOUNT_ID_REGEX = re.compile(r"^ACC-\d{3,}$")
EMAIL_REGEX = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")
ALLOWED_STATUSES = {"Active", "At Risk", "Churned", "Paused", "Watchlist"}
ALLOWED_UPDATE_FIELDS = {"owner_email", "status", "customer_status", "notes"}


class CustomerNotFoundError(Exception):
    """Raised when a requested account ID does not exist in the database."""


class CustomerRepository:
    """Repository managing customer records and audit logs in MySQL."""

    def __init__(self, connection_factory: Callable[[], Any] | None = None):
        self.connection_factory = connection_factory or get_db_connection

    def get_customer_record(self, account_id: str) -> dict[str, Any] | None:
        """Retrieve a customer record by account ID.

        Args:
            account_id: Validated account ID string (e.g. 'ACC-001').

        Returns:
            dict with customer fields or None if not found.
        """
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("account_id must be a non-empty string.")

        cleaned_id = account_id.strip()
        if not ACCOUNT_ID_REGEX.match(cleaned_id):
            raise ValueError(f"Invalid account_id format '{cleaned_id}'. Expected format 'ACC-###'.")

        conn = self.connection_factory()
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT account_id, company_name, industry, region, account_owner,
                       owner_email, customer_status, plan_tier, arr_usd,
                       health_score, notes, last_contact_date, created_at, updated_at
                FROM customer_accounts
                WHERE account_id = %s
                """,
                (cleaned_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            return self._format_customer_row(row)
        finally:
            if cursor:
                cursor.close()
            conn.close()

    def list_customer_records(self, limit: int = 10, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        """List customer records with pagination.

        Args:
            limit: Maximum items to return (1..50).
            offset: Zero-based start offset.

        Returns:
            tuple of (list of customer dictionaries, total integer count).
        """
        if not isinstance(limit, int) or limit < 1 or limit > 50:
            raise ValueError("limit must be an integer between 1 and 50.")
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer.")

        conn = self.connection_factory()
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT COUNT(*) AS total FROM customer_accounts")
            total_row = cursor.fetchone()
            total_count = int(total_row["total"]) if total_row else 0

            cursor.execute(
                """
                SELECT account_id, company_name, industry, region, account_owner,
                       owner_email, customer_status, plan_tier, arr_usd,
                       health_score, notes, last_contact_date, created_at, updated_at
                FROM customer_accounts
                ORDER BY account_id ASC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            rows = cursor.fetchall() or []
            return [self._format_customer_row(r) for r in rows], total_count
        finally:
            if cursor:
                cursor.close()
            conn.close()

    def update_customer_record(
        self,
        account_id: str,
        field_updates: dict[str, Any],
        action_id: str | None = None,
        actor: str = "MCP_SERVER",
    ) -> dict[str, Any]:
        """Update allowlisted fields of a customer record within an explicit transaction.

        Args:
            account_id: Account ID to update.
            field_updates: Dictionary containing allowlisted fields.
            action_id: Optional UUID of the approved Action Queue action.
            actor: Audit actor label (default 'MCP_SERVER').

        Returns:
            dict containing updated customer record.
        """
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("account_id must be a non-empty string.")

        cleaned_id = account_id.strip()
        if not ACCOUNT_ID_REGEX.match(cleaned_id):
            raise ValueError(f"Invalid account_id format '{cleaned_id}'.")

        if not isinstance(field_updates, dict) or not field_updates:
            raise ValueError("field_updates must be a non-empty dictionary.")

        # Validate allowlisted fields
        unauthorized = set(field_updates.keys()) - ALLOWED_UPDATE_FIELDS
        if unauthorized:
            raise ValueError(
                f"Unauthorized fields: {sorted(unauthorized)}. "
                f"Allowed fields: {sorted(ALLOWED_UPDATE_FIELDS)}."
            )

        validated: dict[str, Any] = {}
        if "owner_email" in field_updates:
            email = field_updates["owner_email"]
            if not isinstance(email, str) or not EMAIL_REGEX.match(email.strip()):
                raise ValueError(f"Invalid owner_email value '{email}'.")
            validated["owner_email"] = email.strip()

        # Handle status / customer_status normalization
        status_val = field_updates.get("customer_status") or field_updates.get("status")
        if status_val is not None:
            if not isinstance(status_val, str) or status_val.strip() not in ALLOWED_STATUSES:
                raise ValueError(f"Invalid status '{status_val}'. Allowed: {sorted(ALLOWED_STATUSES)}.")
            validated["customer_status"] = status_val.strip()

        if "notes" in field_updates:
            notes_val = field_updates["notes"]
            if not isinstance(notes_val, str) or len(notes_val) > 1000:
                raise ValueError("notes must be a string of at most 1000 characters.")
            validated["notes"] = notes_val.strip()

        if not validated:
            raise ValueError("No valid updates provided.")

        conn = self.connection_factory()
        cursor = None
        try:
            cursor = conn.cursor(dictionary=True)

            # Check existence and lock row for update
            cursor.execute(
                "SELECT account_id, customer_status, owner_email, notes FROM customer_accounts WHERE account_id = %s FOR UPDATE",
                (cleaned_id,),
            )
            existing = cursor.fetchone()
            if not existing:
                raise CustomerNotFoundError(f"Customer account '{cleaned_id}' not found.")

            # Construct dynamic UPDATE query with parameterized values
            set_clauses = []
            values = []
            for col, val in validated.items():
                set_clauses.append(f"{col} = %s")
                values.append(val)
            values.append(cleaned_id)

            sql_update = f"UPDATE customer_accounts SET {', '.join(set_clauses)} WHERE account_id = %s"
            cursor.execute(sql_update, tuple(values))

            # Record audit entry in audit_logs
            changes = {k: {"old": existing.get(k), "new": v} for k, v in validated.items()}
            cursor.execute(
                """
                INSERT INTO audit_logs (account_id, action_id, entity_type, event_type, actor, changes, message)
                VALUES (%s, %s, 'customer_account', 'CRM_RECORD_UPDATED', %s, %s, %s)
                """,
                (
                    cleaned_id,
                    action_id,
                    actor,
                    json.dumps(changes),
                    f"Updated fields: {', '.join(sorted(validated.keys()))} for {cleaned_id}",
                ),
            )

            conn.commit()

            # Retrieve fresh updated record
            cursor.execute(
                """
                SELECT account_id, company_name, industry, region, account_owner,
                       owner_email, customer_status, plan_tier, arr_usd,
                       health_score, notes, last_contact_date, created_at, updated_at
                FROM customer_accounts
                WHERE account_id = %s
                """,
                (cleaned_id,),
            )
            fresh = cursor.fetchone()
            return self._format_customer_row(fresh)
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            if cursor:
                cursor.close()
            conn.close()

    @staticmethod
    def _format_customer_row(row: dict[str, Any]) -> dict[str, Any]:
        """Format a MySQL dictionary row into a consistent customer record."""
        record = dict(row)
        # Harmonize status / customer_status
        c_status = record.get("customer_status") or "Active"
        record["customer_status"] = c_status
        record["status"] = c_status

        # Format decimal arr_usd to float
        if record.get("arr_usd") is not None:
            try:
                record["arr_usd"] = float(record["arr_usd"])
            except (ValueError, TypeError):
                record["arr_usd"] = 0.0

        # Format date objects to ISO string
        if record.get("last_contact_date") is not None:
            record["last_contact_date"] = str(record["last_contact_date"])

        return record
