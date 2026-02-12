"""
Shrike CLI — subcommands for running tests and scaffolding workspaces.

Part of the Shrike SQL test runner.
Built with Claude Opus 4.6 (Anthropic). Licensed under MIT.
"""

import argparse
import datetime
import logging
import sys
import textwrap
from pathlib import Path

import yaml

from . import __version__
from .engine import (
    ConnectionManager,
    TestResult,
    parse_test_file,
    resolve_env_vars,
    run_test,
)
from .reports import generate_html_report, generate_json_report


# ---------------------------------------------------------------------------
# Cross-platform console safety
# ---------------------------------------------------------------------------

def _safe_symbol(symbol: str, fallback: str) -> str:
    """Return the symbol if the console can render it, otherwise a fallback."""
    try:
        symbol.encode(sys.stdout.encoding or "utf-8")
        return symbol
    except (UnicodeEncodeError, LookupError):
        return fallback


ICON_PASS = _safe_symbol("✅", "[OK]")
ICON_FAIL = _safe_symbol("❌", "[X]")
ICON_WARN = _safe_symbol("⚠️", "[!]")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("shrike")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-5s %(message)s", "%H:%M:%S")
    )
    logger.addHandler(handler)
    return logger


# ---------------------------------------------------------------------------
# Test discovery
# ---------------------------------------------------------------------------

def discover_tests(directory: Path, pattern: str = "*.sql") -> list[Path]:
    """Find all test files in directory matching pattern."""
    files = sorted(directory.glob(pattern))
    for ext in ("*.yaml", "*.yml", "*.txt"):
        files.extend(sorted(directory.glob(ext)))
    return files


# ---------------------------------------------------------------------------
# shrike run
# ---------------------------------------------------------------------------

def cmd_run(args):
    """Execute test files and produce reports."""
    logger = setup_logging(args.verbose)

    # Load shared connections
    shared_connections = {}
    if args.connections:
        conn_path = Path(args.connections)
        if conn_path.exists():
            raw = yaml.safe_load(conn_path.read_text(encoding="utf-8")) or {}
            shared_connections = resolve_env_vars(raw)
            logger.info(f"Loaded {len(shared_connections)} shared connection(s)")
        else:
            logger.warning(f"Connections file not found: {conn_path}")

    conn_mgr = ConnectionManager(shared_connections)

    # Discover tests
    test_dir = Path(args.tests)
    if not test_dir.is_dir():
        logger.error(f"Test directory not found: {test_dir}")
        sys.exit(1)

    test_files = discover_tests(test_dir, args.pattern)
    logger.info(f"Found {len(test_files)} test file(s) in {test_dir}")

    tag_filter = None
    if args.tags:
        tag_filter = {t.strip().lower() for t in args.tags.split(",")}

    results: list[TestResult] = []

    for filepath in test_files:
        try:
            meta = parse_test_file(filepath)
        except Exception as e:
            logger.error(f"Failed to parse {filepath}: {e}")
            results.append(TestResult(
                test_name=filepath.stem,
                file_path=str(filepath),
                passed=False,
                message=f"Parse error: {e}",
                timestamp=datetime.datetime.now().isoformat(),
            ))
            continue

        # Tag filtering
        if tag_filter:
            test_tags = {t.lower() for t in meta.get("tags", [])}
            if not tag_filter & test_tags:
                logger.debug(f"Skipping {filepath.name} (tags don't match)")
                continue

        result = run_test(meta, conn_mgr, logger)
        results.append(result)

    # Summary
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    logger.info(f"\n{'='*60}")
    logger.info(f"RESULTS: {passed} passed, {failed} failed, {len(results)} total")
    logger.info(f"{'='*60}")

    # Reports
    if args.report:
        generate_html_report(results, Path(args.report))
        logger.info(f"HTML report: {args.report}")

    if args.json_report:
        generate_json_report(results, Path(args.json_report))
        logger.info(f"JSON report: {args.json_report}")

    conn_mgr.close_all()
    sys.exit(1 if failed > 0 else 0)


# ---------------------------------------------------------------------------
# shrike init
# ---------------------------------------------------------------------------

INIT_CONNECTIONS = textwrap.dedent("""\
    # Shrike connections
    # ==================
    # Define your SQL Server connections here.
    # Use ${ENV_VAR} or ${ENV_VAR:default} for secrets.
    #
    # Reference these by name in your test files:
    #   connection: my_server

    my_server:
      server: localhost
      database: master
      trusted_connection: true
      # driver: "ODBC Driver 18 for SQL Server"

    # Example with SQL auth:
    # my_other_server:
    #   server: other-sql.example.com
    #   database: my_database
    #   username: ${DB_USER}
    #   password: ${DB_PASSWORD}
    #   trusted_connection: false
""")

