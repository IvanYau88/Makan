import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";
import { backendArguments, findPython } from "./run.mjs";

const root = path.resolve("/repo");
const existing =
  (...files) =>
  (file) =>
    files.includes(file);

test("finds the repository's .venv Python on Windows and elsewhere", () => {
  const windows = path.join(root, ".venv", "Scripts", "python.exe");
  const posix = path.join(root, ".venv", "bin", "python");
  assert.equal(
    findPython({ root, platform: "win32", exists: existing(windows) }),
    windows,
  );
  assert.equal(
    findPython({ root, platform: "linux", exists: existing(posix) }),
    posix,
  );
  assert.equal(
    findPython({ root, platform: "darwin", exists: existing(posix) }),
    posix,
  );
});

test("a Windows layout is not mistaken for a POSIX one", () => {
  const posix = path.join(root, ".venv", "bin", "python");
  assert.equal(
    findPython({ root, platform: "win32", exists: existing(posix) }),
    null,
  );
});

test("prefers .venv, then venv, then the active virtual environment", () => {
  const python = (dir) => path.join(dir, "bin", "python");
  const all = existing(
    python(path.join(root, ".venv")),
    python(path.join(root, "venv")),
    python("/active"),
  );
  assert.equal(
    findPython({ root, platform: "linux", exists: all, virtualEnv: "/active" }),
    python(path.join(root, ".venv")),
  );
  assert.equal(
    findPython({
      root,
      platform: "linux",
      exists: existing(python(path.join(root, "venv"))),
    }),
    python(path.join(root, "venv")),
  );
  assert.equal(
    findPython({
      root,
      platform: "linux",
      exists: existing(python("/active")),
      virtualEnv: "/active",
    }),
    python("/active"),
  );
});

test("reports nothing when there is no environment", () => {
  assert.equal(
    findPython({ root, platform: "linux", exists: () => false }),
    null,
  );
});

test("the backend reloads only in development, and watches only the source", () => {
  const dev = backendArguments({ reload: true, root });
  assert.deepEqual(dev.slice(0, 4), [
    "-m",
    "uvicorn",
    "--factory",
    "makan.web:create_app_from_env",
  ]);
  assert.deepEqual(dev.slice(-3), [
    "--reload",
    "--reload-dir",
    path.join(root, "src"),
  ]);
  assert.ok(!backendArguments({ reload: false, root }).includes("--reload"));
});
