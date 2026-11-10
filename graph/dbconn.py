"""
Neo4j Database Connection module.

Retrieves configuration parameters via ConfigManager and authentication credentials
from environment variables NEO4J_USERID and NEO4J_PASSWORD.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from neo4j import Driver, GraphDatabase, Session
    HAS_NEO4J = True
except ImportError:
    GraphDatabase = None  # type: ignore
    Driver = Any  # type: ignore
    Session = Any  # type: ignore
    HAS_NEO4J = False

from config.config import ConfigManager

logger = logging.getLogger(__name__)


def get_repo_name() -> str:
    """
    Get the repository/directory name from the context where runpy/script is run.

    Returns:
        str: Directory name derived from execution context or current working directory.
    """
    main_mod = sys.modules.get("__main__")
    if main_mod and getattr(main_mod, "__file__", None):
        main_file = main_mod.__file__
        if main_file and not main_file.startswith("<"):
            return Path(main_file).resolve().parent.name
    if sys.argv and sys.argv[0] and not sys.argv[0].startswith("-"):
        arg_path = Path(sys.argv[0]).resolve()
        if arg_path.is_file():
            return arg_path.parent.name
        elif arg_path.is_dir():
            return arg_path.name
    return Path.cwd().name


def get_neo4j_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Retrieve Neo4j user ID and password from environment variables.

    Returns:
        Tuple[Optional[str], Optional[str]]: (userid, password) from NEO4J_USERID and NEO4J_PASSWORD.
    """
    userid = os.environ.get("NEO4J_USERID")
    password = os.environ.get("NEO4J_PASSWORD")
    return userid, password


def get_neo4j_config() -> Dict[str, Any]:
    """
    Get Neo4j configuration parameters combining ConfigManager settings and environment variables.
    If the database name is not provided in config, the directory/repo name from the execution context is used.

    Returns:
        Dict[str, Any]: Dictionary containing uri, host, port, scheme, database, userid, password,
        and is_repo_database flag.
    """
    userid, password = get_neo4j_credentials()

    scheme = ConfigManager.get("NEO4J", "SCHEME", "bolt")
    host = ConfigManager.get("NEO4J", "HOST", "localhost")
    port = ConfigManager.get_int("NEO4J", "PORT", 7687)

    default_uri = f"{scheme}://{host}:{port}"
    uri = ConfigManager.get("NEO4J", "URI", default_uri)

    database = ConfigManager.get("NEO4J", "DATABASE", None)
    is_repo_database = False
    if not database:
        database = get_repo_name()
        is_repo_database = True

    return {
        "uri": uri,
        "host": host,
        "port": port,
        "scheme": scheme,
        "database": database,
        "userid": userid,
        "password": password,
        "is_repo_database": is_repo_database,
    }