INIT_EXAMPLE_TEST = textwrap.dedent("""\
    ---
    test_name: Example - Server Is Reachable
    connection: my_server
    success_column: status
    success_value: 1
    tags: smoke
    ---
    SELECT 1 AS status, @@SERVERNAME AS server_name
""")

INIT_GITIGNORE = textwrap.dedent("""\
    # Reports
    *.html
    *.json

    # Don't commit connections with real credentials
    # (uncomment if your connections.yaml contains secrets)
    # connections.yaml
""")


def cmd_init(args):
    """Scaffold a new Shrike test workspace."""
    target = Path(args.directory)
    tests_dir = target / "tests"

    if tests_dir.exists() and any(tests_dir.iterdir()):
        print(f"{ICON_WARN}  {tests_dir} already exists and is not empty. Aborting.")
        sys.exit(1)

    tests_dir.mkdir(parents=True, exist_ok=True)

    conn_file = target / "connections.yaml"
    if not conn_file.exists():
        conn_file.write_text(INIT_CONNECTIONS, encoding="utf-8")
        print(f"  Created {conn_file}")

    example_test = tests_dir / "01_example_smoke_test.sql"
    example_test.write_text(INIT_EXAMPLE_TEST, encoding="utf-8")
    print(f"  Created {example_test}")

    gitignore = target / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(INIT_GITIGNORE, encoding="utf-8")
        print(f"  Created {gitignore}")

    tests_path = target / "tests"
    conn_path_display = conn_file

    print(f"""
{ICON_PASS} Workspace ready at {target.resolve()}

Next steps:
  1. Edit connections.yaml with your server details
  2. Write test files in the tests/ directory
  3. Run your tests:

     shrike run -t {tests_path} -c {conn_path_display}
""")


# ---------------------------------------------------------------------------
# shrike validate
# ---------------------------------------------------------------------------

def cmd_validate(args):
    """Parse test files and check for errors without executing them."""
    logger = setup_logging(args.verbose)
    test_dir = Path(args.tests)

    if not test_dir.is_dir():
        logger.error(f"Test directory not found: {test_dir}")
        sys.exit(1)

    test_files = discover_tests(test_dir, args.pattern)
    logger.info(f"Validating {len(test_files)} test file(s) in {test_dir}")

    errors = 0
    for filepath in test_files:
        try:
            meta = parse_test_file(filepath)
            # Basic sanity checks
            has_connection = "connection" in meta or "server" in meta or "connections" in meta
            if not has_connection:
                logger.warning(f"  {ICON_WARN}  {filepath.name}: No connection defined")
            has_sql = "_sql" in meta or "_sql_steps" in meta
            if not has_sql:
                logger.error(f"  {ICON_FAIL} {filepath.name}: No SQL body found")
                errors += 1
            else:
                logger.info(f"  {ICON_PASS} {filepath.name}: {meta.get('test_name', filepath.stem)}")
        except Exception as e:
            logger.error(f"  {ICON_FAIL} {filepath.name}: {e}")
            errors += 1

    if errors:
        logger.info(f"\n{errors} file(s) have errors")
        sys.exit(1)
    else:
        logger.info(f"\nAll {len(test_files)} file(s) valid")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="shrike",
        description="Shrike — A SQL Server test runner that catches bugs",
    )
    parser.add_argument("--version", action="version", version=f"shrike {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- shrike run ---
    run_parser = subparsers.add_parser("run", help="Execute tests and produce reports")
    run_parser.add_argument("--tests", "-t", required=True, help="Directory containing test files")
    run_parser.add_argument("--connections", "-c", help="Shared connections YAML file")
    run_parser.add_argument("--report", "-r", help="Output HTML report path")
    run_parser.add_argument("--json-report", "-j", help="Output JSON report path")
    run_parser.add_argument("--pattern", default="*.sql", help="File glob pattern (default: *.sql)")
    run_parser.add_argument("--tags", help="Only run tests with these tags (comma-separated)")
    run_parser.add_argument("--verbose", "-v", action="store_true")
    run_parser.set_defaults(func=cmd_run)

    # --- shrike init ---
    init_parser = subparsers.add_parser("init", help="Scaffold a new test workspace")
    init_parser.add_argument("directory", nargs="?", default=".", help="Target directory (default: current)")
    init_parser.set_defaults(func=cmd_init)

    # --- shrike validate ---
    validate_parser = subparsers.add_parser("validate", help="Check test files for errors without running them")
    validate_parser.add_argument("--tests", "-t", required=True, help="Directory containing test files")
    validate_parser.add_argument("--pattern", default="*.sql", help="File glob pattern (default: *.sql)")
    validate_parser.add_argument("--verbose", "-v", action="store_true")
    validate_parser.set_defaults(func=cmd_validate)

    args = parser.parse_args()
    args.func(args)
