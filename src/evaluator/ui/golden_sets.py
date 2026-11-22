"""Golden-set and test-case GUI routes."""

from evaluator.ui.shared import (
    _GUI_PAGE_SIZE,
    _TEST_CASE_TYPE_VALUES,
    _TEST_CASE_TYPES,
    JUDGE_PRESETS,
    UUID,
    Any,
    ApiError,
    Literal,
    Response,
    ValidationApiError,
    _bulk_item_payload,
    _decimal,
    _error_message,
    _evaluation_preview_service,
    _gate_service,
    _golden_service,
    _golden_set_payload,
    _item_payload,
    _json_field,
    _judge_service,
    _judge_versions_for_editor,
    _onboarding_service,
    _page_details,
    _requested_page,
    abort,
    cast,
    current_app,
    evaluator_config_examples,
    flash,
    gui,
    json,
    jsonify,
    parse_qs,
    redirect,
    render_template,
    request,
    to_utc_iso,
    url_for,
    urlencode,
)


@gui.get("/golden-sets")
def golden_sets_page() -> str:
    service = _golden_service()
    page = _requested_page()
    records, total = service.list_sets(page, _GUI_PAGE_SIZE)
    details = _page_details(page, _GUI_PAGE_SIZE, total)
    if details["page"] != page:
        page = details["page"]
        records, total = service.list_sets(page, _GUI_PAGE_SIZE)
        details = _page_details(page, _GUI_PAGE_SIZE, total)
    return render_template("golden_sets.html", golden_sets=records, pagination=details)


@gui.post("/golden-sets/<uuid:set_id>/duplicate")
def golden_set_duplicate(set_id: UUID) -> Response:
    try:
        record = _golden_service().duplicate_set(set_id, request.form.get("name", ""))
        flash("Golden set duplicated.", "success")
        return redirect(url_for("gui.golden_set_edit", set_id=record.id))
    except (ApiError, ValueError) as exc:
        flash(_error_message(exc), "error")
        return redirect(url_for("gui.golden_sets_page"))


@gui.route("/golden-sets/new", methods=["GET", "POST"])
def golden_set_new() -> str | Response:
    if request.method == "POST":
        try:
            record = _golden_service().create_set(_golden_set_payload())
            flash("Golden set created.", "success")
            return redirect(url_for("gui.golden_set_edit", set_id=record.id))
        except (ApiError, ValueError, json.JSONDecodeError) as exc:
            flash(_error_message(exc), "error")
    return render_template("golden_set_form.html", golden_set=None, items=[])


