#!/usr/bin/env node
"use strict";

const { spawnSync } = require("child_process");
const path = require("path");

function candidates() {
  if (process.env.THINKFLOW_PYTHON) {
    return [{ cmd: process.env.THINKFLOW_PYTHON, args: [] }];
  }
  const result = [
    { cmd: "python", args: [] },
    { cmd: "python3", args: [] }
  ];
  if (process.platform === "win32") {
    result.push({ cmd: "py", args: ["-3.12"] });
  }
  return result;
}

function findPython() {
  for (const candidate of candidates()) {
    const probe = spawnSync(
      candidate.cmd,
      [
        ...candidate.args,
        "-c",
        "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
      ],
      { stdio: "ignore" }
    );
    if (probe.status === 0) {
      return candidate;
    }
  }
  return null;
}

const python = findPython();
if (!python) {
  console.error("ThinkFlow requires Python 3.12+. Set THINKFLOW_PYTHON to a compatible interpreter.");
  process.exit(1);
}

const root = path.resolve(__dirname, "..");
const script = path.join(root, "run.py");
const env = {
  ...process.env,
  PYTHONIOENCODING: process.env.PYTHONIOENCODING || "utf-8",
  PYTHONUTF8: process.env.PYTHONUTF8 || "1"
};
const child = spawnSync(
  python.cmd,
  [...python.args, script, ...process.argv.slice(2)],
  { stdio: "inherit", env }
);

if (child.error) {
  console.error(child.error.message);
  process.exit(1);
}
process.exit(child.status === null ? 1 : child.status);
