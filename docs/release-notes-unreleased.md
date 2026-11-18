# Unreleased changes

## Run exports

Run exports now use bounded database batches and restrictive temporary files instead of materializing complete runs and serialized output in process memory. Web downloads remove temporary artifacts when response transfer closes; command-line exports copy them in bounded chunks and remove them afterward. Synchronous exports reject runs above 50,000 jobs with guidance to split evaluation into smaller runs.

JSON remains complete and versioned, with deterministic section and row ordering. CSV remains one deterministic row per result. HTML remains a human-readable run summary and result table, but no longer embeds a second complete JSON representation. Use the separate JSON export when complete machine-readable snapshots, jobs, attempts, results, and quality gates are required.
