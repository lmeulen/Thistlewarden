"""Domain-focused server-rendered GUI route modules."""

# Explicit imports attach each domain's routes to shared GUI blueprint.
from evaluator.ui import backup as backup
from evaluator.ui import dashboard as dashboard
from evaluator.ui import endpoints as endpoints
from evaluator.ui import golden_sets as golden_sets
from evaluator.ui import judges as judges
from evaluator.ui import runs as runs
from evaluator.ui.shared import csrf_token, gui

__all__ = ["csrf_token", "gui"]
