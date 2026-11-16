"""Configuration backup and restore GUI routes."""

from evaluator.ui.shared import (
    Any,
    ApiError,
    ImportPlan,
    Literal,
    Response,
    _config_service,
    _error_message,
    cast,
    flash,
    gui,
    json,
    redirect,
    render_template,
    request,
    url_for,
)


@gui.get("/configuration")
def legacy_configuration_page() -> Response:
    return redirect(url_for("gui.configuration_page"), code=308)


@gui.get("/backup")
def configuration_page() -> str:
    return render_template("configuration.html", plan=None, preview_payload=None)


@gui.get("/backup/download")
def configuration_download() -> Response:
    content = _config_service().export().model_dump_json(indent=2).encode()
    return Response(
        content,
        content_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="evaluator-config-1.0.json"'},
    )


@gui.post("/backup/restore")
def configuration_import() -> str | Response:
    upload = request.files.get("file")
    preview_payload = request.form.get("preview_payload")
    mode = request.form.get("mode", "merge")
    if (upload is None and not preview_payload) or mode not in {"merge", "replace"}:
        flash("Choose a configuration backup file and restore mode.", "error")
        return render_template("configuration.html", plan=None, preview_payload=None)
    try:
        payload = json.loads(preview_payload) if preview_payload else json.load(cast(Any, upload))
        commit = "commit" in request.form
        if commit and mode == "replace" and "confirm_replace" not in request.form:
            raise ValueError("Explicitly confirm that existing configuration will be archived.")
        plan: ImportPlan = _config_service().import_bundle(
            payload, cast(Literal["merge", "replace"], mode), preview=not commit
        )
        if commit:
            flash(
                "Configuration restored atomically. Add missing secrets through endpoints.",
                "success",
            )
            return redirect(url_for("gui.configuration_page"))
        return render_template(
            "configuration.html",
            plan=plan,
            preview_payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except (ApiError, ValueError, json.JSONDecodeError) as exc:
        flash(_error_message(exc), "error")
        return render_template("configuration.html", plan=None, preview_payload=None)