@gui.route("/golden-sets/<uuid:set_id>", methods=["GET", "POST"])
def golden_set_edit(set_id: UUID) -> str | Response:
    service = _golden_service()
    if request.method == "POST":
        try:
            service.update_set(set_id, _golden_set_payload())
            flash("Golden set saved.", "success")
            return redirect(url_for("gui.golden_set_edit", set_id=set_id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    record = service.get_set(set_id)
    page = _requested_page()
    category = (request.args.get("category") or "").strip()[:200] or None
    tag = (request.args.get("tag") or "").strip()[:200] or None
    query = (request.args.get("query") or "").strip()[:200] or None
    enabled_value = (request.args.get("enabled") or "").strip().lower()
    enabled = {"true": True, "false": False}.get(enabled_value)
    evaluation_method = (request.args.get("evaluation_method") or "").strip()
    if evaluation_method not in _TEST_CASE_TYPE_VALUES:
        evaluation_method = ""
    items, total = service.list_items(
        set_id,
        page=page,
        per_page=_GUI_PAGE_SIZE,
        category=category,
        tag=tag,
        enabled=enabled,
        evaluation_method=evaluation_method or None,
        query=query,
    )
    details = _page_details(page, _GUI_PAGE_SIZE, total)
    if details["page"] != page:
        page = details["page"]
        items, total = service.list_items(
            set_id,
            page=page,
            per_page=_GUI_PAGE_SIZE,
            category=category,
            tag=tag,
            enabled=enabled,
            evaluation_method=evaluation_method or None,
            query=query,
        )
        details = _page_details(page, _GUI_PAGE_SIZE, total)
    return render_template(
        "golden_set_form.html",
        golden_set=record,
        items=items,
        pagination=details,
        item_filters={
            "category": category or "",
            "tag": tag or "",
            "enabled": enabled_value,
            "evaluation_method": evaluation_method,
            "query": query or "",
        },
        test_case_types=_TEST_CASE_TYPES,
        destination_sets=[
            candidate for candidate in service.list_sets(1, 100)[0] if candidate.id != set_id
        ],
        gates=_gate_service().defaults(set_id),
        judges=_judge_service().list_all(),
        judge_versions=_judge_versions_for_editor(),
        selected_judge_id=_judge_service().golden_set_override(set_id),
    )


@gui.post("/golden-sets/<uuid:set_id>/delete")
def golden_set_delete(set_id: UUID) -> Response:
    service = _golden_service()
    golden_set = service.get_set(set_id)
    if request.form.get("confirmation_name", "") != golden_set.data.name:
        flash("Type the exact golden set name to confirm deletion.", "error")
        return redirect(url_for("gui.golden_set_edit", set_id=set_id))
    try:
        deleted = service.delete_set(set_id)
        flash(
            "Golden set deleted."
            if deleted
            else "Golden set archived because run history still references it.",
            "success",
        )
    except ApiError as exc:
        flash(_error_message(exc), "error")
        return redirect(url_for("gui.golden_set_edit", set_id=set_id))
    return redirect(url_for("gui.golden_sets_page"))


@gui.post("/golden-sets/<uuid:set_id>/judge")
def golden_set_judge_update(set_id: UUID) -> Response:
    raw = request.form.get("judge_profile_id", "")
    try:
        _judge_service().set_golden_set_override(set_id, UUID(raw) if raw else None)
        flash("Judge override saved.", "success")
    except (ApiError, ValueError) as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.golden_set_edit", set_id=set_id))


@gui.post("/golden-sets/<uuid:set_id>/gates")
def quality_gates_update(set_id: UUID) -> Response:
    names = (
        "min_weighted_score",
        "min_pass_rate",
        "max_error_rate",
        "max_mean_latency_ms",
        "max_p95_latency_ms",
        "max_p95_ttft_ms",
        "min_mean_tps",
        "max_total_cost",
    )
    try:
        _gate_service().set_defaults(set_id, {name: _decimal(name) for name in names})
        flash("Quality gates saved.", "success")
    except (ApiError, ValueError) as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.golden_set_edit", set_id=set_id))


def _legacy_presets_for_item(item: Any) -> list[Any]:
    if item is None or item.data.evaluation_method != "llm_judge":
        return []
    config = item.data.evaluator_config
    preset_id = config.get("preset_id")
    preset_version = config.get("preset_version")
    return [
        preset
        for preset in JUDGE_PRESETS
        if preset.id == preset_id and preset.version == preset_version
    ]


def _render_golden_item_form(*, item: Any, golden_set: Any) -> str:
    legacy_presets = _legacy_presets_for_item(item)
    judge_versions = _judge_versions_for_editor()
    evaluator_examples = evaluator_config_examples()
    evaluator_examples["llm_judge"] = (
        {
            "judge_definition_id": judge_versions[0]["id"],
            "judge_definition_version": judge_versions[0]["version"],
            "expected_value": None,
            "context": None,
        }
        if judge_versions
        else {}
    )
    return render_template(
        "golden_item_form.html",
        item=item,
        golden_set=golden_set,
        evaluator_examples=evaluator_examples,
        legacy_judge_presets=legacy_presets,
        judge_presets_json=[preset.model_dump(mode="json") for preset in legacy_presets],
        judge_versions_json=judge_versions,
    )


@gui.route("/golden-sets/<uuid:set_id>/items/new", methods=["GET", "POST"])
def golden_item_new(set_id: UUID) -> str | Response:
    if request.method == "POST":
        try:
            item = _golden_service().create_item(set_id, _item_payload())
            flash("Test case created.", "success")
            return redirect(url_for("gui.golden_item_edit", item_id=item.id))
        except (ApiError, ValueError, json.JSONDecodeError) as exc:
            flash(_error_message(exc), "error")
        except Exception:
            current_app.logger.exception("golden-item creation failed")
            flash("Test case could not be created. Check its fields and try again.", "error")
    return _render_golden_item_form(item=None, golden_set=_golden_service().get_set(set_id))


@gui.post("/golden-sets/<uuid:set_id>/items/verify")
def golden_item_verify(set_id: UUID) -> tuple[Response, int] | Response:
    _golden_service().get_set(set_id)
    try:
        result = _evaluation_preview_service().evaluate(
            method=request.form.get("evaluation_method", "exact_match"),
            config=_json_field("evaluator_config"),
            expected_answer=request.form.get("expected_answer") or None,
            candidate=request.form.get("test_output", ""),
        )
    except (ValidationApiError, json.JSONDecodeError) as exc:
        message = (
            exc.message
            if isinstance(exc, ValidationApiError)
            else "Evaluator configuration is invalid JSON."
        )
        details = (
            [detail.as_dict() for detail in exc.details]
            if isinstance(exc, ValidationApiError)
            else []
        )
        return jsonify({"ok": False, "message": message, "details": details}), 400
    _onboarding_service().record_success("evaluator_preview_succeeded", set_id)
    return jsonify({"ok": True, **result.model_dump()})


@gui.route("/golden-items/<uuid:item_id>", methods=["GET", "POST"])
def golden_item_edit(item_id: UUID) -> str | Response:
    item = _golden_service().get_item(item_id)
    if request.method == "POST":
        try:
            _golden_service().update_item(item_id, _item_payload())
            flash("Test case saved.", "success")
            return redirect(url_for("gui.golden_item_edit", item_id=item_id))
        except (ApiError, ValueError, json.JSONDecodeError) as exc:
            flash(_error_message(exc), "error")
        except Exception:
            current_app.logger.exception("golden-item update failed")
            flash("Test case could not be saved. Check its fields and try again.", "error")
    return _render_golden_item_form(
        item=item, golden_set=_golden_service().get_set(item.golden_set_id)
    )


def _golden_return_parameters(raw_query: str) -> dict[str, Any]:
    values = parse_qs(raw_query[:2000], keep_blank_values=True)
    parameters: dict[str, Any] = {}
    page = values.get("page", [""])[0]
    if page.isdigit() and int(page) > 1:
        parameters["page"] = int(page)
    for name in ("query", "category", "tag", "evaluation_method"):
        value = values.get(name, [""])[0][:200]
        if value:
            parameters[name] = value
    enabled = values.get("enabled", [""])[0]
    if enabled in {"true", "false"}:
        parameters["enabled"] = enabled
    return parameters


@gui.post("/golden-sets/<uuid:set_id>/items/bulk-preview")
def golden_items_bulk_preview(set_id: UUID) -> str | Response:
    try:
        preview = _golden_service().preview_bulk_items(
            set_id, _bulk_item_payload(include_versions=False)
        )
        return render_template(
            "golden_items_bulk_preview.html",
            golden_set=_golden_service().get_set(set_id),
            preview=preview,
            expected_updated_at=json.dumps(
                {
                    str(item_id): to_utc_iso(value)
                    for item_id, value in preview.request.expected_updated_at.items()
                },
                sort_keys=True,
            ),
            return_query=urlencode(_golden_return_parameters(request.form.get("return_query", ""))),
        )
    except (ApiError, ValueError, json.JSONDecodeError) as exc:
        flash(_error_message(exc), "error")
        return redirect(url_for("gui.golden_set_edit", set_id=set_id))


@gui.post("/golden-sets/<uuid:set_id>/items/bulk-apply")
def golden_items_bulk_apply(set_id: UUID) -> Response:
    return_parameters = _golden_return_parameters(request.form.get("return_query", ""))
    try:
        count = _golden_service().apply_bulk_items(
            set_id, _bulk_item_payload(include_versions=True)
        )
        flash(f"Bulk change applied to exactly {count} test case(s).", "success")
    except (ApiError, ValueError, json.JSONDecodeError) as exc:
        flash(_error_message(exc), "error")
    return redirect(
        url_for(
            "gui.golden_set_edit",
            set_id=set_id,
            _anchor="testcases",
            **return_parameters,
        )
    )


@gui.post("/golden-sets/<uuid:set_id>/items/delete")
def golden_items_delete(set_id: UUID) -> Response:
    try:
        item_ids = [UUID(value) for value in request.form.getlist("item_ids")]
        deleted = _golden_service().delete_items(set_id, item_ids)
        flash(f"{deleted} test case(s) deleted.", "success")
    except (ApiError, ValueError) as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.golden_set_edit", set_id=set_id))


@gui.post("/golden-items/<uuid:item_id>/delete")
def golden_item_delete(item_id: UUID) -> Response:
    item = _golden_service().get_item(item_id)
    try:
        _golden_service().delete_item(item_id)
        flash("Test case deleted.", "success")
    except ApiError as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.golden_set_edit", set_id=item.golden_set_id))


@gui.route("/golden-sets/import", methods=["GET", "POST"])
def golden_set_import() -> str | Response:
    preview_data = None
    if request.method == "POST":
        upload = request.files.get("file")
        format_ = request.form.get("format", "json")
        if upload is None or format_ not in {"json", "csv"}:
            flash("Choose a valid JSON or CSV file.", "error")
        else:
            try:
                data, items, record = _golden_service().import_set(
                    upload.read(),
                    cast(Literal["json", "csv"], format_),
                    csv_set_name=request.form.get("name"),
                    preview="commit" not in request.form,
                )
                if record is None:
                    preview_data = {"name": data.name, "version": data.version, "count": len(items)}
                    flash("File is valid. Select it again to complete import.", "success")
                else:
                    flash("Golden set imported.", "success")
                    return redirect(url_for("gui.golden_set_edit", set_id=record.id))
            except ApiError as exc:
                flash(_error_message(exc), "error")
    return render_template("golden_import.html", preview=preview_data)


@gui.get("/golden-sets/<uuid:set_id>/download/<format_>")
def golden_set_download(set_id: UUID, format_: str) -> Response:
    if format_ not in {"json", "csv"}:
        abort(404)
    content = _golden_service().export_set(set_id, cast(Literal["json", "csv"], format_))
    return Response(
        content,
        content_type="application/json" if format_ == "json" else "text/csv",
        headers={"Content-Disposition": f'attachment; filename="golden-set.{format_}"'},
    )
