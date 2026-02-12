# Shrike

- Designed by Richard Kelly. 
- Built with [Claude Opus 4.6](https://www.anthropic.com) (Anthropic).

A SQL Server test runner that catches bugs.

Shrike reads test files from a directory, executes them against SQL Server, and reports pass/fail results. It supports single-server checks, multi-row validation, and cross-server comparisons.

Runs on **Windows** and **Linux**.

## Installation

### Prerequisites

- **Python 3.10+**
- **ODBC Driver for SQL Server** — download [Microsoft ODBC Driver 18 for SQL Server](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server) for your platform if not already installed.

### For users (install from GitHub)

```bash
# Create a workspace for your tests (anywhere you like)
mkdir my-sql-tests
cd my-sql-tests

# Create a virtual environment
python -m venv .venv

# Activate it
# Windows (Command Prompt):
.venv\Scripts\activate.bat
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# Install Shrike from GitHub
pip install git+https://github.com/your-org/shrike.git

# Scaffold a test workspace
shrike init
```

This gives you:

```
my-sql-tests/
├── .venv/
├── .gitignore
├── connections.yaml
└── tests/
    └── 01_example_smoke_test.sql
```

Your tests live in **your own repo**, completely separate from Shrike. To update later:

```bash
pip install --upgrade git+https://github.com/your-org/shrike.git
```

### For development (clone the repo)

```bash
git clone https://github.com/your-org/shrike.git
cd shrike
python -m venv .venv

# Activate (see above for your platform)

pip install -e .
```

## Quick Start

```bash
# Scaffold a workspace (creates connections.yaml + tests/)
shrike init

# Edit connections.yaml with your servers, then run
shrike run --tests ./tests/ --connections connections.yaml

# With reports
shrike run -t ./tests/ -c connections.yaml --report report.html --json-report report.json

# Validate test files without connecting to any servers
shrike validate -t ./tests/

# Filter by tag
shrike run -t ./tests/ -c connections.yaml --tags "daily,etl"
```

## Test File Format

Each test file has **YAML frontmatter** (between `---` markers) followed by a **SQL body**.

### Simple Test

```sql
---
test_name: No Orphaned Orders
connection: production_db
tags: data-integrity, orders
success_column: orphan_count
success_value: 0
---
SELECT COUNT(*) AS orphan_count
FROM   orders o
LEFT JOIN customers c ON o.customer_id = c.id
WHERE  c.id IS NULL
```

The runner checks every returned row: if `success_column` equals `success_value` in all rows, the test passes.

### Cross-Server Test

Define multiple **connections** and **steps**. Use `success_expression` to compare results across servers.

```sql
---
test_name: Row Count Matches Warehouse
connections:
  source:
    server: prod-sql.example.com
    database: production
    trusted_connection: true
  target:
    server: warehouse.example.com
    database: dw
    trusted_connection: true

steps:
  - name: prod_count
    connection: source
  - name: wh_count
    connection: target

success_expression: "steps['prod_count'][0]['cnt'] == steps['wh_count'][0]['cnt']"
---
--- step: prod_count
SELECT COUNT(*) AS cnt FROM orders WHERE order_date >= '2024-01-01'
--- step: wh_count
SELECT COUNT(*) AS cnt FROM fact_orders WHERE order_date >= '2024-01-01'
```

### Value Injection

Later steps can reference values from earlier steps using `{{step.<n>.<column>}}`:

```sql
--- step: detail
SELECT {{step.prod_count.cnt}} AS source_count,
       {{step.wh_count.cnt}}   AS target_count
```

### Tolerance Checks

```yaml
success_expression: >
  abs(steps['source'][0]['total'] - steps['target'][0]['total'])
  / max(steps['source'][0]['total'], 1)
  < 0.01
```

## Connections File

Define connections once in `connections.yaml` and reference them by name:

```yaml
production_db:
  server: prod-sql.example.com
  database: app_production
  trusted_connection: true

warehouse:
  server: warehouse-sql.example.com
  database: data_warehouse
  trusted_connection: true
```

For **Windows/AD authentication**, `trusted_connection: true` is all you need — no username or password. The process authenticates as whatever account launches the script (your user, or a service account in a scheduled task).

For **Linux** with AD auth, you'll typically need Kerberos configured (`kinit`) and the ODBC driver set up for integrated auth. Alternatively, use SQL authentication with `username`/`password` fields.

## Secret Management

Use `${VAR}` or `${VAR:default}` anywhere in connection configs for environment variable substitution:

```yaml
warehouse:
  server: ${WAREHOUSE_HOST:localhost}
  username: ${DW_USERNAME}
  password: ${DW_PASSWORD}
```

Providing the variables:

```bash
# Linux / macOS
DW_PASSWORD=s3cret shrike run -t ./tests/ -c connections.yaml

# Windows (PowerShell)
$env:DW_PASSWORD = "s3cret"
shrike run -t ./tests/ -c connections.yaml

# Windows (Command Prompt)
set DW_PASSWORD=s3cret
shrike run -t ./tests/ -c connections.yaml
```

## YAML Frontmatter Reference

| Field | Required | Description |
|---|---|---|
| `test_name` | No | Display name (defaults to filename) |
| `description` | No | Human-readable description |
| `connection` | * | Named connection reference |
| `server` | * | Server address (if not using named connection) |
| `database` | No | Database name (default: `master`) |
| `username` / `password` | * | SQL auth credentials |
| `trusted_connection` | No | Use Windows/AD auth (default: false) |
| `driver` | No | ODBC driver (default: `ODBC Driver 18 for SQL Server`) |
| `tags` | No | Comma-separated or list of tags for filtering |
| `success_column` | No | Column to check (default: `success`) |
| `success_value` | No | Expected value (default: `0`) |
| `allow_empty` | No | Pass if query returns no rows (default: false) |
| `success_expression` | No | Python expression for complex evaluation |
| `connections` | ** | Map of named connections (multi-step) |
| `steps` | ** | List of step definitions (multi-step) |

*\* One of `connection` or `server` required for simple tests.*
*\*\* Required for cross-server tests.*

## CLI Reference

```
shrike run       Execute tests and produce reports
  --tests, -t          Test directory (required)
  --connections, -c    Connections YAML file
  --report, -r         HTML report output path
  --json-report, -j    JSON report output path
  --pattern            File glob (default: *.sql)
  --tags               Filter by tags (comma-separated)
  --verbose, -v        Debug logging

shrike init [dir]    Scaffold a new test workspace

shrike validate      Check test files for syntax errors
  --tests, -t          Test directory (required)
  --verbose, -v        Debug logging
```

## Exit Codes

`shrike run` returns `0` if all tests pass, `1` if any fail — suitable for CI/CD pipelines and scheduled tasks.

## Platform Notes

| | Windows | Linux |
|---|---|---|
| **Auth** | `trusted_connection: true` uses AD/domain account automatically | Requires Kerberos (`kinit`) for AD auth, or use SQL auth |
| **ODBC Driver** | [Download installer](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server) | `apt install msodbcsql18` or equivalent |
| **Scheduling** | Task Scheduler — run under a service account | cron / systemd timer |
| **Python** | python.org installer or Windows Store | System package manager or pyenv |

## Attribution

Built with [Claude Opus 4.6](https://www.anthropic.com) (Anthropic).

## License

MIT
