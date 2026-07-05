# Controlled Same-Prompt Benchmark v0.5

This benchmark is the clean, reproducible successor to the early ad hoc TK/CC comparison.

It fixes the user prompt and success criteria first, then records each agent run as data. The analyzer reads saved run files only; it does not call any model provider.

## Principle

The main variable should be the agent harness, not the task.

Hard controls:

- Use exactly `inputs/prompt.md`.
- Start each agent in a fresh empty output directory.
- Do not manually repair files during a run.
- Record the provider, model, system-prompt policy and cache policy.
- Run the same delivery checks for every artifact.

Two tiers are allowed:

- **Harness-controlled tier**: 续想/ThinkFlow vs `baseline_agent` through the same model endpoint where possible.
- **Product-comparison tier**: 续想/ThinkFlow vs Claude Code or other products. This is end-to-end product evidence; if the model differs, the report must say so.

## Directory Layout

```text
inputs/prompt.md
manifest.json
runs/
reports/
analyze.py
run_record.example.json
```

Each real run should be saved as:

```text
runs/<agent-id>.json
```

Use stable ids such as:

- `thinkflow.json`
- `baseline.json`
- `claude-code.json`

## Required Run Record

Each run JSON should follow `run_record.example.json`:

- agent identity and version
- model/provider endpoint family
- output directory
- usage counters
- build/delivery result
- notes for any deviation

Do not store API keys, raw local config or private logs.

## Analyze

```bash
python bench/controlled_same_prompt_v0.5/analyze.py
```

The script prints a table and writes:

```text
reports/summary.md
```

## Interpreting Results

Primary metrics:

- API calls
- total tokens
- effective input tokens, defined as `prompt_tokens - cached_tokens`
- completion tokens
- wall time
- delivery compliance

Cost comparisons should report both:

- raw total tokens
- cached-free estimate: `effective_input_tokens + completion_tokens`

Delivery compliance matters. A run that ignores the requested output directory or fails `npm run build` is not equivalent even if it uses fewer tokens.
