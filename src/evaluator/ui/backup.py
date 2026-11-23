"""Configuration backup and restore GUI routes."""

from evaluator.ui.shared import (
    Any,
    ApiError,
    BackupSelection,
    ImportPlan,
    Response,
    RestoreMode,
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

_BACKUP_CATEGORIES = (
    "endpoints",
    "model_profiles",
    "judge_profiles",
    "judge_definitions",
    "golden_sets",
    "golden_set_items",
    "runs",
)


def _configuration_template(
    *, plan: ImportPlan | None = None, preview_payload: str | None = None
) -> str:
    return render_template(
        "configuration.html",
        plan=plan,
        preview_payload=preview_payload,
        backup_options=_config_service().selection_options(),
    )


@gui.get("/configuration")
def legacy_configuration_page() -> Response:
    return redirect(url_for("gui.configuration_page"), code=308)


@gui.get("/backup")
def configuration_page() -> str:
    return _configuration_template()


@gui.route("/backup/download", methods=["GET", "POST"])
def configuration_download() -> Response:
    selection = None
    if request.method == "POST":
        selection = BackupSelection(
            categories=set(request.form.getlist("categories")),
            item_ids={
                category: set(request.form.getlist(f"items_{category}"))
                for category in _BACKUP_CATEGORIES
            },
        )
        if not selection.categories and not any(selection.item_ids.values()):
            flash("Select at least one category or record to back up.", "error")
            return redirect(url_for("gui.configuration_page"))
    bundle = _config_service().export(selection)
    content = bundle.model_dump_json(indent=2).encode()
    return Response(
        content,
        content_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="evaluator-config-{bundle.schema_version}.json"'
            )
        },
    )


@gui.post("/backup/restore")
def configuration_import() -> str | Response:
    upload = request.files.get("file")
    preview_payload = request.form.get("preview_payload")
    mode = request.form.get("mode", "merge")
    if (upload is None and not preview_payload) or mode not in {
        "merge",
        "replace",
        "archive_all",
    }:
        flash("Choose a configuration backup file and restore mode.", "error")
        return _configuration_template()
    try:
        payload = json.loads(preview_payload) if preview_payload else json.load(cast(Any, upload))
        commit = "commit" in request.form
        if commit and mode in {"replace", "archive_all"} and "confirm_replace" not in request.form:
            raise ValueError("Explicitly confirm that existing configuration will be archived.")
        plan: ImportPlan = _config_service().import_bundle(
            payload, cast(RestoreMode, mode), preview=not commit
        )
        if commit:
            flash(
                "Configuration restored atomically. Add missing secrets through endpoints.",
                "success",
            )
            return redirect(url_for("gui.configuration_page"))
        return _configuration_template(
            plan=plan,
            preview_payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except (ApiError, ValueError, json.JSONDecodeError) as exc:
        flash(_error_message(exc), "error")
        return _configuration_template()
