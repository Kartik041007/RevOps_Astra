"""Centralized MySQL database connection management for RevOps_Astra.

Provides a robust, environment-configurable connection helper built with
mysql-connector-python, maintaining full backward compatibility with local defaults.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import mysql.connector
from mysql.connector.connection import MySQLConnection


def _load_env_file():
    import sys
    if "unittest" in sys.modules:
        return
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val


_load_env_file()

DEFAULT_DB_HOST = "localhost"
DEFAULT_DB_PORT = 3306
DEFAULT_DB_USER = "root"
DEFAULT_DB_PASSWORD = "1234"  # Default local password preserved from existing setup
DEFAULT_DB_NAME = "RevOps_Astra"


def get_db_config() -> dict[str, Any]:
    """Retrieve database connection settings from environment variables with safe local fallbacks."""
    port_str = os.environ.get("REVOPS_DB_PORT", os.environ.get("MYSQL_PORT", str(DEFAULT_DB_PORT)))
    try:
        port = int(port_str)
    except (ValueError, TypeError):
        port = DEFAULT_DB_PORT

    return {
        "host": os.environ.get("REVOPS_DB_HOST", os.environ.get("MYSQL_HOST", DEFAULT_DB_HOST)),
        "port": port,
        "user": os.environ.get("REVOPS_DB_USER", os.environ.get("MYSQL_USER", DEFAULT_DB_USER)),
        "password": os.environ.get("REVOPS_DB_PASSWORD", os.environ.get("MYSQL_PASSWORD", DEFAULT_DB_PASSWORD)),
        "database": os.environ.get("REVOPS_DB_NAME", os.environ.get("MYSQL_DATABASE", DEFAULT_DB_NAME)),
    }


def get_db_connection() -> MySQLConnection:
    """Establish and return a new MySQLConnection using configured credentials.

    Returns:
        mysql.connector.connection.MySQLConnection: An active database connection.
    """
    config = get_db_config()
    return mysql.connector.connect(**config)
