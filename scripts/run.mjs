// Starts Makan with one command, from the repository root:
//
//   npm run dev     the backend with auto reload, and the front end dev server, together
//   npm start       builds the page, then runs only the backend, which serves it
//
// Add `-- --demo` to either to run with sample places, no API key and no network.
// This file has no dependencies and no shell syntax, so it behaves the same in cmd, PowerShell,
// macOS and Linux. The backend loads `.env` itself (see src/makan/env.py).

import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const HOST = "127.0.0.1";
const PORT = "8000";
const INSTALL_PYTHON = '-m pip install -e ".[web,overture]"';
const INSTALL_NODE = "npm --prefix web ci";

/** Where a virtual environment keeps its Python on this platform, relative to the environment. */
export function pythonInEnvironment(platform) {
  return platform === "win32"
    ? path.join("Scripts", "python.exe")
    : path.join("bin", "python");
}

/** The project's Python: `.venv` or `venv` in the repository, else the environment that is active. */
export function findPython({
  root,
  platform,
  exists = existsSync,
  virtualEnv = "",
}) {
  const environments = [path.join(root, ".venv"), path.join(root, "venv")];
  if (virtualEnv) environments.push(virtualEnv);
  return (
    environments
      .map((directory) => path.join(directory, pythonInEnvironment(platform)))
      .find((candidate) => exists(candidate)) ?? null
  );
}

/** The uvicorn command line for the backend. Reload only makes sense while developing. */
export function backendArguments({ reload, root }) {
  const args = ["-m", "uvicorn", "--factory", "makan.web:create_app_from_env"];
  args.push("--host", HOST, "--port", PORT);
  if (reload) args.push("--reload", "--reload-dir", path.join(root, "src"));
  return args;
}

function fail(...lines) {
  console.error(lines.join("\n"));
  process.exit(1);
}

function checkSetup() {
  const python = findPython({
    root: ROOT,
    platform: process.platform,
    virtualEnv: process.env.VIRTUAL_ENV ?? "",
  });
  const relative = (file) => path.relative(ROOT, file) || file;
  if (python === null) {
    const venvPython = relative(
      path.join(ROOT, ".venv", pythonInEnvironment(process.platform)),
    );
    fail(
      "Makan is not set up yet: no Python virtual environment (.venv) was found.",
      "Set it up once, from the repository root:",
      "  python -m venv .venv",
      `  ${venvPython} ${INSTALL_PYTHON}`,
      `  ${INSTALL_NODE}`,
    );
  }
  const imports = spawnSync(
    python,
    ["-c", "import fastapi, uvicorn, makan.web"],
    {
      stdio: "ignore",
    },
  );
  if (imports.status !== 0) {
    fail(
      `The Python dependencies are not installed in ${relative(python)}.`,
      `Run: ${relative(python)} ${INSTALL_PYTHON}`,
    );
  }
  if (!existsSync(path.join(ROOT, "web", "node_modules", "vite"))) {
    fail(
      "The front end dependencies are not installed.",
      `Run: ${INSTALL_NODE}`,
    );
  }
  return python;
}

// On POSIX each child leads its own process group, so `stop` can end it and everything it started.
const DETACHED = process.platform !== "win32";

function npm(args) {
  const options = { cwd: ROOT, stdio: "inherit", detached: DETACHED };
  // npm is a .cmd file on Windows, which only a shell can start. The arguments are fixed text.
  return process.platform === "win32"
    ? spawn(`npm ${args.join(" ")}`, { ...options, shell: true })
    : spawn("npm", args, options);
}

function backend(python, { reload, env }) {
  return spawn(python, backendArguments({ reload, root: ROOT }), {
    cwd: ROOT,
    stdio: "inherit",
    detached: DETACHED,
    env,
  });
}

function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === "win32") {
    // Kill the whole tree: the shell, npm and node, or uvicorn and its reload worker.
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
    });
  } else {
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch {
      // the group is already gone
    }
  }
}

/** Run the children together: when one ends or the user interrupts, stop the rest. */
function supervise(children) {
  let stopping = false;
  const stopAll = (code) => {
    if (!stopping) process.exitCode = code;
    stopping = true;
    children.forEach(stop);
  };
  for (const child of children) {
    child.on("error", (error) => {
      console.error(`Could not start a process: ${error.message}`);
      stopAll(1);
    });
    child.on("exit", (code) => stopAll(code ?? 1));
  }
  for (const signal of ["SIGINT", "SIGTERM", "SIGBREAK"])
    process.on(signal, () => stopAll(0));
}

function build() {
  return new Promise((resolve) => {
    npm(["--prefix", "web", "run", "build"]).on("exit", (code) =>
      resolve(code ?? 1),
    );
  });
}

export async function main(argv) {
  const command = argv.find((arg) => !arg.startsWith("--"));
  const unknown = argv.filter(
    (arg) => arg.startsWith("--") && arg !== "--demo",
  );
  if (!["dev", "start"].includes(command) || unknown.length > 0) {
    fail("Usage: npm run dev [-- --demo]  |  npm start [-- --demo]");
  }
  const python = checkSetup();
  const env = argv.includes("--demo")
    ? { ...process.env, MAKAN_DEMO: "1" }
    : process.env;

  if (command === "dev") {
    console.log(
      "Makan dev: the page is at http://localhost:5173, press Ctrl+C to stop.",
    );
    supervise([
      backend(python, { reload: true, env }),
      npm(["--prefix", "web", "run", "dev"]),
    ]);
    return;
  }
  const built = await build();
  if (built !== 0)
    fail("The front end build failed, so Makan was not started.");
  console.log(`Makan is at http://localhost:${PORT}, press Ctrl+C to stop.`);
  supervise([backend(python, { reload: false, env })]);
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  await main(process.argv.slice(2));
}
