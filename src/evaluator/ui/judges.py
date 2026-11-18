"""Judge-profile GUI routes."""

from evaluator.ui.shared import (
    UUID,
    ApiError,
    Response,
    _all_models,
    _error_message,
    _judge_payload,
    _judge_service,
    current_app,
    flash,
    gui,
    redirect,
    render_template,
    request,
    url_for,
)


@gui.get("/judges")
def judges_page() -> str:
    definition_service = current_app.extensions["judge_definition_service"]
    definitions = []
    for record in definition_service.list_all():
        versions = definition_service.list_versions(record.id)
        definitions.append(
            {
                "record": record,
                "content": record.draft or (versions[-1].data if versions else None),
                "versions": versions,
            }
        )
    return render_template(
        "judges.html",
        judge_definitions=definitions,
        judge_profiles=_judge_service().list_all(),
    )


@gui.route("/judges/new", methods=["GET", "POST"])
def judge_new() -> str | Response:
    if request.method == "POST":
        try:
            record = _judge_service().create(_judge_payload())
            flash("Judge profile created.", "success")
            return redirect(url_for("gui.judge_edit", profile_id=record.id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    return render_template(
        "judge_form.html",
        judge=None,
        models=_all_models(),
        implementations=current_app.extensions["judge_implementation_registry"].identities(),
    )


@gui.route("/judges/<uuid:profile_id>", methods=["GET", "POST"])
def judge_edit(profile_id: UUID) -> str | Response:
    service = _judge_service()
    judge = service.get(profile_id)
    if request.method == "POST":
        try:
            service.update(profile_id, _judge_payload())
            flash("Judge profile saved.", "success")
            return redirect(url_for("gui.judge_edit", profile_id=profile_id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    return render_template(
        "judge_form.html",
        judge=judge,
        models=_all_models(),
        implementations=current_app.extensions["judge_implementation_registry"].identities(),
    )


@gui.post("/judges/<uuid:profile_id>/delete")
def judge_delete(profile_id: UUID) -> Response:
    if "confirm" not in request.form:
        flash("Explicitly confirm permanent deletion.", "error")
        return redirect(url_for("gui.judges_page"))
    try:
        _judge_service().delete(profile_id)
        flash("Judge profile deleted.", "success")
    except ApiError as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.judges_page"))
