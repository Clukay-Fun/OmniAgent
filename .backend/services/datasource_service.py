"""
Description: Dynamic datasource connection service
Features:
    - Connect to external databases based on assistant config
    - Support PostgreSQL, MySQL, and API datasources
    - Execute queries on external datasources
Dependencies: psycopg2, pymysql (optional)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional
from uuid import UUID
from contextlib import contextmanager

from db.connection import get_connection


# ============================================
# region Logger
# ============================================

logger = logging.getLogger(__name__)

# endregion
# ============================================


# ============================================
# region DatasourceConnectionService
# ============================================

class DatasourceConnectionService:
    """
    Dynamic datasource connection service
    
    Manages connections to external datasources configured for assistants.
    """
    
    # Connection pool cache (simple in-memory cache)
    _connection_cache: dict[str, Any] = {}

    @classmethod
    def _get_datasource_config(cls, datasource_id: UUID) -> Optional[dict]:
        """
        Get datasource configuration from database
        
        Args:
            datasource_id: Datasource UUID
        Returns:
            Datasource config dict or None
        """
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ds_type, connection_config, table_schema, name
                    FROM agent_datasources
                    WHERE datasource_id = %s AND is_active = TRUE
                    """,
                    (str(datasource_id),)
                )
                row = cur.fetchone()

                if not row:
                    return None

                return {
                    "ds_type": row[0],
                    "connection_config": row[1],
                    "table_schema": row[2],
                    "name": row[3]
                }

    @classmethod
    @contextmanager
    def get_postgresql_connection(cls, config: dict):
        """
        Create PostgreSQL connection from config
        
        Args:
            config: Connection config with host, port, database, user, password
        Yields:
            Database connection
        """
        import psycopg2
        
        conn = None
        try:
            conn = psycopg2.connect(
                host=config.get("host", "localhost"),
                port=config.get("port", 5432),
                database=config.get("database"),
                user=config.get("user"),
                password=config.get("password")
            )
            yield conn
        finally:
            if conn:
                conn.close()

    @classmethod
    @contextmanager
    def get_mysql_connection(cls, config: dict):
        """
        Create MySQL connection from config
        
        Args:
            config: Connection config with host, port, database, user, password
        Yields:
            Database connection
        """
        try:
            import pymysql
        except ImportError:
            raise ImportError("pymysql is required for MySQL connections. Install with: pip install pymysql")
        
        conn = None
        try:
            conn = pymysql.connect(
                host=config.get("host", "localhost"),
                port=config.get("port", 3306),
                database=config.get("database"),
                user=config.get("user"),
                password=config.get("password"),
                charset="utf8mb4"
            )
            yield conn
        finally:
            if conn:
                conn.close()

    @classmethod
    @contextmanager
    def get_connection_by_type(cls, ds_type: str, config: dict):
        """
        Get connection based on datasource type
        
        Args:
            ds_type: Type of datasource (postgresql, mysql)
            config: Connection configuration
        Yields:
            Database connection
        """
        if ds_type == "postgresql":
            with cls.get_postgresql_connection(config) as conn:
                yield conn
        elif ds_type == "mysql":
            with cls.get_mysql_connection(config) as conn:
                yield conn
        else:
            raise ValueError(f"Unsupported datasource type: {ds_type}")

    @classmethod
    def execute_query(
        cls, 
        datasource_id: UUID, 
        query: str, 
        params: Optional[tuple] = None
    ) -> list[dict[str, Any]]:
        """
        Execute query on external datasource
        
        Args:
            datasource_id: Datasource UUID
            query: SQL query to execute
            params: Query parameters
        Returns:
            List of result rows as dicts
        """
        config = cls._get_datasource_config(datasource_id)
        if not config:
            raise ValueError(f"Datasource not found: {datasource_id}")
        
        ds_type = config["ds_type"]
        conn_config = config["connection_config"]
        
        if ds_type == "api":
            return cls._execute_api_query(conn_config, query)
        
        with cls.get_connection_by_type(ds_type, conn_config) as conn:
            with conn.cursor() as cur:
                cur.execute(query, params or ())
                
                # Get column names
                columns = [desc[0] for desc in cur.description] if cur.description else []
                
                # Fetch results
                rows = cur.fetchall()
                
                return [dict(zip(columns, row)) for row in rows]

    @classmethod
    def _execute_api_query(cls, config: dict, query: str) -> list[dict[str, Any]]:
        """
        Execute query via API datasource
        
        Args:
            config: API configuration with endpoint, headers, method
            query: Query string (used as request body or params)
        Returns:
            API response as list of dicts
        """
        import requests
        
        endpoint = config.get("endpoint")
        headers = config.get("headers", {})
        method = config.get("method", "POST").upper()
        
        if method == "GET":
            response = requests.get(endpoint, params={"query": query}, headers=headers)
        else:
            response = requests.post(endpoint, json={"query": query}, headers=headers)
        
        response.raise_for_status()
        data = response.json()
        
        # Normalize response to list of dicts
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and "data" in data:
            return data["data"] if isinstance(data["data"], list) else [data["data"]]
        else:
            return [data]

    @classmethod
    def get_table_schema(cls, datasource_id: UUID) -> Optional[dict]:
        """
        Get table schema for a datasource (for LLM context)
        
        Args:
            datasource_id: Datasource UUID
        Returns:
            Table schema dict or None
        """
        config = cls._get_datasource_config(datasource_id)
        if config:
            return config.get("table_schema")
        return None

    @classmethod
    def test_connection(cls, datasource_id: UUID) -> dict[str, Any]:
        """
        Test connection to a datasource
        
        Args:
            datasource_id: Datasource UUID
        Returns:
            Test result with status and message
        """
        try:
            config = cls._get_datasource_config(datasource_id)
            if not config:
                return {"success": False, "message": "Datasource not found"}
            
            ds_type = config["ds_type"]
            conn_config = config["connection_config"]
            
            if ds_type == "api":
                # Simple API connectivity test
                import requests
                endpoint = conn_config.get("endpoint")
                requests.head(endpoint, timeout=5)
                return {"success": True, "message": "API endpoint reachable"}
            
            with cls.get_connection_by_type(ds_type, conn_config) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return {"success": True, "message": "Connection successful"}
                    
        except Exception as e:
            logger.error("Connection test failed: %s", e)
            return {"success": False, "message": str(e)}

    @classmethod
    def list_tables(cls, datasource_id: UUID) -> list[str]:
        """
        List tables in the datasource
        
        Args:
            datasource_id: Datasource UUID
        Returns:
            List of table names
        """
        config = cls._get_datasource_config(datasource_id)
        if not config:
            return []
        
        ds_type = config["ds_type"]
        conn_config = config["connection_config"]
        
        if ds_type == "api":
            return []  # API datasources don't have tables
        
        try:
            with cls.get_connection_by_type(ds_type, conn_config) as conn:
                with conn.cursor() as cur:
                    if ds_type == "postgresql":
                        cur.execute("""
                            SELECT table_name 
                            FROM information_schema.tables 
                            WHERE table_schema = 'public'
                        """)
                    elif ds_type == "mysql":
                        cur.execute("SHOW TABLES")
                    
                    return [row[0] for row in cur.fetchall()]
        except Exception as e:
            logger.error("Failed to list tables: %s", e)
            return []


# endregion
# ============================================
