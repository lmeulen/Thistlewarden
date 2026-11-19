from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from tests.integration.test_run_queue import FakeClock, fixture_app

from evaluator.runs import LeaseLost, RunQueue

pytestmark = pytest.mark.integration


def test_only_expired_utc_lease_recovers_with_new_generation(tmp_path: Path) -> None:
    app, golden, model = fixture_app(tmp_path)
    clock = FakeClock()
    queue = RunQueue(app.extensions["database_factory"], clock=clock, lease_seconds=10)
    run_id = queue.create_run(
        {"name": "recovery", "golden_set_id": str(golden.id), "model_profile_ids": [str(model.id)]}
    )
    old = queue.claim("old")
    assert old is not None
    assert queue.recover_expired() == {"runs": 0, "jobs": 0}
    assert queue.claim("new") is None
    clock.now += timedelta(seconds=11)
    assert queue.recover_expired() == {"runs": 1, "jobs": 1}
    # Scan preserves old owner/generation; next claim performs generation bump.
    new = queue.claim("new")
    assert new is not None and new.generation == old.generation + 1
    with pytest.raises(LeaseLost):
        queue.complete(old, status="passed", score="1", weight="1", result={})
    queue.complete(new, status="passed", score="1", weight="1", result={})
    run = queue.get_run(run_id)
    assert run["recovery_warning"] is True
    assert run["recovery_message"] == (
        "Interrupted provider calls may have been repeated and caused duplicate costs."
    )
