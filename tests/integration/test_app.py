from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from evaluator import create_app
from evaluator.config import load_bootstrap_settings
from evaluator.dashboard import DashboardStatistics, LatestRunStatistics

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path):
    settings = load_bootstrap_settings(
        env_file=None,
        environment={
            "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
            "DATABASE_PATH": str(tmp_path / "data" / "production.db"),
            "EXPORT_PATH": str(tmp_path / "exports"),
            "LOG_LEVEL": "WARNING",
        },
    )
    return create_app(settings=settings, test_config={"TESTING": True})


def test_app_factory_starts_in_test_mode(app) -> None:
    assert app.testing is True
    assert app.name == "evaluator.app"


def test_health_has_stable_database_independent_contract(app) -> None:
    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "service": "thistlewarden",
        "status": "ok",
        "version": "1",
    }


def test_index_renders_english_page_with_empty_dashboard(app) -> None:
    response = app.test_client().get("/")

    assert response.status_code == 200
    assert response.content_type.startswith("text/html")
    page = response.get_data(as_text=True)
    assert "Thistlewarden" in page
    assert "Local evaluation of language models." in page
    assert '<html lang="en">' in page
    assert page.count("<section aria-labelledby=") == 3
    assert "<strong>0</strong> total runs" in page
    assert "<strong>0</strong> active model profiles" in page
    assert "<strong>0</strong> enabled test cases" in page
    assert "Pricing coverage" in page
    assert ">0%<" in page
    assert "Active judges" in page
    assert "Overall pass rate" in page
    assert "Average weighted score" in page
    assert page.count(">Unknown<") >= 2
    assert "LLM judge test cases" in page
    assert "Total judges" in page
    assert "No completed runs yet." in page
    assert 'href="/runs"' in page
    assert 'href="/endpoints"' in page
    assert 'href="/golden-sets"' in page
    assert '<link rel="icon" href="/static/app-icon.svg" type="image/svg+xml">' in page
    assert 'class="brand" href="/"' in page
    assert page.count('src="/static/app-icon.svg" alt="" width="32" height="32"') == 2
    assert '<footer class="site-footer">' in page
    assert '<strong class="footer-product">Thistlewarden</strong>' in page
    assert "<span>Local single-user evaluator</span>" in page


def test_header_links_open_github_and_manual_in_new_tabs(app) -> None:
    page = app.test_client().get("/").get_data(as_text=True)
    github = (
        '<a class="header-icon" href="https://github.com/lmeulen/Thistlewarden" '
        'target="_blank" rel="noopener noreferrer" title="GitHub project">'
    )
    manual = (
        '<a class="header-icon" href="https://github.com/lmeulen/Thistlewarden/blob/'
        'master/docs/manual/README.md" target="_blank" rel="noopener noreferrer" '
        'title="User manual">'
    )

    assert github in page
    assert manual in page
    assert "GitHub project (opens in a new tab)" in page
    assert "User manual (opens in a new tab)" in page
    assert page.count('<svg viewBox="0 0 24 24" aria-hidden="true">') == 2
    assert page.index(github) < page.index(manual) < page.index('id="theme-toggle"')


def test_dark_theme_controls_and_persistence_hooks_render(app) -> None:
    page = app.test_client().get("/").get_data(as_text=True)

    assert 'id="theme-toggle" type="button" aria-pressed="false"' in page
    assert '<svg class="theme-sun"' in page
    assert '<svg class="theme-moon"' in page
    assert '<span class="sr-only" id="theme-label">Dark theme</span>' in page
    assert "button.setAttribute('aria-label',dark?'Use light theme':'Use dark theme')" in page
    assert "localStorage.getItem('thistlewarden-theme')" in page
    assert "localStorage.setItem('thistlewarden-theme',theme)" in page
    assert "prefers-color-scheme: dark" in page
    assert "document.documentElement.dataset.theme=theme" in page
    assert page.index("document.documentElement.dataset.theme=theme") < page.index("app.css")


def test_dark_theme_styles_cover_surfaces_statuses_and_charts(app) -> None:
    css = app.test_client().get("/static/app.css").get_data(as_text=True)

    assert ":root[data-theme=dark]{color-scheme:dark" in css
    for selector in (
        ":root[data-theme=dark] input",
        ":root[data-theme=dark] table",
        ":root[data-theme=dark] .warning",
        ":root[data-theme=dark] .status-passed",
        ":root[data-theme=dark] .status-provider_guardrail",
        ":root[data-theme=dark] .status-provider_error",
        ":root[data-theme=dark] .status-processing_error",
        ":root[data-theme=dark] .status-unknown",
        ":root[data-theme=dark] .scatter-axis",
        ":root[data-theme=dark] .scatter-grid",
        ":root[data-theme=dark] .scatter-label",
        ":root[data-theme=dark] .scatter-point",
        ":root[data-theme=dark] .chart-track",
        ":root[data-theme=dark] .chart-score",
        ":root[data-theme=dark] .chart-pass",
        ":root[data-theme=dark] .chart-fail",
        ":root[data-theme=dark] .chart-error",
        ":root[data-theme=dark] .gate-dot-passed",
        ":root[data-theme=dark] .gate-dot-failed",
        ":root[data-theme=dark] .gate-dot-indeterminate",
        ":root[data-theme=dark] .gate-threshold",
    ):
        assert selector in css


