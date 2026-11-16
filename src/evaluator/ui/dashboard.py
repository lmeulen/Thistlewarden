"""Dashboard, onboarding, and settings GUI routes."""

from evaluator.ui.shared import (
    ApiError,
    ConfigurationError,
    DashboardService,
    DashboardStatistics,
    OnboardingService,
    ResolvedSettings,
    Response,
    SettingLifecycle,
    SettingsService,
    _error_message,
    _golden_service,
    _onboarding_service,
    cast,
    current_app,
    flash,
    gui,
    redirect,
    render_template,
    request,
    url_for,
)


@gui.get("/")
def dashboard() -> str:
    service = current_app.extensions.get("dashboard_service")
    statistics = (
        cast(DashboardService, service).statistics()
        if service is not None
        else DashboardStatistics()
    )
    onboarding = (
        _onboarding_service().state() if "onboarding_service" in current_app.extensions else None
    )
    return render_template("index.html", statistics=statistics, onboarding=onboarding)


@gui.post("/onboarding/dismiss")
def onboarding_dismiss() -> Response:
    _onboarding_service().set_dismissed(True)
    flash("Pilot guidance dismissed. You can restore it from the dashboard.", "success")
    return redirect(url_for("gui.dashboard"))


@gui.post("/onboarding/restore")
def onboarding_restore() -> Response:
    _onboarding_service().set_dismissed(False)
    flash("Pilot guidance restored.", "success")
    return redirect(url_for("gui.dashboard"))


@gui.post("/onboarding/starter-set")
def onboarding_starter_set() -> Response:
    try:
        record = OnboardingService.create_starter_set(_golden_service())
        flash("Synthetic pilot golden set created. Review and edit it before use.", "success")
        return redirect(url_for("gui.golden_set_edit", set_id=record.id))
    except (ApiError, ValueError) as exc:
        flash(_error_message(exc), "error")
        return redirect(url_for("gui.dashboard"))


@gui.get("/settings")
def settings_page() -> str:
    service: SettingsService = current_app.extensions["settings_service"]
    active: ResolvedSettings = service.active
    desired: ResolvedSettings = service.desired
    return render_template(
        "settings.html", active=active.metadata, settings=desired.metadata.values()
    )


@gui.post("/settings/<name>")
def update_setting(name: str) -> Response:
    service: SettingsService = current_app.extensions["settings_service"]
    try:
        service.set_database_value(name, request.form.get("value", ""))
        current_app.extensions["active_settings"] = service.active
        current_app.extensions["desired_settings"] = service.desired
        current_app.extensions["settings"] = service.active
        lifecycle = service.desired.get(name).definition.lifecycle
        if lifecycle is SettingLifecycle.RESTART_REQUIRED:
            flash(
                "Setting saved as desired configuration. Restart required to activate it.",
                "success",
            )
        else:
            flash("Setting saved and applied.", "success")
    except (ConfigurationError, KeyError) as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.settings_page"))
