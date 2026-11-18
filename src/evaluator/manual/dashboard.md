<a id="page-top"></a>

# Dashboard

[Top of page](#page-top) · [Manual contents](README.md#manual-top)


## Available statistics

The dashboard is the application home page. It provides a read-only summary of evaluation activity, configured model access, and available test material. The summary updates when you refresh the page.

![Dashboard with evaluation, configuration, and test-material summaries](images/dashboard.png)

*Dashboard overview. Screenshots use synthetic demonstration data.*

It has three cards:

- **Evaluate** shows run counts:
  - **Total runs**: every run retained in the application.
  - **Active runs**: runs that are pending or running.
  - **Finished runs**: runs that completed, completed with errors, failed, or were cancelled.
- **Configure** shows model-access counts:
  - **Active model profiles**: profiles that are active and whose endpoint is also active.
  - **Active endpoints**: enabled provider endpoints.
  - **Total model profiles**: all profiles, including inactive profiles and profiles under inactive endpoints.
- **Test material** shows golden-set counts:
  - **Enabled test cases**: cases that are enabled and belong to an active golden set.
  - **Active golden sets**: enabled golden sets.
  - **Total test cases**: all cases, including disabled cases and cases in inactive golden sets.

A new installation displays zero for every value. This is normal until you configure endpoints, profiles, golden sets, and runs.

## First-pilot guidance

New or incomplete installations show an ordered **Run your first pilot evaluation** checklist above dashboard statistics. Its state comes from application records and successful actions stored in local database, so progress survives browser changes and application restarts. It does not depend on browser storage.

Checklist covers:

1. adding an active provider endpoint;
2. successfully testing an active model profile;
3. creating or importing an active golden set with an enabled test case;
4. successfully previewing a deterministic evaluator;
5. starting a run with exactly one model; and
6. opening a result from a completed run.

Each incomplete step links to its primary action and explains its blocker. A completed historical workflow prevents onboarding from being imposed on an existing installation.

Select **Dismiss guidance** to hide checklist temporarily. Dashboard then offers **Restore pilot guidance** until workflow is complete.

Select **Mark as done** when you no longer need start guidance. This persistent presentation preference hides checklist across browser reloads and application restarts. It does not complete any record-derived step, create endpoint/model/test/run records, bypass validation or blockers, or indicate setup, readiness, or evaluation success. Dashboard shows **Pilot guidance marked as done** and offers **Reopen pilot guidance**. Reopening displays current record-derived progress. Dismiss, mark-as-done, restore, and reopen choices are stored in local database.

Checklist optionally creates **Synthetic pilot examples**, an editable and removable golden set with three deterministic cases. This action creates no endpoint, credential, provider call, paid call, or judge call. Starter examples demonstrate workflow only; they do not establish model quality or production readiness. Starting pilot run remains normal run flow, including cost preview and sensitive-data warning.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Interpreting totals and active-item counts

The dashboard distinguishes total records from records currently available for new work.

**Total** counts are inventory counts. They include inactive, disabled, completed, cancelled, and historical records where relevant. Use them to understand how much configuration and test material the application contains.

**Active** and **enabled** counts show records eligible for normal use:

- A model profile counts as active only when both the profile and its parent endpoint are active.
- A test case counts as enabled only when both the case and its parent golden set are active.
- A run remains in the total after it finishes, fails, or is cancelled; it no longer counts as active.

These rules explain apparent differences between totals and usable records. For example, activating a model profile does not make it usable if its endpoint is inactive. Likewise, enabling a test case does not include it in a new run while its golden set is inactive.

Use the dashboard as a quick readiness check before creating a run. If active model profiles or enabled test cases are zero, open the linked configuration page and check the parent record as well as the child record.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

## Navigation shortcuts

Each dashboard card has a button that opens the page associated with its summary:

- **View runs** opens **Runs**, where you create, monitor, inspect, export, and manage evaluation runs.
- **View endpoints** opens **Endpoints**, where you manage provider endpoints and their model profiles.
- **View golden sets** opens **Golden sets**, where you manage test suites and test cases.

Use these buttons when a dashboard count identifies missing setup or work requiring attention. For example:

- select **View endpoints** when no active model profiles are available;
- select **View golden sets** when no enabled test cases are available;
- select **View runs** to inspect active runs or compare completed work.

The main navigation bar remains available on every page. Use it to move directly between the dashboard, endpoints, golden sets, judges, runs, settings, and configuration exchange.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)
