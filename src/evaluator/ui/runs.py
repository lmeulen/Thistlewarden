"""Run and result GUI routes plus presentation calculations."""

from collections.abc import Iterable

from flask import send_file
from werkzeug.wsgi import ClosingIterator

from evaluator.run_exports import ExportFormat
from evaluator.ui.shared import (
    _RUN_SET_PAGE_SIZE,
    RESULT_PAGE_SIZE,
    UUID,
    Any,
    ApiError,
    ComparisonFilter,
    Decimal,
    ResolvedSettings,
    Response,
    ResultQuery,
    ValidationApiError,
    _aggregates,
    _all_models,
    _error_message,
    _gate_service,
    _golden_service,
    _judge_service,
    _onboarding_service,
    _optional,
    _page_details,
    _requested_page,
    _run_comparisons,
    _run_exports,
    _run_queue,
    _run_views,
    abort,
    cast,
    current_app,
    flash,
    gui,
    jsonify,
    redirect,
    render_template,
    request,
    safe_filename,
    url_for,
)

_GATE_PROGRESS_METRICS = {
    "min_weighted_score": ("Weighted score", "weighted_score"),
    "min_pass_rate": ("Pass rate", "pass_rate"),
    "max_error_rate": ("Error rate", "error_rate"),
    "max_mean_latency_ms": ("Mean latency", "latency_mean"),
    "max_p95_latency_ms": ("P95 latency", "latency_p95"),
    "max_p95_ttft_ms": ("P95 TTFT", "ttft_p95"),
    "min_mean_tps": ("Mean TPS", "tps_mean"),
}


def _gate_progress_position(measured: Decimal, threshold: Decimal) -> int:
    if threshold == 0:
        return 70 if measured == 0 else 100
    return max(0, min(100, round(measured / threshold * Decimal(70))))


