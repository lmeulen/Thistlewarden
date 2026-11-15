<a id="page-top"></a>

# Judge profiles

[Top of page](#page-top) · [Manual contents](README.md#manual-top)


### Creating an LLM judge profile

**Advanced feature:** finish one deterministic evaluation first. LLM judge makes extra provider call and can add cost, latency, and model variation.

Open **Judge profiles** and select **Add judge profile**. A judge execution profile defines only model and provider-call settings used by test cases with **LLM judge** method. Test cases separately select an immutable, explicitly versioned preset that owns rubric and threshold.

Complete these fields:

1. Enter unique, descriptive **Name**, such as `Quality judge — GPT-4 low temperature`.
2. Select configured **Model profile** for judge calls.
3. Set either low **Temperature** for conventional models or **Reasoning effort** (for example `low`, `medium`, or `high`) for reasoning models. Leave both empty to inherit model-profile inference settings. Reasoning effort uses reasoning field path configured on selected model profile.
4. Optionally set positive **Max. output tokens** for judge response.
5. Select **Active** when profile is ready. Optionally select **Global default**.
6. Select **Save**.

Create separate profiles when suites require different models or operational settings. Deactivate obsolete profiles rather than relying on users to avoid them. Profile edits affect new runs only: started run holds execution-profile snapshot.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Selecting a judge model

Judge model is selected from existing model profiles. It is a second model role: target profiles generate candidate outputs, while judge profile assesses them. Judge model can be same provider/model as target, but independent capable model is often better for reducing self-assessment bias.

Before selecting model, confirm profile and its endpoint are active, provider credentials work, model name is available, and expected judge cost is acceptable. Judge calls inherit selected model profile connection and request configuration. Judge profile can override temperature or reasoning effort, plus optional output-token limit, for judge call. Temperature and reasoning effort are mutually exclusive. Reasoning override requires selected model profile to define provider-specific reasoning field path such as `reasoning_effort` or `reasoning.effort`.

Choose model with reliable instruction following and structured JSON output. Start with low temperature, modest output limit, and small pilot run. Judge must produce strict response contract; malformed output causes evaluation error rather than inferred score. Provider rate limits, outages, or authentication errors can therefore affect LLM-judge results even if target model succeeded.

Avoid changing a commonly used judge profile without a tracked change decision. Create versioned replacement, run comparison, then make replacement active/default after review.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Prompt contract

Application uses fixed built-in judge request contract. It sends original prompt, preset-required expected value when present, resolved preset rubric, optional item context, and candidate answer as quoted untrusted evaluation data. It instructs judge model to return one JSON object only, with score, passed flag, reason, and exactly one result for each rubric criterion.

Judge execution profiles cannot supply or override prompt templates. Semantic prompt-contract changes require an application release and, where evaluation meaning changes, a new preset version.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Rubrics and scoring thresholds

Each registered preset defines ordered observable criteria, criterion weights, and an inclusive threshold. Judge returns criterion score from `0` to `1` and reason for every preset criterion. Thistlewarden calculates overall score as weighted mean of criterion scores, then compares calculated score with preset threshold. Judge-provided overall score and pass flag are retained for diagnostics but do not override calculated result.

Test cases and judge profiles cannot extend, replace, or override preset rubric or threshold. Calibrate each preset using known good, borderline, and unacceptable candidate answers before using it for release gates. Semantic changes require a new immutable preset version. See [section 9.6](evaluators.md#llm-judge) for available presets and required inputs.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Assigning judges to golden sets

A golden set selects judge with its **Judge profile** override. Open saved golden set, choose profile in **Override**, then select **Save judge override**. Select **Use global default** to remove set-specific choice.

Resolution for an LLM-judge case is:

1. Run-level judge override, when selected during run creation.
2. Golden-set judge override, when configured.
3. Active global-default judge profile.

Deterministic methods never use this selection. A golden set can contain both deterministic and LLM-judge cases; only latter add judge calls. Keep one global default only when it is approved for broad use. Selecting **Global default** on another profile clears default flag from previous profile.

Confirm selected judge is active and backed by available active model profile before starting run. Use set override for specialized domain rubric or lower-cost experimentation, and run override for one-off comparison without changing set configuration. Run captures resolved judge settings, so later assignment/profile changes do not alter historical results.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)
