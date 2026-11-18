const path = require("node:path");
const { spawn } = require("node:child_process");

const root = path.resolve(__dirname, "../..");
const frontend = path.join(root, "frontend");
const nextCli = path.join(frontend, "node_modules", "next", "dist", "bin", "next");
const controller = spawn("python", [path.join(root, "portal", "server.py")], {
  cwd: root, stdio: "inherit", windowsHide: true,
});
const next = spawn(process.execPath, [nextCli, "dev", "--hostname", "127.0.0.1", "--port", "3000"], {
  cwd: frontend, stdio: "inherit", windowsHide: true,
});

let stopping = false;
function terminateTree(child) {
  if (!child || child.exitCode !== null) return Promise.resolve();
  if (process.platform === "win32" && child.pid) {
    return new Promise((resolve) => {
      const killer = spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
        stdio: "ignore", windowsHide: true,
      });
      killer.once("close", resolve);
      killer.once("error", () => { child.kill(); resolve(); });
    });
  } else {
    child.kill("SIGTERM");
    return Promise.resolve();
  }
}

function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  const finish = async () => {
    await Promise.all([terminateTree(controller), terminateTree(next)]);
    process.exit(code);
  };
  const fallback = setTimeout(() => { finish(); }, 1500);
  fetch("http://127.0.0.1:8081/api/stop-forwards", { method: "POST" })
    .then((response) => response.body?.cancel())
    .catch(() => {})
    .finally(() => { clearTimeout(fallback); finish(); });
}
controller.on("error", (error) => { console.error(`Could not start Python controller: ${error.message}`); stop(1); });
next.on("error", (error) => { console.error(`Could not start Next.js: ${error.message}`); stop(1); });
next.on("exit", (code) => stop(code ?? 0));
controller.on("exit", (code) => { if (!stopping && code !== 0) stop(code ?? 1); });
process.on("SIGINT", () => stop(0));
process.on("SIGTERM", () => stop(0));

console.log("Lab controller: http://127.0.0.1:8081");
console.log("Next.js UI:     http://127.0.0.1:3000");
