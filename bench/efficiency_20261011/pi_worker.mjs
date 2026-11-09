/** Observations only: retain Pi's native prompt, tools, history and provider payload. */
import { writeFileSync } from 'node:fs';

export default function (pi) {
  const started = performance.now();
  const events = [];
  const tool_records = [];
  let last_error = null;
  let stopped_reason = null;
  function save() {
    writeFileSync(process.env.BENCH_RECORD, JSON.stringify({events, tool_records, last_error, stopped_reason}, null, 2));
  }
  function record(value) {
    events.push({...value, seconds: (performance.now() - started) / 1000});
    save();
  }
  pi.on('turn_start', (event) => record({type: 'stream_started', turn: event.turnIndex}));
  pi.on('tool_execution_start', (event) => record({type: 'tool_started', id: event.toolCallId,
    tool: event.toolName, cmd: event.args.command || '', path: event.args.path || ''}));
  pi.on('tool_execution_end', (event) => {
    const output = event.result.content.filter((block) => block.type === 'text').map((block) => block.text).join('\n');
    const detail = event.result.details || {};
    tool_records.push({id: event.toolCallId, tool: event.toolName,
      status: event.isError ? 'failed' : 'success', exit_code: detail.exitCode ?? detail.exit_code ?? null,
      stdout: output, stderr: '', result: event.result});
    record({type: 'tool_completed', id: event.toolCallId, tool: event.toolName, success: !event.isError, flow: 'blocking'});
  });
  pi.on('turn_end', (event) => {
    if (event.message.stopReason === 'error' || event.message.stopReason === 'aborted') {
      last_error = event.message.errorMessage || event.message.stopReason;
    }
    record({type: 'stream_finished', turn: event.turnIndex});
  });
  pi.on('agent_end', () => { stopped_reason = last_error ? 'error' : 'completed'; save(); });
}