def _quality_gate_progress(
    run: dict[str, Any], aggregates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Current, non-persistent gate measurements for an active run.

    This deliberately does not call the terminal gate evaluator or assign an
    outcome: incomplete samples cannot establish that a gate has passed.
    """
    if run["terminal"]:
        return None
    configured = run.get("quality_gates", {})
    if not configured:
        return None
    expected_per_model = run["total_jobs"] // len(aggregates) if aggregates else 0
    entries: list[dict[str, Any]] = []
    for aggregate in aggregates:
        model = aggregate["model"].get("display_name") or "Model"
        coverage = (
            f"{aggregate['total']}/{expected_per_model} results"
            if expected_per_model
            else "No results"
        )
        for gate, (label, metric) in _GATE_PROGRESS_METRICS.items():
            raw_threshold = configured.get(gate)
            if raw_threshold is None:
                continue
            threshold = Decimal(str(raw_threshold))
            measured = aggregate.get(metric)
            entries.append(
                {
                    "label": label,
                    "model": model,
                    "threshold": threshold,
                    "measured": measured,
                    "position": (
                        None
                        if measured is None
                        else _gate_progress_position(Decimal(str(measured)), threshold)
                    ),
                    "direction": "at least" if gate.startswith("min_") else "at most",
                    "coverage": coverage,
                }
            )
    raw_cost_limit = configured.get("max_total_cost")
    if raw_cost_limit is not None:
        known_costs = [
            item["known_total_cost"] for item in aggregates if item["known_total_cost"] is not None
        ]
        known_count = sum(item["known_cost_count"] for item in aggregates)
        expected = run["total_jobs"]
        projection = (
            sum(known_costs, Decimal(0)) / Decimal(known_count) * Decimal(expected)
            if known_count and expected
            else None
        )
        threshold = Decimal(str(raw_cost_limit))
        entries.append(
            {
                "label": "Total cost",
                "model": "Entire run",
                "threshold": threshold,
                "measured": projection,
                "position": None
                if projection is None
                else _gate_progress_position(projection, threshold),
                "direction": "at most",
                "coverage": f"{known_count}/{expected} costs known" if expected else "No jobs",
                "extrapolated": True,
            }
        )
    return {"entries": entries}


def _overview_model_charts(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = (
        ("weighted_score", "score", "Score", "Weighted score", "", 4, Decimal(1)),
        ("known_total_cost", "cost", "Cost", "Total known cost", "", 6, None),
        ("latency_mean", "latency", "Latency", "Mean latency", " ms", 0, None),
        (
            "total_tokens",
            "tokens",
            "Total tokens",
            "Total input and output tokens",
            " tokens",
            0,
            None,
        ),
    )
    charts = []
    count = max(1, len(models))
    chart_width = Decimal(max(800, 140 + count * 110))
    slot = (chart_width - Decimal(140)) / Decimal(count)
    for source, key, label, axis_label, suffix, places, fixed_max in definitions:
        known = [Decimal(str(model[source])) for model in models if model[source] is not None]
        maximum = fixed_max or (max(known) if known else Decimal(1))
        if maximum <= 0:
            maximum = Decimal(1)
        bars = []
        for index, model in enumerate(models):
            value = model[source]
            x = Decimal(70) + slot * Decimal(index) + slot * Decimal("0.15")
            width = slot * Decimal("0.7")
            height = (
                Decimal(0)
                if value is None
                else min(Decimal(200), Decimal(str(value)) / maximum * Decimal(200))
            )
            bars.append(
                {
                    "model": model["model"].get("display_name") or "Model",
                    "value": value,
                    "x": x,
                    "width": width,
                    "y": Decimal(220) - height,
                    "height": height,
                    "label_x": x + width / Decimal(2),
                }
            )
        charts.append(
            {
                "key": key,
                "label": label,
                "axis_label": axis_label,
                "suffix": suffix,
                "places": places,
                "axis_places": 4 if key == "cost" else places,
                "maximum": maximum,
                "width": chart_width,
                "axis_end": chart_width - Decimal(70),
                "axis_center": chart_width / Decimal(2),
                "bars": bars,
                "omitted": len(models) - len(known),
            }
        )
    return charts


@gui.get("/runs")
def runs_page() -> str:
    return render_template("runs.html", runs=_run_views().list_runs())


@gui.route("/runs/new", methods=["GET", "POST"])
def run_new() -> str | Response:
    service = _golden_service()
    defaults: dict[str, Any] = {}
    rerun_source: UUID | None = None
    rerun_warnings: list[str] = []
    rerun_value = (request.args.get("rerun") or "").strip()
    if request.method == "GET" and rerun_value:
        try:
            rerun_source = UUID(rerun_value)
            defaults = _run_views().rerun_defaults(rerun_source)
        except (ValueError, ApiError):
            abort(404)
    set_page = _requested_page("set_page")
    set_query = (request.args.get("set_query") or "").strip()[:200]
    golden_sets, set_total = service.search_sets(
        set_page,
        _RUN_SET_PAGE_SIZE,
        query=set_query,
        active_only=True,
    )
    set_pagination = _page_details(set_page, _RUN_SET_PAGE_SIZE, set_total)
    if set_pagination["page"] != set_page:
        set_page = set_pagination["page"]
        golden_sets, set_total = service.search_sets(
            set_page,
            _RUN_SET_PAGE_SIZE,
            query=set_query,
            active_only=True,
        )
        set_pagination = _page_details(set_page, _RUN_SET_PAGE_SIZE, set_total)
    models = [model for model in _all_models() if model.data.active]
    judges = [judge for judge in _judge_service().list_all() if judge.data.active]
    if defaults:
        selected_set_id = defaults["golden_set_id"]
        if not any(str(item.id) == selected_set_id for item in golden_sets):
            try:
                selected_set = service.get_set(UUID(selected_set_id))
                if selected_set.data.active:
                    golden_sets = [selected_set, *golden_sets]
                else:
                    rerun_warnings.append("Original golden set is no longer active.")
            except ApiError:
                rerun_warnings.append("Original golden set is no longer available.")
        active_model_ids = {str(model.id) for model in models}
        missing_models = set(defaults["model_profile_ids"]) - active_model_ids
        if missing_models:
            rerun_warnings.append(
                f"{len(missing_models)} original model profile(s) are no longer active "
                "or available."
            )
        judge_id = defaults["judge_profile_id"]
        if judge_id and judge_id not in {str(judge.id) for judge in judges}:
            rerun_warnings.append("Original judge profile is no longer active or available.")
    if request.method == "POST":
        gate_names = (
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
            payload: dict[str, Any] = {
                "name": request.form.get("name", ""),
                "golden_set_id": request.form.get("golden_set_id", ""),
                "model_profile_ids": request.form.getlist("model_profile_ids"),
                "judge_profile_id": _optional("judge_profile_id"),
                "note": _optional("note"),
            }
            if "override_gates" in request.form:
                payload["quality_gates"] = {
                    name: value for name in gate_names if (value := _optional(name)) is not None
                }
            run_id = _run_queue().create_run(payload)
            flash("Run created and queued.", "success")
            return redirect(url_for("gui.run_detail", run_id=run_id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    settings = cast(ResolvedSettings, current_app.extensions["settings"]).values
    return render_template(
        "run_form.html",
        golden_sets=golden_sets,
        set_query=set_query,
        set_pagination=set_pagination,
        models=models,
        judges=judges,
        concurrency=settings.run_concurrency,
        max_profiles=settings.max_model_profiles_per_run,
        set_summaries=_run_views().golden_set_summaries(),
        defaults=defaults,
        rerun_source=rerun_source,
        rerun_warnings=rerun_warnings,
    )


_MODEL_COMPARISON_SORTS = {
    "rank",
    "model",
    "score",
    "latency",
    "ttft",
    "tps",
    "tokens",
    "cost",
    "gates",
}
_GATE_SORT_ORDER = {"failed": 0, "indeterminate": 1, "passed": 2}


def _sort_model_comparison(
    aggregates: list[dict[str, Any]], sort: str, direction: str
) -> list[dict[str, Any]]:
    def value(item: dict[str, Any]) -> Any:
        if sort == "rank":
            return item["rank"]
        if sort == "model":
            return (item["model"].get("display_name") or "").casefold()
        if sort == "tokens":
            if item["input_tokens"] is None and item["output_tokens"] is None:
                return None
            return (item["input_tokens"] or 0, item["output_tokens"] or 0)
        if sort == "gates":
            if not item["gates"]:
                return None
            return tuple(sorted(_GATE_SORT_ORDER[gate["outcome"]] for gate in item["gates"]))
        return item[
            {
                "score": "weighted_score",
                "latency": "latency_p95",
                "ttft": "ttft_p95",
                "tps": "tps_mean",
                "cost": "known_total_cost",
            }[sort]
        ]

    available = [item for item in aggregates if value(item) is not None]
    missing = [item for item in aggregates if value(item) is None]
    available.sort(key=lambda item: item["model_snapshot_id"])
    available.sort(key=value, reverse=direction == "desc")
    return available + missing


def _model_chooser(aggregates: list[dict[str, Any]], *, eligible: bool) -> dict[str, Any]:
    """Return an explainable recommendation from the already displayed aggregates."""
    if not eligible:
        return {"model": None, "reason": "Recommendation is available after the run completes."}
    candidates = [
        item
        for item in aggregates
        if item["weighted_score"] is not None
        and all(gate["outcome"] == "passed" for gate in item["gates"])
    ]
    if not candidates:
        return {
            "model": None,
            "reason": "No model has a score and passes every configured model gate.",
        }
    chosen = min(candidates, key=lambda item: (item["rank"], item["model_snapshot_id"]))
    alternatives = [
        item
        for item in aggregates
        if item is not chosen
        and item["total_cost"] is not None
        and chosen["total_cost"] is not None
        and item["total_cost"] < chosen["total_cost"]
    ]
    alternative = (
        min(alternatives, key=lambda item: (item["total_cost"], item["model_snapshot_id"]))
        if alternatives
        else None
    )
    return {
        "model": chosen,
        "alternative": alternative,
        "cost_savings": chosen["total_cost"] - alternative["total_cost"]
        if alternative is not None
        else None,
        "error_rate_delta": alternative["error_rate"] - chosen["error_rate"]
        if alternative is not None
        and alternative["error_rate"] is not None
        and chosen["error_rate"] is not None
        else None,
        "has_configured_gates": bool(chosen["gates"]),
    }


def _cost_score_scatter(aggregates: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an accessible quality/cost frontier from visible aggregate values."""
    plot_left = Decimal(70)
    plot_width = Decimal(660)
    plot_top = Decimal(20)
    plot_height = Decimal(220)
    eligible = [
        item
        for item in aggregates
        if item["known_total_cost"] is not None and item["weighted_score"] is not None
    ]
    scores = [min(Decimal(1), max(Decimal(0), item["weighted_score"])) for item in eligible]
    costs = [max(Decimal(0), item["known_total_cost"]) for item in eligible]

    def bounds(
        values: list[Decimal], *, floor: Decimal, ceiling: Decimal | None = None
    ) -> tuple[Decimal, Decimal]:
        if not values:
            return floor, ceiling if ceiling is not None else floor + Decimal(1)
        low, high = min(values), max(values)
        padding = (
            max(abs(low) * Decimal("0.1"), Decimal("0.05") if ceiling else Decimal("0.000001"))
            if low == high
            else (high - low) * Decimal("0.1")
        )
        lower, upper = max(floor, low - padding), high + padding
        if ceiling is not None:
            upper = min(ceiling, upper)
        if lower == upper:
            lower = max(floor, lower - padding)
            upper = min(ceiling, upper + padding) if ceiling is not None else upper + padding
        return lower, upper

    _, x_max = bounds(costs, floor=Decimal(0))
    x_min, y_max = Decimal(0), Decimal(1)
    lowest_score = min(scores, default=Decimal(0))
    y_min = max(
        Decimal(0),
        (lowest_score * Decimal(10)).to_integral_value(rounding="ROUND_FLOOR") / Decimal(10),
    )
    if y_min == y_max:
        y_min = Decimal("0.9")
    x_range, y_range = x_max, y_max - y_min
    latency_values = [
        item["latency_p95"] for item in eligible if item.get("latency_p95") is not None
    ]
    latency_min, latency_max = (
        (min(latency_values), max(latency_values)) if latency_values else (None, None)
    )

    def gate_status(item: dict[str, Any]) -> str:
        outcomes = [gate["outcome"] for gate in item.get("gates", [])]
        if not outcomes:
            return "unconfigured"
        if all(outcome == "passed" for outcome in outcomes):
            return "passed"
        if any(outcome == "failed" for outcome in outcomes):
            return "failed"
        return "indeterminate"

    def dominated(item: dict[str, Any]) -> bool:
        cost, score = item["known_total_cost"], item["weighted_score"]
        return any(
            other is not item
            and other["known_total_cost"] <= cost
            and other["weighted_score"] >= score
            and (other["known_total_cost"] < cost or other["weighted_score"] > score)
            for other in eligible
        )

    points = []
    for item, cost, score in zip(eligible, costs, scores, strict=True):
        latency = item.get("latency_p95")
        radius = Decimal(8)
        if latency is not None and latency_min is not None and latency_max is not None:
            radius = (
                Decimal(11)
                if latency_min == latency_max
                else Decimal(6) + (latency - latency_min) / (latency_max - latency_min) * Decimal(8)
            )
        points.append(
            {
                "model": item["model"],
                "cost": item["known_total_cost"],
                "score": item["weighted_score"],
                "latency": latency,
                "radius": radius,
                "gate_status": gate_status(item),
                "dominated": dominated(item),
                "x": plot_left + (cost - x_min) / x_range * plot_width,
                "y": plot_top + (y_max - score) / y_range * plot_height,
            }
        )
    frontier = sorted(
        (point for point in points if not point["dominated"]),
        key=lambda point: (point["cost"], point["score"]),
    )
    x_ticks = [
        {
            "value": x_range * Decimal(index) / Decimal(4),
            "x": plot_left + plot_width * Decimal(index) / Decimal(4),
        }
        for index in range(5)
    ]
    y_tick_count = int(y_range / Decimal("0.1"))
    y_ticks = [
        {
            "value": y_max - Decimal(index) / Decimal(10),
            "y": plot_top + plot_height * Decimal(index) / Decimal(y_tick_count),
        }
        for index in range(y_tick_count + 1)
    ]
    return {
        "points": points,
        "frontier": frontier,
        "omitted": len(aggregates) - len(points),
        "x_min": x_min,
        "x_max": x_max,
        "x_ticks": x_ticks,
        "y_min": y_min,
        "y_max": y_max,
        "y_ticks": y_ticks,
    }


@gui.get("/runs/<uuid:run_id>")
def run_detail(run_id: UUID) -> str:
    run = _run_views().run(run_id)
    query_error = None
    try:
        query = ResultQuery.parse(request.args)
    except ApiError as exc:
        query, query_error = ResultQuery(), _error_message(exc)
    models = _run_views().models(run_id)
    aggregates = _aggregates().for_run(run_id)
    names = {model["id"]: model for model in models}
    for aggregate in aggregates:
        aggregate["model"] = names.get(aggregate["model_snapshot_id"], {})
        input_tokens = aggregate["input_tokens"]
        output_tokens = aggregate["output_tokens"]
        aggregate["total_tokens"] = (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        )
    gate_results = _gate_service().results(run_id)
    gates_by_model: dict[str, list[dict[str, Any]]] = {}
    for gate in gate_results["gates"]:
        model_snapshot_id = gate["model_snapshot_id"]
        if model_snapshot_id is not None:
            gates_by_model.setdefault(model_snapshot_id, []).append(gate)
    for aggregate in aggregates:
        aggregate["gates"] = gates_by_model.get(aggregate["model_snapshot_id"], [])
    model_sort = request.args.get("model_sort", "rank").strip()
    model_direction = request.args.get("model_direction", "asc").strip()
    if model_sort not in _MODEL_COMPARISON_SORTS or model_direction not in {"asc", "desc"}:
        model_sort, model_direction = "rank", "asc"
        query_error = query_error or "Invalid model comparison sorting."
    aggregates = _sort_model_comparison(aggregates, model_sort, model_direction)
    result_rows = _run_views().results(run_id, query)
    analysis = _run_views().analysis(run_id)
    has_next = len(result_rows) > RESULT_PAGE_SIZE
    return render_template(
        "run_detail.html",
        run=run,
        models=models,
        aggregates=aggregates,
        model_sort=model_sort,
        model_direction=model_direction,
        model_chooser=_model_chooser(
            aggregates,
            eligible=run["status"] in {"completed", "completed_with_errors"},
        ),
        cost_score_scatter=_cost_score_scatter(aggregates),
        model_charts=_overview_model_charts(aggregates),
        model_count=len(aggregates),
        analysis=analysis,
        results=result_rows[:RESULT_PAGE_SIZE],
        query=query,
        query_error=query_error,
        has_next=has_next,
        gates=gate_results,
        gate_progress=_quality_gate_progress(run, aggregates),
    )


@gui.get("/runs/<uuid:run_id>/compare")
def run_compare(run_id: UUID) -> str:
    baseline_value = request.args.get("baseline", "").strip()
    try:
        baseline = UUID(baseline_value) if baseline_value else None
    except ValueError as exc:
        raise ValidationApiError("Invalid baseline run.") from exc
    filter_value = request.args.get("filter", "all")
    if filter_value not in {"all", "regression", "improvement", "unchanged", "incomparable"}:
        raise ValidationApiError("Invalid comparison filter.")
    comparison = (
        _run_comparisons().compare(run_id, baseline, filter_=cast(ComparisonFilter, filter_value))
        if baseline is not None
        else None
    )
    return render_template(
        "run_compare.html",
        run=_run_views().run(run_id),
        candidates=_run_comparisons().candidates(run_id),
        comparison=comparison,
        selected_baseline=baseline,
        selected_filter=filter_value,
    )


@gui.get("/runs/<uuid:run_id>/compare/<uuid:baseline_id>/export")
def run_compare_export(run_id: UUID, baseline_id: UUID) -> Response:
    comparison = _run_comparisons().compare(run_id, baseline_id)
    name = safe_filename(f"{comparison['candidate']['name']}-vs-{comparison['baseline']['name']}")
    return Response(
        _run_comparisons().export_json(run_id, baseline_id),
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}.comparison.json"'},
    )


