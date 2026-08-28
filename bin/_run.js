"use strict";
const { spawnSync } = require("child_process");
const path = require("path");

module.exports = function run(mod) {
  const src = path.join(__dirname, "..", "src");
  for (const py of ["python3", "python"]) {
    const r = spawnSync(py, ["-m", mod, ...process.argv.slice(2)], {
      stdio: "inherit",
      env: { ...process.env, PYTHONPATH: src + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : "") },
    });
    if (r.error && r.error.code === "ENOENT") continue;   // try the next interpreter
    process.exit(r.status === null ? 1 : r.status);
  }
  console.error("nightshift: no python3 on PATH (needs Python 3.9+; no other dependencies)");
  process.exit(127);
};
