"""Endpoint and model GUI routes."""

from evaluator.ui.shared import (
    _LOG,
    UUID,
    ApiError,
    Response,
    ValidationApiError,
    _discovery_service,
    _endpoint_payload,
    _endpoint_service,
    _error_message,
    _model_payload,
    _model_service,
    _model_tester,
    _onboarding_service,
    flash,
    g,
    gui,
    json,
    log_event,
    logging,
    redirect,
    render_template,
    request,
    url_for,
)


@gui.get("/endpoints")
def endpoints_page() -> str:
    return render_template("endpoints.html", endpoints=_endpoint_service().list_all())


@gui.route("/endpoints/new", methods=["GET", "POST"])
def endpoint_new() -> str | Response:
    if request.method == "POST":
        try:
            record = _endpoint_service().create(_endpoint_payload())
            flash("Endpoint created.", "success")
            return redirect(url_for("gui.endpoint_edit", endpoint_id=record.id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    return render_template("endpoint_form.html", endpoint=None)


@gui.route("/endpoints/<uuid:endpoint_id>", methods=["GET", "POST"])
def endpoint_edit(endpoint_id: UUID) -> str | Response:
    service = _endpoint_service()
    if request.method == "POST":
        try:
            service.update(endpoint_id, _endpoint_payload())
            flash("Endpoint saved; leaving secret blank preserves its current value.", "success")
            return redirect(url_for("gui.endpoint_edit", endpoint_id=endpoint_id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    return render_template("endpoint_form.html", endpoint=service.get(endpoint_id))


@gui.post("/endpoints/<uuid:endpoint_id>/delete")
def endpoint_delete(endpoint_id: UUID) -> Response:
    service = _endpoint_service()
    try:
        endpoint = service.get(endpoint_id)
        if request.form.get("confirmation_name", "") != endpoint.data.name:
            flash("Endpoint name did not match. Endpoint was not deleted.", "error")
            return redirect(url_for("gui.endpoint_edit", endpoint_id=endpoint_id))
        service.delete(endpoint_id, cascade_models=True)
        flash("Endpoint and its model profiles deleted.", "success")
    except ApiError as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.endpoints_page"))


@gui.route("/endpoints/<uuid:endpoint_id>/discover", methods=["GET", "POST"])
def discover(endpoint_id: UUID) -> str | Response:
    try:
        candidates = _discovery_service().discover(endpoint_id)
        if request.method == "POST":
            selected = request.form.getlist("model_id")
            records = _discovery_service().import_selected(endpoint_id, {"model_ids": selected})
            flash(f"{len(records)} model profile(s) created.", "success")
            return redirect(url_for("gui.endpoint_edit", endpoint_id=endpoint_id))
        flash("Connection succeeded; models retrieved.", "success")
        return render_template(
            "discovery.html", endpoint=_endpoint_service().get(endpoint_id), candidates=candidates
        )
    except ApiError as exc:
        flash(_error_message(exc), "error")
        return redirect(url_for("gui.endpoint_edit", endpoint_id=endpoint_id))


@gui.route("/endpoints/<uuid:endpoint_id>/models/new", methods=["GET", "POST"])
def model_new(endpoint_id: UUID) -> str | Response:
    service = _model_service()
    endpoint = _endpoint_service().get(endpoint_id)
    profiles = service.list_for_endpoint(endpoint_id)
    if request.method == "POST":
        try:
            record = service.create(endpoint_id, _model_payload())
            flash("Model profile created.", "success")
            return redirect(url_for("gui.model_edit", profile_id=record.id))
        except (ApiError, ValueError, json.JSONDecodeError) as exc:
            flash(_error_message(exc), "error")

    source = None
    source_id = request.args.get("copy_from", "").strip()
    if source_id:
        try:
            source = service.get(UUID(source_id))
            if source.endpoint_id != endpoint_id:
                raise ValidationApiError("Copy source does not belong to this endpoint.")
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
            source = None

    candidates = []
    if request.args.get("discover") == "1":
        try:
            candidates = _discovery_service().discover(endpoint_id)
            flash(f"Retrieved {len(candidates)} available model(s).", "success")
        except ApiError as exc:
            flash(_error_message(exc), "error")

    selected_model_id = request.args.get("model_id", "").strip()
    if (
        selected_model_id
        and candidates
        and selected_model_id not in {candidate.model_id for candidate in candidates}
    ):
        flash("Selected model is not in the retrieved model list.", "error")
        selected_model_id = ""
    display_name = ""
    if source is not None:
        reserved = {profile.data.display_name.casefold() for profile in profiles}
        base = f"{source.data.display_name} (copy)"
        display_name = base
        suffix = 2
        while display_name.casefold() in reserved:
            display_name = f"{base} {suffix}"
            suffix += 1
    elif selected_model_id:
        display_name = selected_model_id

    return render_template(
        "model_form.html",
        model=None,
        endpoint=endpoint,
        copy_profiles=profiles,
        copy_source=source,
        candidates=candidates,
        selected_model_id=selected_model_id,
        suggested_display_name=display_name,
    )


@gui.route("/models/<uuid:profile_id>", methods=["GET", "POST"])
def model_edit(profile_id: UUID) -> str | Response:
    service = _model_service()
    model = service.get(profile_id)
    if request.method == "POST":
        try:
            service.update(profile_id, _model_payload())
            flash("Model profile saved.", "success")
            return redirect(url_for("gui.model_edit", profile_id=profile_id))
        except (ApiError, ValueError, json.JSONDecodeError) as exc:
            flash(_error_message(exc), "error")
    return render_template(
        "model_form.html",
        model=model,
        endpoint=_endpoint_service().get(model.endpoint_id),
        copy_profiles=[],
        copy_source=None,
        candidates=[],
        selected_model_id="",
        suggested_display_name="",
        test_result=None,
        test_error=None,
    )


@gui.post("/models/<uuid:profile_id>/test")
def model_test(profile_id: UUID) -> str:
    model = _model_service().get(profile_id)
    endpoint = _endpoint_service().get(model.endpoint_id)
    result = None
    test_error = None
    try:
        result = _model_tester().test(model)
        if result.successful:
            _onboarding_service().record_success("model_test_succeeded", profile_id)
            flash("Model call succeeded.", "success")
        else:
            test_error = {
                "code": result.error_code or "MODEL_TEST_FAILED",
                "message": "Provider stream could not be processed successfully.",
                "request_id": g.request_id,
            }
    except ApiError as exc:
        diagnostic = getattr(exc, "provider_diagnostic", None)
        test_error = {
            "code": exc.code,
            "message": _error_message(exc),
            "request_id": g.request_id,
            "status_code": getattr(diagnostic, "status_code", None),
            "provider_code": getattr(diagnostic, "provider_code", None),
            "error_type": getattr(diagnostic, "error_type", None),
            "parameter": getattr(diagnostic, "parameter", None),
            "provider_message": getattr(diagnostic, "message", None),
            "provider_request_id": getattr(diagnostic, "provider_request_id", None),
            "raw_response": getattr(diagnostic, "raw_response", None),
            "response_truncated": getattr(diagnostic, "response_truncated", False),
        }
    except Exception as exc:
        log_event(
            _LOG,
            logging.ERROR,
            "model_test_failed",
            model_id=model.data.model_id,
            exception_type=type(exc).__name__,
            error_code="INTERNAL_ERROR",
        )
        test_error = {
            "code": "INTERNAL_ERROR",
            "message": "Unexpected error during model test.",
            "request_id": g.request_id,
        }
    if test_error is not None:
        flash("Model call failed. Review test result below.", "error")
    return render_template(
        "model_form.html",
        model=model,
        endpoint=endpoint,
        copy_profiles=[],
        copy_source=None,
        candidates=[],
        selected_model_id="",
        suggested_display_name="",
        test_result=result,
        test_error=test_error,
    )


@gui.post("/endpoints/<uuid:endpoint_id>/models/delete")
def models_delete(endpoint_id: UUID) -> Response:
    try:
        profile_ids = [UUID(value) for value in request.form.getlist("profile_ids")]
        count = _model_service().delete_many(endpoint_id, profile_ids)
        flash(
            f"{count} model profile{'s' if count != 1 else ''} deleted.",
            "success",
        )
    except (ApiError, ValueError) as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.endpoints_page"))


@gui.post("/models/<uuid:profile_id>/delete")
def model_delete(profile_id: UUID) -> Response:
    try:
        _model_service().delete(profile_id)
        flash("Model profile deleted.", "success")
    except ApiError as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.endpoints_page"))