@gui.get("/runs/<uuid:run_id>/status")
def run_status(run_id: UUID) -> Response:
    run = _run_views().run(run_id)
    return jsonify(
        {
            key: run[key]
            for key in (
                "id",
                "status",
                "gate_status",
                "total_jobs",
                "done_jobs",
                "progress_percent",
                "terminal",
            )
        }
    )


@gui.get("/runs/<uuid:run_id>/results/<uuid:result_id>")
def run_result_detail(run_id: UUID, result_id: UUID) -> str:
    run = _run_views().run(run_id)
    result = _run_views().result(run_id, result_id)
    if run["status"] in {"completed", "completed_with_errors"}:
        _onboarding_service().record_success("result_inspected", result_id)
    return render_template("run_result.html", run=run, result=result)


@gui.post("/runs/<uuid:run_id>/name")
def run_rename(run_id: UUID) -> Response:
    try:
        _run_queue().rename(run_id, request.form.get("name", ""))
        flash("Run renamed.", "success")
    except ApiError as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.run_detail", run_id=run_id))


@gui.post("/runs/<uuid:run_id>/cancel")
def run_cancel(run_id: UUID) -> Response:
    if "confirm" not in request.form:
        flash("Explicitly confirm cancellation.", "error")
    else:
        try:
            _run_queue().cancel(run_id)
            flash("Run cancelled.", "success")
        except ApiError as exc:
            flash(_error_message(exc), "error")
    return redirect(url_for("gui.run_detail", run_id=run_id))


