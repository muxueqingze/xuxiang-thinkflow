"use strict";

const { spawnSync } = require("child_process");
const path = require("path");

if (process.env.THINKFLOW_SKIP_PY_DEPS === "1") {
  console.log("ThinkFlow: skipping Python dependency installation.");
  process.exit(0);
}

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
  console.error("ThinkFlow requires Python 3.12+. Install Python or set THINKFLOW_PYTHON.");
  process.exit(1);
}

const root = path.resolve(__dirname, "..");
const requirements = path.join(root, "requirements.txt");
const pipArgs = [...python.args, "-m", "pip", "install"];
if (!process.env.VIRTUAL_ENV && process.env.THINKFLOW_PIP_USER !== "0") {
  pipArgs.push("--user");
}
pipArgs.push("-r", requirements);

console.log("ThinkFlow: installing Python dependencies...");
const install = spawnSync(python.cmd, pipArgs, { stdio: "inherit" });
if (install.error) {
  console.error(install.error.message);
  process.exit(1);
}
process.exit(install.status === null ? 1 : install.status);