def test_application_icon_is_served(app) -> None:
    response = app.test_client().get("/static/app-icon.svg")

    assert response.status_code == 200
    assert response.content_type.startswith("image/svg+xml")
    assert b'<title id="title">Thistlewarden</title>' in response.data


def test_numeric_thousands_info_stays_inline(app) -> None:
    css = app.test_client().get("/static/app.css").get_data(as_text=True)

    assert ".numeric-options>.numeric-checkbox{" in css
    rule = css.split(".numeric-options>.numeric-checkbox{", 1)[1].split("}", 1)[0]
    assert "flex-direction:row" in rule
    assert "flex-wrap:nowrap" in rule


def test_index_renders_all_dashboard_statistics(app) -> None:
    class StubDashboardService:
        def statistics(self) -> DashboardStatistics:
            return DashboardStatistics(
                total_runs=9,
                active_runs=2,
                finished_runs=7,
                active_model_profiles=3,
                active_endpoints=4,
                total_model_profiles=5,
                enabled_cases=6,
                active_golden_sets=7,
                total_cases=8,
                priced_model_profiles=2,
                active_judge_profiles=1,
                total_judge_profiles=3,
                llm_judge_cases=4,
                overall_pass_rate=Decimal("0.625"),
                average_weighted_score=Decimal("0.7125"),
                latest_run=LatestRunStatistics(
                    id="10000000-0000-0000-0000-000000000001",
                    name="Release candidate",
                    status="completed_with_errors",
                    gate_status="failed",
                    completed_at="2026-01-02T01:00:00Z",
                    weighted_score=Decimal("0.75"),
                    pass_rate=Decimal("0.8"),
                    error_rate=Decimal("0.1"),
                    known_total_cost=Decimal("0.1234"),
                    known_cost_count=9,
                    total_results=10,
                ),
            )

    app.extensions["dashboard_service"] = StubDashboardService()
    page = app.test_client().get("/").get_data(as_text=True)

    for label, value in (
        ("total runs", 9),
        ("Active runs", 2),
        ("Finished runs", 7),
        ("active model profiles", 3),
        ("Active endpoints", 4),
        ("Total model profiles", 5),
        ("enabled test cases", 6),
        ("Active golden sets", 7),
        ("Total test cases", 8),
        ("Active judges", 1),
        ("Total judges", 3),
        ("LLM judge test cases", 4),
    ):
        assert label in page
        assert f">{value}<" in page
    assert "Pricing coverage" in page
    assert ">67%<" in page
    assert "Overall pass rate" in page
    assert ">62%<" in page
    assert "Average weighted score" in page
    assert ">0.71<" in page
    assert "Latest completed run" in page
    assert "Release candidate" in page
    assert ">0.75<" in page
    assert ">80%<" in page
    assert ">10%<" in page
    assert ">0.123<" in page
    assert "9/10 results known" in page
    assert 'href="/runs/10000000-0000-0000-0000-000000000001"' in page


def test_openapi_uses_product_name(app) -> None:
    response = app.test_client().get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert response.get_json()["info"]["title"] == "Thistlewarden API"


def test_app_start_does_not_create_runtime_files(app, tmp_path: Path) -> None:
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "exports").exists()


def test_app_factory_accepts_container_bind_environment(tmp_path: Path) -> None:
    environment = {
        "SECRET_KEY": "test-secret-key-with-at-least-32-bytes",
        "CONTAINER_MODE": "true",
        "HOST": "0.0.0.0",
        "DATABASE_PATH": str(tmp_path / "container.db"),
        "EXPORT_PATH": str(tmp_path / "exports"),
        "LOG_LEVEL": "WARNING",
        "ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    }
    settings = load_bootstrap_settings(env_file=None, environment=environment)

    container_app = create_app(
        settings=settings,
        settings_environment=environment,
        initialize_database=True,
        test_config={"TESTING": True},
    )

    assert container_app.extensions["settings"].values.host == "0.0.0.0"
    assert container_app.extensions["settings"].values.container_mode is True
    assert (tmp_path / "container.db").exists()
    container_app.extensions["shutdown_services"]()
