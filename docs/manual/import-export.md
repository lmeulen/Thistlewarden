<a id="page-top"></a>

# Import, export, and backup

[Top of page](#page-top) · [Manual contents](README.md#manual-top)


### Importing golden sets

Import reusable test material from **Golden sets** by selecting **Import JSON/CSV**. Choose **JSON** or **CSV**, choose file, and select **Validate and preview** before committing.

JSON can carry golden-set metadata and test cases. CSV imports test cases and requires **Name for CSV** to create the destination golden set. Imported cases are validated using the same rules as manually created cases, including evaluator method and configuration validation.

After successful validation, select file again and choose **Complete import**. Import creates a new golden set with new internal identifiers; it does not update an existing set. Open created set to review metadata, case order, evaluator configuration, activation, judge override, and quality gates before using it in a run.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Previewing import changes

Golden-set preview validates file without writing data. It reports name, version, and number of test cases. Use it to catch malformed JSON or CSV, missing required values, invalid evaluator configurations, duplicate case IDs, and size-limit issues before import.

Preview is not a reservation or staged upload. It does not retain file content. Select the file again to complete import, and expect current validation and name-conflict checks to run again at commit time.

For full application configuration, **Configuration exchange** preview additionally reports mode, new-object count, records to archive, UUID conflicts/remaps, and endpoints missing secrets. Review all counts and warnings before committing.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Exporting golden sets

Open a saved golden set. In **Testcases**, select **JSON** or **CSV** to download it.

- **JSON** exports golden-set metadata and test cases. Choose it for a portable suite with name, version, description, tags, activation state, and evaluator definitions.
- **CSV** exports flat test-case data. Choose it for spreadsheet review or bulk editing. Provide a destination set name when importing CSV later.

Exports contain test material, including prompts, expected answers, evaluator configurations, and potentially sensitive content. Store them only in approved locations, restrict access, and review changes before importing edited files. A golden-set export is not a complete application backup: it does not include endpoints, model profiles, judges, run history, or secret values.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Exporting run results

Open run detail and select **Export**. Select one format, acknowledge sensitive-content warning, then select **Download**.

| Format | Use |
|---|---|
| Complete JSON | Machine-readable full run record and results. Best for archival or downstream processing. |
| Flat CSV results | Tabular per-result data for spreadsheet analysis. |
| Offline HTML report | Portable human-readable report for review without application access. |

Run exports may contain prompts, system prompts, expected answers, candidate model answers, evaluator output, performance measurements, and costs. Confirmation is required because files can expose sensitive evaluation data. Encrypt or access-control exported files according to policy; do not attach them to public tickets or commit them to source control.

Export before permanently deleting a run. An export is a point-in-time copy and does not update after future configuration changes.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Exporting and importing application configuration

Open **Configuration exchange** to download or import a version `1.0` JSON configuration bundle.

Configuration export includes application configuration objects: endpoints, model profiles, judge profiles, golden sets, and golden-set items. It excludes secret values. API keys and secret additional-header values are represented as missing rather than exported. It also is not a run-history export.

To import:

1. Select a JSON configuration file.
2. Choose **Merge** or **Replace and archive existing records**.
3. Select **Preview** and inspect counts, conflicts/remaps, archives, and missing secrets.
4. For replace mode, explicitly confirm archiving.
5. Select **Import preview** to commit.
6. Add required API keys and secret headers through endpoint management before provider calls.

Import commits atomically: valid configuration changes are applied together, or no partial bundle is written. Imported endpoints with missing secrets cannot make provider calls until secrets are entered locally.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Conflict handling

Golden-set import creates a new set, so destination golden-set name must be unique. If name conflicts, rename source set or use another CSV destination name, validate again, and retry. Import validation also rejects duplicate case IDs within imported file without regard to letter case.

Configuration **Merge** adds imported objects. When incoming internal UUID already exists locally, Thistlewarden remaps incoming UUID and references to new UUIDs, shown in preview as conflicts/remaps. This avoids overwriting existing objects by identifier. Name or other database uniqueness conflicts can still prevent commit; resolve them in source or local configuration and retry.

Configuration **Replace** does not delete active existing endpoints, model profiles, judge profiles, or golden sets. It archives them by deactivating and renaming them, then imports bundle. This protects historical references while removing archived records from normal selection. Replace requires explicit confirmation because it changes active operational configuration.

Always preview current file against target environment immediately before committing. Treat cross-environment imports as change-controlled operations, especially where names, active state, provider URLs, or pricing differ.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Backing up application data

**Operator task:** use your organization-approved backup procedure. See also [configuration guide](../configuration.md) for data paths and retention settings.

Use exports for portable sharing, but use database backup for recoverable application state. A complete backup must preserve application data and secret storage together. Keep backup encrypted and access-controlled because database may contain credentials and sensitive prompts, answers, and run results.

Recommended process:

1. Stop application or ensure database backup method produces a consistent SQLite snapshot.
2. Copy persistent application data volume or database file to protected backup storage.
3. Record application version and backup time.
4. Regularly test restoration in an isolated environment.
5. Restore data and secret storage together; then verify endpoints, model profiles, golden sets, judge profiles, and sample run access.

Do not rely on configuration export alone: it intentionally omits secret values and run history. Do not rely on run or golden-set exports alone: each contains only a subset of data. Define retention, encryption, access, and restore-testing practices that meet your organization's requirements.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

[Top of page](#page-top) · [Manual contents](README.md#manual-top)
