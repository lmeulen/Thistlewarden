"""Lightweight checks for stable internal dependency direction."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
SOURCE_ROOT = ROOT / "src" / "evaluator"

# Presentation code may compose dependencies. These modules must never be imported by
# domain, repository, provider, or persistence code.
NON_UI_MODULES = (
    "database.py",
    "endpoints.py",
    "evaluators",
    "golden_set_contracts.py",
    "golden_set_io.py",
    "golden_set_repository.py",
    "golden_set_service.py",
    "golden_sets.py",
    "judging",
    "migrations.py",
    "models.py",
    "provider.py",
    "provider_outcomes.py",
    "rate_limits.py",
    "repositories.py",
    "secrets.py",
    "settings.py",
    "streaming.py",
)

# Contracts remain portable and framework-free. New shared contracts belong here only
# after satisfying these restrictions.
SHARED_CONTRACTS = (
    "evaluators/contracts.py",
    "golden_set_contracts.py",
    "judging/extensions.py",
    "judging/presets.py",
)

UI_IMPORTS = {"evaluator.gui", "evaluator.ui"}
REMOVED_IMPORTS = {"evaluator.judges"}

CONTRACT_IMPORTS = {
    "flask",
    "sqlite3",
    "evaluator.database",
    "evaluator.golden_set_repository",
    "evaluator.gui",
    "evaluator.provider",
    "evaluator.streaming",
    "evaluator.ui",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


def _matches(imported: str, forbidden: set[str]) -> bool:
    return any(imported == name or imported.startswith(f"{name}.") for name in forbidden)


def _violations(paths: tuple[str, ...], forbidden: set[str]) -> list[str]:
    violations: list[str] = []
    for relative_path in paths:
        path = SOURCE_ROOT / relative_path
        candidates = path.rglob("*.py") if path.is_dir() else (path,)
        for candidate in candidates:
            for imported in sorted(_imports(candidate)):
                if _matches(imported, forbidden):
                    violations.append(f"{candidate.relative_to(ROOT)} imports {imported}")
    return violations


def test_non_ui_layers_do_not_depend_on_presentation() -> None:
    violations = _violations(NON_UI_MODULES, UI_IMPORTS)
    assert not violations, "Presentation dependency violation:\n" + "\n".join(violations)


def test_shared_contracts_are_framework_and_implementation_free() -> None:
    violations = _violations(SHARED_CONTRACTS, CONTRACT_IMPORTS)
    assert not violations, "Shared-contract dependency violation:\n" + "\n".join(violations)


def test_repository_does_not_restore_or_import_removed_judge_facade() -> None:
    assert not (SOURCE_ROOT / "judges.py").exists()
    violations: list[str] = []
    for root in (ROOT / "src", ROOT / "tests"):
        for candidate in root.rglob("*.py"):
            for imported in sorted(_imports(candidate)):
                if _matches(imported, REMOVED_IMPORTS):
                    violations.append(f"{candidate.relative_to(ROOT)} imports {imported}")
    assert not violations, "Removed-import violation:\n" + "\n".join(violations)


def test_boundary_guard_detects_direct_and_nested_forbidden_imports(tmp_path: Path) -> None:
    source = tmp_path / "contract.py"
    source.write_text(
        "from evaluator import ui\nfrom evaluator.provider.client import OpenAIProviderClient\n",
        encoding="utf-8",
    )
    imported = _imports(source)
    assert _matches("evaluator.ui", UI_IMPORTS)
    assert any(_matches(name, CONTRACT_IMPORTS) for name in imported)
    assert _matches("evaluator.judges.profiles", REMOVED_IMPORTS)
