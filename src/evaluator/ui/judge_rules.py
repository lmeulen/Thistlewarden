"""Judge-rule GUI routes: versioned evaluation semantics, not model settings."""

from __future__ import annotations

from decimal import Decimal

from evaluator.judging.definitions import (
    DEFAULT_GENERAL_METRIC_WEIGHTS,
    DEFAULT_PER_METRIC_PROMPTS,
    DEFAULT_SINGLE_PROMPT,
)
from evaluator.ui.shared import (
    METRIC_CATALOG,
    ApiError,
    Response,
    _error_message,
    _judge_definition_service,
    flash,
    gui,
    redirect,
    render_template,
    request,
    url_for,
)


def _payload() -> dict[str, object]:
    mode = request.form.get("mode", "single_prompt")
    metrics = []
    for metric_id in METRIC_CATALOG:
        enabled = metric_id in request.form.getlist("metrics")
        metrics.append(
            {
                "metric_id": metric_id,
                "enabled": enabled,
                "weight": request.form.get(f"weight_{metric_id}", "0"),
                "instructions": (
                    request.form.get(f"instructions_{metric_id}", "").strip() or None
                    if mode == "per_metric"
                    else None
                ),
            }
        )
    return {
        "name": request.form.get("name", ""),
        "description": request.form.get("description", "").strip() or None,
        "mode": mode,
        "output_mode": request.form.get("output_mode", "json_only"),
        "threshold": request.form.get("threshold", "0.7"),
        "general_instructions": (request.form.get("general_instructions", "").strip() or None),
        "metrics": metrics,
        "requires_expected": "requires_expected" in request.form,
        "requires_context": "requires_context" in request.form,
    }


@gui.get("/judge-rules")
def judge_rules_page() -> Response:
    return redirect(url_for("gui.judges_page"))


@gui.route("/judge-rules/new", methods=["GET", "POST"])
def judge_rule_new() -> str | Response:
    if request.method == "POST":
        try:
            record = _judge_definition_service().create(_payload())
            flash("Judge draft created.", "success")
            return redirect(url_for("gui.judge_rule_edit", definition_id=record.id))
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    return render_template(
        "judge_rule_form.html",
        judge=None,
        metrics=METRIC_CATALOG.values(),
        default_metric_weights=DEFAULT_GENERAL_METRIC_WEIGHTS,
        default_per_metric_prompts=DEFAULT_PER_METRIC_PROMPTS,
        default_single_prompt=DEFAULT_SINGLE_PROMPT,
        Decimal=Decimal,
    )


@gui.route("/judge-rules/<definition_id>", methods=["GET", "POST"])
def judge_rule_edit(definition_id: str) -> str | Response:
    service = _judge_definition_service()
    judge = service.get(definition_id)
    if request.method == "POST":
        try:
            judge = service.update_draft(definition_id, _payload())
            flash("Judge draft saved.", "success")
        except (ApiError, ValueError) as exc:
            flash(_error_message(exc), "error")
    versions = service.list_versions(definition_id)
    form_judge = judge
    if form_judge.draft is None and versions:
        form_judge = form_judge.model_copy(update={"draft": versions[-1].data})
    return render_template(
        "judge_rule_form.html",
        judge=form_judge,
        versions=versions,
        next_version=service.next_version(definition_id),
        metrics=METRIC_CATALOG.values(),
        default_metric_weights=DEFAULT_GENERAL_METRIC_WEIGHTS,
        default_per_metric_prompts=DEFAULT_PER_METRIC_PROMPTS,
        default_single_prompt=DEFAULT_SINGLE_PROMPT,
        Decimal=Decimal,
    )


@gui.post("/judge-rules/<definition_id>/publish")
def judge_rule_publish(definition_id: str) -> Response:
    try:
        version = request.form.get("version", "")
        published = _judge_definition_service().publish(definition_id, version)
        flash(
            f"Judge version {published.version} published. Earlier results remain unchanged "
            "and auditable.",
            "success",
        )
    except ApiError as exc:
        flash(_error_message(exc), "error")
    return redirect(url_for("gui.judge_rule_edit", definition_id=definition_id))
