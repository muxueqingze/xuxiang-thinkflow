# ThinkFlow Command Execution Queue

## Purpose

ThinkFlow parses executable `<tf-*>` tags from a streaming model response. The model can emit text much faster than tools can safely run, so parsed commands must not be handed to the executor as independent background tasks.

The execution queue preserves ThinkFlow's core contract:

- The parser accepts only complete, valid commands.
- The dispatcher keeps command order exactly as the model emitted it.
- The executor receives only `Command` objects, never raw model text.
- Tool bodies remain part of the command ledger/global context.
- Fast model output creates queue depth, not concurrent dependent side effects.

## Components

### Parser

`StreamingParser` scans the stream and emits complete `Command` objects. It owns syntax validation, id validation, required attributes, duplicate-id rejection, and incomplete-tag detection.

If parsing fails, the current model turn is interrupted and a parser error is injected into the next API call. Commands already accepted before the parser error keep their normal ledger receipts.

### Dispatcher

`AgentLoop._dispatch_text_commands()` is the boundary between parsing and execution. It does not parse raw text. It receives parser-approved `Command` objects and either:

- enqueues normal commands into `CommandExecutionQueue`, or
- waits for the queue barrier before executing a `need_result=true` command.

### CommandExecutionQueue

`CommandExecutionQueue` is a single-worker FIFO queue.

Rules:

- One command runs at a time.
- Commands execute in stream order.
- `barrier()` waits for all commands enqueued so far.
- `close()` drains the queue at turn end.
- The first failed command becomes the queue failure result.
- After a failure, later queued commands are recorded as `status="skipped"` and are not executed.
- Skipped commands are cancellation receipts, not side effects.

This prevents dependency races such as:

```xml
<tf-bash id="1" cmd="npm install" />
<tf-bash id="2" cmd="npm run build" />
```

The model may output both tags in one fast stream chunk, but `npm run build` cannot start until `npm install` has finished successfully. If `npm install` fails, `npm run build` is recorded as skipped and never runs.

## Transaction Feedback

Command tags are executable protocol, not durable assistant prose:

- commands emitted in the thinking/reasoning stream are parsed but are not stored as assistant messages;
- commands emitted in the visible text fallback are stripped from assistant history before the message is saved;
- the next model call learns tool state from ThinkFlow's injected ledger/error messages, not from raw command tags in assistant history.

Therefore the queue must report transaction boundaries explicitly. When a queued command fails, the failure feedback includes:

- commands that completed before the failure (`success` ledger entries),
- the failed command (`failed`),
- commands that had already been emitted and queued but were not executed (`skipped`).

The model should treat skipped commands as abandoned patches: they produced no side effects and must be re-planned with new ids if still needed.

### Executor

`Executor` performs the actual side effect for each command. It does not know about stream buffering or parser state. It reports `ExecutionResult`, and the context ledger records the result with flow/risk/hash/summary and tool body where applicable.

## Blocking Semantics

`need_result=true` is a barrier:

1. Wait for all earlier queued commands.
2. If any earlier command failed, interrupt with `tool_failed`.
3. Execute the result-bearing command.
4. Inject the result into the next model call.

Information-bearing tools such as `tf-read` are also treated as blocking by flow metadata, so successful output can be injected automatically when required by the agent loop.

## Cache Semantics

The queue does not remove command bodies from context. Tool outputs and write/edit bodies remain in the command ledger because they are part of ThinkFlow's global context design.

Cost control should come from cache-friendly request layout:

- stable system prompt,
- stable historical prefix,
- dynamic runtime state appended at the request tail,
- error feedback appended after the cached prefix.

Do not reduce cost by deleting tool bodies from global context.
