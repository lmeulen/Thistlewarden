"""Compatibility imports for server-rendered GUI.

New route code lives in :mod:`evaluator.ui` domain modules.
"""

from evaluator.ui import csrf_token, gui
from evaluator.ui.runs import (
    _cost_score_scatter,
    _model_chooser,
    _overview_model_charts,
    _quality_gate_progress,
    _sort_model_comparison,
)

__all__ = [
    "_cost_score_scatter",
    "_model_chooser",
    "_overview_model_charts",
    "_quality_gate_progress",
    "_sort_model_comparison",
    "csrf_token",
    "gui",
]
