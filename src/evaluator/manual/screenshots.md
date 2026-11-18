<a id="page-top"></a>

# Recreating manual screenshots

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

Manual screenshots use one deterministic, documentation-only fixture. Fixture names and results are synthetic and do not represent provider endorsements or benchmark results. It contains no credential, API key, reachable provider URL, real prompt, real answer, request ID, or personal data.

## Capture standard

Use these settings for every image:

- current application version from repository checkout;
- desktop viewport: **1440 × 1000 CSS pixels**;
- browser zoom: **100%**;
- device-pixel ratio: **1**;
- theme: **dark**;
- PNG output without browser chrome, cursor, focus ring, flash message, or loading state;
- full 1440-pixel width, cropped only below relevant page content;
- lossless PNG encoding; do not resize text after capture.

## Prepare fixture

From repository root, create a fresh database outside repository:

```bash
PYTHONPATH=src python scripts/prepare_manual_screenshot_fixture.py /tmp/thistlewarden-manual.db
```

Script refuses to overwrite an existing file. Start one loopback-only application process with that database, workers disabled, and fresh strong local `SECRET_KEY` and `ENCRYPTION_KEY` values. Do not add a credential or enable provider calls. Fixture provider URLs use reserved `.invalid` domain.

Open target page, set dark theme, confirm zoom and viewport, wait for fonts/layout to settle, then capture relevant task state. Evaluator images use corresponding fixture test case in **Form fields** mode. Keep general test-case fields visible so reader remains oriented. Crop only unused space below page; retain header and page title.

## Inventory

Capture and retain:

- `dashboard.png`: dashboard cards and latest synthetic run;
- `endpoints.png`: synthetic endpoint and collapsed model list;
- `runs.png`: completed synthetic run list;
- `test-case.png`: general test-case fields and evaluator selection;
- `evaluation-exact-match.png`, `evaluation-contains.png`, `evaluation-regex.png`, `evaluation-numeric.png`, and `evaluation-json-schema.png`: current deterministic evaluator forms;
- `evaluation-llm-judge.png`: current LLM Judge testcase fields.

After capture, copy each image to both `docs/manual/images/` and `src/evaluator/manual/images/` while duplicate manual packaging remains supported. Verify both trees are byte-identical, every Markdown reference resolves, removed files have no references, and manual rendering/asset tests pass. Delete fixture database, browser profile, and uncropped temporary images; never commit them.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)
