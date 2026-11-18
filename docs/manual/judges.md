<a id="page-top"></a>

# Judges

[Top of page](#page-top) · [Manual contents](README.md#manual-top)


### Evaluation rules: metrics, instructions, and assessment method

Open **Judges** and use **Evaluation rules** to create versioned rules separately from judge-model settings. A test case selects one published Judge version. That version contains enabled metrics, weights, instructions, pass threshold, and assessment method.

Choose one assessment method before publishing:

- **One assessment for all metrics** sends one judge call containing all enabled metrics. It shows one free **Prompt** field followed by read-only **Fixed output requirements**. Thistlewarden appends those requirements to your prompt to form the single provider prompt.
- **Separate assessment per metric** makes one sequential judge call for each enabled metric. Every active metric starts with editable, metric-specific guidance covering evidence, scope, optional expected answers or context, five score anchors, and boundaries with related metrics. Review these defaults as starting points rather than assuming they guarantee deterministic scores. Thistlewarden combines resulting metric scores using configured weights.

Switching assessment methods in editor does not overwrite text already entered in current form. Saving and reopening an existing separate-assessment draft preserves its instructions. Calibrate edited prompts with known fully acceptable, borderline, partial, and unacceptable answers before publishing.

Thistlewarden always owns and adds fixed prompt parts: safety boundaries, quoted testcase data, code-owned metric definitions, and strict JSON response contract. Your instructions are evaluation data inside that wrapper; they cannot change metric IDs, score ranges, aggregation, provider settings, or output schema. Publish the next Judge version for any semantic change, including changes to default or customized metric instructions; editor proposes next minor version and shows published history. Existing drafts are not rewritten when code-owned defaults change. Existing published versions and run snapshots remain unchanged.

### Creating an LLM judge profile

**Advanced feature:** finish one deterministic evaluation first. LLM judge makes extra provider call and can add cost, latency, and model variation.

On **Judges**, use **Judge profiles** and select **Add judge profile**. A judge execution profile defines only model and provider-call settings used by test cases with **LLM judge** method. Test cases separately select an immutable, explicitly versioned user-created Judge that owns metrics, weights, instructions, assessment method, and threshold.

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

Choose model with reliable instruction following and structured JSON output. Start with low temperature, modest output limit, and small pilot run. Judge must produce strict response contract; malformed output causes evaluation error rather than inferred score. Provider rate limits, outages, or authentication errors can therefore affect LLM-judge results even if target model succeeded. If a separate per-metric assessment fails after earlier calls completed, the testcase remains an incomplete error with no partial score or pass decision. Safe usage, request IDs, finish reasons, and known cost from completed Judge calls remain available for audit.

Avoid changing a commonly used judge profile without a tracked change decision. Create versioned replacement, run comparison, then make replacement active/default after review.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Prompt contract

Application wraps user-authored Judge instructions in a fixed request contract. It sends original prompt, optional expected value, Judge metrics, optional item context, and candidate answer as quoted untrusted evaluation data. It instructs judge model to return one JSON object only, with score, passed flag, reason, and exactly one result for each enabled metric.

Judge-model settings cannot supply or override prompts. Change evaluation instructions under **Judges** → **Evaluation rules** and publish a new Judge version. Core-owned safety boundaries and output contract cannot be overridden.

[Top of page](#page-top) · [Manual contents](README.md#manual-top)

### Rubrics and scoring thresholds

Each published Judge version defines ordered metrics, metric weights, and an inclusive threshold. Judge returns a score from `0` to `1` and reason for every enabled metric. Thistlewarden calculates overall score as weighted mean of metric scores, then compares calculated score with Judge threshold. Judge-provided overall score and pass flag are retained for diagnostics but do not override calculated result.

Thistlewarden ships no selectable default Judges. Create a Judge, calibrate it using known good, borderline, and unacceptable candidate answers, then publish it. Semantic changes require a new immutable Judge version. Historical test cases using former built-in presets remain readable and retain their original semantics.

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