@gui.get("/runs/<uuid:run_id>/rerun-failures")
def run_rerun_failures_review(run_id: UUID) -> str | Response:
    try:
        preview = _run_queue().rerun_failures_preview(run_id)
    except ApiError as exc:
        flash(_error_message(exc), "error")
        return redirect(url_for("gui.run_detail", run_id=run_id))
    return render_template("rerun_failures_review.html", run_id=run_id, preview=preview)


@gui.post("/runs/<uuid:run_id>/rerun-failures")
def run_rerun_failures(run_id: UUID) -> Response:
    try:
        fingerprint = request.form.get("fingerprint", "")
        if "confirm" not in request.form:
            raise ValidationApiError("Explicitly confirm rerun scope and cost.")
        if not fingerprint:
            raise ValidationApiError("Review current configuration before creating a rerun.")
        new_id = _run_queue().rerun_failures(run_id, request.form.get("name", ""), fingerprint)
        flash("New run created for models with errored jobs.", "success")
        return redirect(url_for("gui.run_detail", run_id=new_id))
    except ApiError as exc:
        flash(_error_message(exc), "error")
        return redirect(url_for("gui.run_rerun_failures_review", run_id=run_id))


@gui.post("/runs/delete")
def runs_delete() -> Response:
    try:
        run_ids = [UUID(value) for value in request.form.getlist("run_ids")]
        _run_exports().delete_many(run_ids)
        count = len(set(run_ids))
        flash(f"{count} run{'s' if count != 1 else ''} permanently deleted.", "success")
    except (ApiError, ValueError):
        flash("Select one or more valid, completed runs.", "error")
    return redirect(url_for("gui.runs_page"))


