"""
Core test engine: parsing, execution, and evaluation.

Part of the Shrike SQL test runner.
Built with Claude Opus 4.6 (Anthropic). Licensed under MIT.
"""

import datetime
import logging
import os
import re
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import pyodbc
import yaml

# ---------------------------------------------------------------------------
# Environment variable resolution
# ---------------------------------------------------------------------------

ENV_VAR_RE = re.compile(r"\$\{(\w+)(?::([^}]*))?\}")


def resolve_env_vars(value: Any) -> Any:
    """
    Recursively resolve ${VAR} and ${VAR:default} placeholders in strings,
    dicts, and lists.

    Examples:
      "${DB_PASSWORD}"          -> os.environ["DB_PASSWORD"]  (raises if unset)
      "${DB_PASSWORD:changeme}" -> os.environ.get("DB_PASSWORD", "changeme")
      "${DB_HOST}:${DB_PORT:1433}" -> "myserver.example.com:1433"
    """
    if isinstance(value, str):
        def replacer(match):
            var_name = match.group(1)
            default = match.group(2)
            env_val = os.environ.get(var_name)
            if env_val is not None:
                return env_val
            if default is not None:
                return default
            raise ValueError(
                f"Environment variable '${{{var_name}}}' is not set and no default provided"
            )
        return ENV_VAR_RE.sub(replacer, value)
    elif isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_env_vars(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    step_name: str
    server: str
    database: str
    sql: str
    rows: list[dict[str, Any]]
    columns: list[str]
    duration_ms: float
    error: Optional[str] = None


@dataclass
class TestResult:
    test_name: str
    file_path: str
    passed: bool
    message: str
    steps: list[StepResult] = field(default_factory=list)
    duration_ms: float = 0.0
    timestamp: str = ""
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages named SQL Server connections with caching."""

    def __init__(self, shared_connections: dict[str, dict] | None = None):
        self._shared = shared_connections or {}
        self._cache: dict[str, pyodbc.Connection] = {}

    def get_connection(self, conn_info: dict) -> pyodbc.Connection:
        """Return a pyodbc connection (cached by connection string)."""
        conn_str = self._build_connection_string(conn_info)
        if conn_str not in self._cache:
            self._cache[conn_str] = pyodbc.connect(conn_str, timeout=30)
        return self._cache[conn_str]

    def resolve(self, name_or_dict: str | dict) -> dict:
        """Resolve a connection name to its full config dict."""
        if isinstance(name_or_dict, str):
            if name_or_dict not in self._shared:
                raise ValueError(
                    f"Connection '{name_or_dict}' not found in shared connections. "
                    f"Available: {list(self._shared.keys())}"
                )
            return self._shared[name_or_dict]
        return name_or_dict

    def close_all(self):
        for conn in self._cache.values():
            try:
                conn.close()
            except Exception:
                pass
        self._cache.clear()

    @staticmethod
    def _build_connection_string(info: dict) -> str:
        driver = info.get("driver", "ODBC Driver 18 for SQL Server")
        server = info["server"]
        database = info.get("database", "master")
        trusted = info.get("trusted_connection", False)

        parts = [
            f"DRIVER={{{driver}}}",
            f"SERVER={server}",
            f"DATABASE={database}",
        ]

        if trusted:
            parts.append("Trusted_Connection=yes")
        else:
            parts.append(f"UID={info['username']}")
            parts.append(f"PWD={info['password']}")

        if info.get("trust_server_certificate", True):
            parts.append("TrustServerCertificate=yes")

        for k, v in info.get("odbc_extras", {}).items():
            parts.append(f"{k}={v}")

        return ";".join(parts)


# ---------------------------------------------------------------------------
# Test file parser
# ---------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
STEP_SEPARATOR_RE = re.compile(r"^---\s*step\s*(?::.*)?$", re.MULTILINE | re.IGNORECASE)


def parse_test_file(filepath: Path) -> dict:
    """
    Parse a test file with YAML frontmatter and SQL body.

    Supports two formats:

    FORMAT 1 — Simple (single query):
        ---
        test_name: My Test
        server: localhost
        database: mydb
        success_column: status
        ---
        SELECT 0 AS status, 'All good' AS message

    FORMAT 2 — Multi-step (cross-server):
        ---
        test_name: Cross-Server Row Count Match
        connections:
          source: { server: server-a, database: sales_db, trusted_connection: true }
          target: { server: server-b, database: warehouse_db, trusted_connection: true }
        steps:
          - name: source_count
            connection: source
          - name: target_count
            connection: target
        success_expression: "steps['source_count'][0]['cnt'] == steps['target_count'][0]['cnt']"
        ---
        --- step: source_count
        SELECT COUNT(*) AS cnt FROM orders
        --- step: target_count
        SELECT COUNT(*) AS cnt FROM fact_orders
    """
    text = filepath.read_text(encoding="utf-8-sig")

    m = FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"No YAML frontmatter found in {filepath}")

    meta = yaml.safe_load(m.group(1))
    body = text[m.end():]

    # Split body into steps if step separators are present
    step_name_re = re.compile(r"^---\s*step\s*:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
    step_names_in_body = [m.group(1).strip() for m in step_name_re.finditer(body)]

    if step_names_in_body:
        raw_parts = step_name_re.split(body)
        sql_by_step = {}
        for i in range(1, len(raw_parts), 2):
            name = raw_parts[i].strip()
            sql = raw_parts[i + 1].strip() if i + 1 < len(raw_parts) else ""
            sql_by_step[name] = sql
        meta["_sql_steps"] = sql_by_step
    else:
        meta["_sql"] = body.strip()

    meta["_filepath"] = str(filepath)
    meta = resolve_env_vars(meta)
    meta["_filepath"] = str(filepath)
    return meta


# ---------------------------------------------------------------------------
# Template rendering (inject prior step results into SQL)
# ---------------------------------------------------------------------------

def render_sql(sql_template: str, step_results: dict[str, list[dict]]) -> str:
    """
    Replace {{step.<step_name>.<column>}} placeholders with actual values
    from prior step results (first row).
    """
    def replacer(match):
        step_name = match.group(1)
        column = match.group(2)
        rows = step_results.get(step_name)
        if not rows:
            raise ValueError(f"Step '{step_name}' has no results to reference")
        val = rows[0].get(column)
        if val is None:
            raise ValueError(f"Column '{column}' not found in step '{step_name}'")
        if isinstance(val, str):
            return f"'{val}'"
        return str(val)

    return re.sub(r"\{\{step\.(\w+)\.(\w+)\}\}", replacer, sql_template)


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------

def execute_query(conn: pyodbc.Connection, sql: str) -> tuple[list[dict], list[str]]:
    """Execute SQL and return (rows_as_dicts, column_names)."""
    cursor = conn.cursor()
    cursor.execute(sql)

    if cursor.description is None:
        return [], []

    columns = [col[0] for col in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    return rows, columns


# ---------------------------------------------------------------------------
# Test evaluation
# ---------------------------------------------------------------------------

def evaluate_simple_test(meta: dict, rows: list[dict]) -> tuple[bool, str]:
    """Evaluate a single-query test using success_column or success_expression."""
    success_col = meta.get("success_column", "success")
    expect_value = meta.get("success_value", 0)

    if not rows:
        if meta.get("allow_empty", False):
            return True, "Query returned no rows (allowed)"
        return False, "Query returned no rows"

    failures = []
    for i, row in enumerate(rows):
        if success_col not in row:
            return False, f"Column '{success_col}' not found in results. Columns: {list(row.keys())}"
        if row[success_col] != expect_value:
            failures.append(f"Row {i}: {success_col}={row[success_col]}")

    if failures:
        return False, f"{len(failures)} row(s) failed: {'; '.join(failures[:5])}"
    return True, f"All {len(rows)} row(s) passed"


def evaluate_expression(expr: str, steps: dict[str, list[dict]]) -> tuple[bool, str]:
    """Evaluate a Python expression with step results in scope."""
    try:
        result = eval(expr, {"__builtins__": {}}, {
            "steps": steps, "len": len, "abs": abs,
            "sum": sum, "min": min, "max": max,
        })
        if result:
            return True, f"Expression passed: {expr}"
        else:
            detail_parts = []
            for step_name, rows in steps.items():
                if rows:
                    detail_parts.append(f"{step_name}: {rows[0]}")
            detail = "; ".join(detail_parts)
            return False, f"Expression failed: {expr} | Values: {detail}"
    except Exception as e:
        return False, f"Expression error: {e}"


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run_test(meta: dict, conn_mgr: ConnectionManager, logger: logging.Logger) -> TestResult:
    """Execute a single test (simple or multi-step) and return the result."""
    test_name = meta.get("test_name", Path(meta["_filepath"]).stem)
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]

    result = TestResult(
        test_name=test_name,
        file_path=meta["_filepath"],
        passed=False,
        message="",
        tags=tags,
        timestamp=datetime.datetime.now().isoformat(),
    )

    t0 = time.perf_counter()

    try:
        if "_sql_steps" in meta:
            result = _run_multistep(meta, conn_mgr, result, logger)
        else:
            result = _run_simple(meta, conn_mgr, result, logger)
    except Exception as e:
        result.passed = False
        result.message = f"Unhandled error: {e}"
        logger.error(f"[FAIL] {test_name}: {e}")
        logger.debug(traceback.format_exc())

    result.duration_ms = (time.perf_counter() - t0) * 1000
    return result


def _run_simple(meta, conn_mgr, result, logger):
    conn_info = _resolve_connection(meta, conn_mgr)
    conn = conn_mgr.get_connection(conn_info)

    sql = meta["_sql"]
    t0 = time.perf_counter()
    rows, columns = execute_query(conn, sql)
    dur = (time.perf_counter() - t0) * 1000

    step = StepResult(
        step_name="query",
        server=conn_info.get("server", "?"),
        database=conn_info.get("database", "?"),
        sql=sql, rows=rows, columns=columns, duration_ms=dur,
    )
    result.steps.append(step)

    if "success_expression" in meta:
        result.passed, result.message = evaluate_expression(
            meta["success_expression"], {"query": rows}
        )
    else:
        result.passed, result.message = evaluate_simple_test(meta, rows)

    log_fn = logger.info if result.passed else logger.warning
    log_fn(f"[{'PASS' if result.passed else 'FAIL'}] {result.test_name}: {result.message}")
    return result


def _run_multistep(meta, conn_mgr, result, logger):
    connections_config = meta.get("connections", {})
    steps_config = meta.get("steps", [])
    sql_steps = meta["_sql_steps"]

    collected: dict[str, list[dict]] = {}

    for step_def in steps_config:
        step_name = step_def["name"]
        conn_name = step_def.get("connection")

        if conn_name:
            if conn_name in connections_config:
                conn_info = connections_config[conn_name]
            else:
                conn_info = conn_mgr.resolve(conn_name)
        else:
            conn_info = _resolve_connection(meta, conn_mgr)

        conn = conn_mgr.get_connection(conn_info)

        sql_template = sql_steps.get(step_name, "")
        if not sql_template:
            raise ValueError(f"No SQL body found for step '{step_name}'")

        sql = render_sql(sql_template, collected)

        t0 = time.perf_counter()
        try:
            rows, columns = execute_query(conn, sql)
            error = None
        except Exception as e:
            rows, columns = [], []
            error = str(e)
        dur = (time.perf_counter() - t0) * 1000

        step_result = StepResult(
            step_name=step_name,
            server=conn_info.get("server", "?"),
            database=conn_info.get("database", "?"),
            sql=sql, rows=rows, columns=columns,
            duration_ms=dur, error=error,
        )
        result.steps.append(step_result)
        collected[step_name] = rows

        logger.debug(f"  Step '{step_name}': {len(rows)} rows in {dur:.1f}ms")

        if error:
            result.passed = False
            result.message = f"Step '{step_name}' failed: {error}"
            logger.warning(f"[FAIL] {result.test_name}: {result.message}")
            return result

    expr = meta.get("success_expression")
    if expr:
        result.passed, result.message = evaluate_expression(expr, collected)
    else:
        last_step = steps_config[-1]["name"]
        result.passed, result.message = evaluate_simple_test(meta, collected.get(last_step, []))

    log_fn = logger.info if result.passed else logger.warning
    log_fn(f"[{'PASS' if result.passed else 'FAIL'}] {result.test_name}: {result.message}")
    return result


def _resolve_connection(meta: dict, conn_mgr: ConnectionManager) -> dict:
    """Build connection info from inline fields or a named 'connection' key."""
    if "connection" in meta:
        return conn_mgr.resolve(meta["connection"])
    return {
        "server": meta["server"],
        "database": meta.get("database", "master"),
        "username": meta.get("username"),
        "password": meta.get("password"),
        "trusted_connection": meta.get("trusted_connection", False),
        "driver": meta.get("driver", "ODBC Driver 18 for SQL Server"),
        "trust_server_certificate": meta.get("trust_server_certificate", True),
    }
