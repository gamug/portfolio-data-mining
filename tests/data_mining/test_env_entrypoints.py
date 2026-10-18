"""Every entry point loads `.env` before it imports anything that reads the environment (T-066b).

Covers every `apps/*.py` and `cli/*.py` (except `apps/__init__.py`) and
`src/news_collector/main.py` (run as `python -m news_collector.main`). `scripts/` take `--db`
and are out of scope. An import side effect does not count: the call has to be a top-level
statement.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PROJECT_PACKAGES = {"news_collector", "data_mining", "pricing", "sec_edgar", "extractor", "apps"}
ENTRY_POINTS = sorted(
    [p for p in (REPO / "apps").glob("*.py") if p.name != "__init__.py"]
    + list((REPO / "cli").glob("*.py"))
    + [REPO / "src" / "news_collector" / "main.py"]
)


def _modules(node: ast.AST) -> list[str]:
    """The modules imported by `node` when it runs, in any nested block but not in a function body."""
    if isinstance(node, ast.ImportFrom):
        return [node.module] if node.level == 0 and node.module else []
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
        return []  # runs when called, not at import
    return [m for child in ast.iter_child_nodes(node) for m in _modules(child)]


def problem(source: str) -> str | None:
    """Why `source` does not load `.env` in time, or None.

    `portfolio_common` is not in `PROJECT_PACKAGES` on purpose: it reads no environment variable
    (no `environ`/`getenv` in the package, checked when this test was written).
    """
    body = ast.parse(source).body
    call = next(
        (
            i
            for i, n in enumerate(body)
            if isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name)
            and n.value.func.id == "load_env"
        ),
        None,
    )
    if call is None:
        return "no top-level load_env() call"
    before = [m for node in body[:call] for m in _modules(node)]
    if "data_mining.env" not in before:
        return "load_env is not imported from data_mining.env before the call"
    for module in before:
        if module != "data_mining.env" and module.split(".")[0] in PROJECT_PACKAGES:
            return f"imports {module} before load_env()"
    return None


@pytest.mark.parametrize("path", ENTRY_POINTS, ids=lambda p: str(p.relative_to(REPO)))
def test_entry_point_loads_env_first(path: Path) -> None:
    assert problem(path.read_text()) is None


def test_the_expected_entry_points_are_covered() -> None:
    names = {str(p.relative_to(REPO)) for p in ENTRY_POINTS}
    assert {"apps/gateway.py", "cli/news_collector_cli.py", "src/news_collector/main.py"} <= names
    assert len(names) == 10  # 5 apps (gateway and 4 sub-apps), 4 CLIs, news_collector/main.py


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from data_mining.env import load_env\nload_env()\nimport news_collector.x\n", None),
        (
            "import sys\nsys.path.insert(0, 'x')\nfrom data_mining.env import load_env\nload_env()\n",
            None,
        ),
        (
            "import news_collector.x\nfrom data_mining.env import load_env\nload_env()\n",
            "imports news_collector.x",
        ),
        (
            "from data_mining.db import f\nfrom data_mining.env import load_env\nload_env()\n",
            "imports data_mining.db",
        ),
        ("import fastapi\n", "no top-level load_env() call"),
        ("from dotenv import load_dotenv\nload_dotenv()\n", "no top-level load_env() call"),
        (
            "from data_mining.env import load_env\ndef f():\n    load_env()\n",
            "no top-level load_env() call",
        ),
        ("load_env()\n", "load_env is not imported"),
        (
            "try:\n    import news_collector.x\nexcept ImportError:\n    pass\n"
            "from data_mining.env import load_env\nload_env()\n",
            "imports news_collector.x",
        ),
        (
            "if True:\n    from pricing.fetcher import F\n"
            "from data_mining.env import load_env\nload_env()\n",
            "imports pricing.fetcher",
        ),
        (
            "def f():\n    import news_collector.x\n"
            "from data_mining.env import load_env\nload_env()\n",
            None,
        ),
    ],
)
def test_the_check_itself_fails_where_it_should(source: str, expected: str | None) -> None:
    result = problem(source)
    assert (result is None) if expected is None else (result is not None and expected in result)