@gui.post("/runs/<uuid:run_id>/delete")
def run_delete(run_id: UUID) -> Response:
    if "confirm" not in request.form:
        flash("Explicitly confirm permanent deletion.", "error")
        return redirect(url_for("gui.run_detail", run_id=run_id))
    try:
        _run_exports().delete(run_id)
        flash("Run permanently deleted.", "success")
        return redirect(url_for("gui.runs_page"))
    except ApiError as exc:
        flash(_error_message(exc), "error")
        return redirect(url_for("gui.run_detail", run_id=run_id))


@gui.route("/runs/<uuid:run_id>/export", methods=["GET", "POST"])
def run_export(run_id: UUID) -> str | Response:
    run = _run_views().run(run_id)
    if request.method == "GET":
        return render_template("run_export.html", run=run)
    format_ = request.form.get("format", "json")
    if format_ not in {"json", "csv", "html"} or "confirm_sensitive" not in request.form:
        flash("Choose a format and confirm the warning.", "error")
        return render_template("run_export.html", run=run)
    artifact = _run_exports().create_export(run_id, cast(ExportFormat, format_))
    try:
        response = send_file(
            artifact.path,
            mimetype=artifact.mimetype,
            as_attachment=True,
            download_name=artifact.download_name,
        )
    except BaseException:
        artifact.cleanup()
        raise
    response.response = ClosingIterator(
        cast(Iterable[bytes], response.response), [artifact.cleanup]
    )
    return response