class Neo4jConnection:
    """
    Manager for Neo4j database connections.
    """

    def __init__(
        self,
        uri: Optional[str] = None,
        userid: Optional[str] = None,
        password: Optional[str] = None,
        database: Optional[str] = None,
        auto_create_db: Optional[bool] = None,
    ) -> None:
        cfg = get_neo4j_config()
        self.uri = uri or cfg["uri"]
        self.userid = userid if userid is not None else cfg["userid"]
        self.password = password if password is not None else cfg["password"]

        if database is not None:
            self.database = database
            self._auto_create_db = auto_create_db if auto_create_db is not None else False
        else:
            self.database = cfg["database"]
            self._auto_create_db = (
                auto_create_db if auto_create_db is not None else cfg.get("is_repo_database", False)
            )

        self._driver: Optional[Any] = None

    def get_existing_databases(self) -> list[str]:
        """
        Retrieve list of existing database names from Neo4j system database.
        """
        try:
            res = self.execute_query("SHOW DATABASES", database="system")
            records = getattr(res, "records", None)
            if records is None:
                if isinstance(res, (tuple, list)) and len(res) > 0:
                    records = res[0]
                elif hasattr(res, "__iter__") and not isinstance(res, (str, bytes, dict)):
                    records = res

            existing = []
            if records:
                for record in records:
                    name = None
                    if hasattr(record, "get"):
                        try:
                            name = record.get("name")
                        except Exception:
                            pass
                    if name is None and hasattr(record, "__getitem__"):
                        try:
                            name = record["name"]
                        except (IndexError, KeyError, TypeError):
                            pass
                    if name and isinstance(name, str):
                        existing.append(name)
            return existing
        except Exception as e:
            logger.warning("Could not list databases in Neo4j: %s", e)
            return []

    def database_exists(self, database_name: str) -> bool:
        """
        Check if a database already exists in Neo4j.
        """
        if not database_name:
            return False
        db_clean = database_name.replace("`", "").strip()
        existing_dbs = self.get_existing_databases()
        return any(db_clean.lower() == existing.lower() for existing in existing_dbs)

    def create_database(self, database_name: Optional[str] = None) -> Any:
        """
        Create database in Neo4j if it does not already exist with the same name.
        """
        db = database_name or self.database
        if not db:
            return None
        db_clean = db.replace("`", "")

        if self.database_exists(db_clean):
            logger.info("Database '%s' already exists in Neo4j. Skipping creation.", db_clean)
            return None

        query = f"CREATE DATABASE `{db_clean}` IF NOT EXISTS"
        logger.info("Creating database '%s' in Neo4j if not exists.", db_clean)
        try:
            return self.execute_query(query, database="system")
        except Exception as e:
            logger.warning("Could not create database '%s' in Neo4j: %s", db_clean, e)
            return None

    def connect(self) -> Any:
        """
        Establish connection and return Neo4j Driver instance.
        """
        if not HAS_NEO4J:
            raise ImportError(
                "The 'neo4j' package is required to connect to Neo4j. "
                "Please install it via 'pip install neo4j'."
            )

        if self._driver is None:
            auth = None
            if self.userid and self.password:
                auth = (self.userid, self.password)
            elif self.userid:
                auth = (self.userid, "")

            logger.info("Connecting to Neo4j at %s", self.uri)
            self._driver = GraphDatabase.driver(self.uri, auth=auth)

            if self._auto_create_db and self.database:
                self.create_database(self.database)
                if not self.database_exists(self.database):
                    logger.warning("Target database '%s' does not exist in Neo4j and cannot be created. Falling back to default database.", self.database)
                    self.database = None

        return self._driver

    @property
    def driver(self) -> Any:
        """
        Active Neo4j driver, establishing the connection lazily if needed.

        Returns None (instead of raising) when a connection cannot be established,
        so callers can use it in truthiness checks safely.
        """
        if self._driver is None:
            try:
                self.connect()
            except Exception as e:
                logger.warning("Could not establish Neo4j driver: %s", e)
                return None
        return self._driver

    def get_driver(self) -> Any:
        """Get active driver instance or establish new connection."""
        if self._driver is None:
            return self.connect()
        return self._driver

    def close(self) -> None:
        """Close the active Neo4j driver connection."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def get_session(self, database: Optional[str] = None) -> Any:
        """Get a session for executing queries."""
        driver = self.get_driver()
        db = database if database is not None else self.database
        return driver.session(database=db) if db else driver.session()

    def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> Any:
        """
        Execute Cypher query and return results.
        Falls back to default database if specified database does not exist.
        """
        driver = self.get_driver()
        db = database if database is not None else self.database
        try:
            return driver.execute_query(query, parameters_=parameters, database_=db)
        except Exception as e:
            err_msg = str(e).lower()
            if ("database" in err_msg and ("not found" in err_msg or "does not exist" in err_msg)) or "databasenotfound" in err_msg:
                logger.warning("Database '%s' not found in Neo4j. Falling back to default database.", db)
                if self.database == db:
                    self.database = None
                return driver.execute_query(query, parameters_=parameters, database_=None)
            raise

    def verify_connectivity(self) -> bool:
        """
        Verify if the connection to Neo4j database is successful.
        Returns True if successful, False otherwise.
        """
        try:
            driver = self.get_driver()
            if hasattr(driver, "verify_connectivity"):
                driver.verify_connectivity()
            self.execute_query("RETURN 1")
            return True
        except Exception as e:
            logger.warning("Neo4j database connection test failed: %s", e)
            return False

    def __enter__(self) -> Neo4jConnection:
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


def get_driver(
    uri: Optional[str] = None,
    userid: Optional[str] = None,
    password: Optional[str] = None,
) -> Any:
    """
    Convenience helper function to create a Neo4j driver using configuration and environment credentials.
    """
    conn = Neo4jConnection(uri=uri, userid=userid, password=password)
    return conn.connect()


def create_database(
    database_name: Optional[str] = None,
    uri: Optional[str] = None,
    userid: Optional[str] = None,
    password: Optional[str] = None,
) -> None:
    """
    Create a database in Neo4j if it does not exist.
    """
    conn = Neo4jConnection(
        uri=uri,
        userid=userid,
        password=password,
        database=database_name,
        auto_create_db=True,
    )
    conn.connect()
    conn.create_database(database_name or conn.database)
