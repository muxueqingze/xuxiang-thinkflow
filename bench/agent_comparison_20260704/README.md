# Agent Comparison Benchmark 2026-07-04

This folder contains a reproducible comparison scaffold for two tasks:

- `frontend_app`: build the Agent Efficiency Lab React/Vite project from the prompt on the desktop.
- `novel_five_chapters`: read a long outline and write five chapters in a fresh session.

Agents:

- `claude-code`
- `thinkflow`

`pi` support exists in the runner, but the current benchmark report uses only
Claude Code and ThinkFlow because the local `pi` run did not finish reliably in
the first smoke run.

Run records, raw logs, generated projects, and summary metrics are written under `runs/`.
