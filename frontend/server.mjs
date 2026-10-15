/**
 * Single-origin front door for BowserAuto.
 *
 *   browser ──► :3000 (this file) ──► /api/*, /mock-platform/*  ──► FastAPI (BACKEND_URL)
 *                                 └─► everything else            ──► Next.js (NEXT_PORT)
 *
 * WebSocket upgrades for /api/ws are proxied to the backend; Next's HMR socket goes to Next.
 * Keeping one origin means the session cookie is first-party, CSRF/Origin checks are simple
 * and no CORS configuration is needed. In production you can use nginx/Caddy with the same
 * routing instead (see deploy/nginx.conf).
 */
import http from "node:http";
import { spawn } from "node:child_process";
import httpProxy from "http-proxy";

const PORT = Number(process.env.PORT || 3000);
const NEXT_PORT = Number(process.env.NEXT_PORT || 3001);
const BACKEND = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const NEXT = `http://127.0.0.1:${NEXT_PORT}`;
const isProd = process.env.NODE_ENV === "production";

const proxy = httpProxy.createProxyServer({ ws: true, xfwd: true, changeOrigin: false, proxyTimeout: 0 });
proxy.on("error", (err, _req, res) => {
  if (res && "writeHead" in res && !res.headersSent) {
    res.writeHead(502, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: { code: "bad_gateway", message: `Upstream unavailable: ${err.message}` } }));
  } else if (res && "end" in res) {
    try { res.end(); } catch { /* socket already gone */ }
  }
});

const isBackendPath = (url = "") => url.startsWith("/api/") || url === "/api" || url.startsWith("/mock-platform");

// Forward the public host so the backend's Origin checks and signed URLs see the real origin.
function forwardHeaders(req) {
  const host = req.headers["x-forwarded-host"] || req.headers.host;
  const proto = req.headers["x-forwarded-proto"] || (req.socket.encrypted ? "https" : "http");
  req.headers["x-forwarded-host"] = host;
  req.headers["x-forwarded-proto"] = proto;
}

const server = http.createServer((req, res) => {
  forwardHeaders(req);
  proxy.web(req, res, { target: isBackendPath(req.url) ? BACKEND : NEXT });
});
server.on("upgrade", (req, socket, head) => {
  forwardHeaders(req);
  proxy.ws(req, socket, head, { target: isBackendPath(req.url) ? BACKEND : NEXT });
});

// Start Next.js on the internal port unless an external instance is already managed.
if (process.env.NEXT_EXTERNAL !== "1") {
  const args = isProd ? ["start", "-p", String(NEXT_PORT), "-H", "127.0.0.1"] : ["dev", "-p", String(NEXT_PORT), "-H", "127.0.0.1"];
  const next = spawn("npx", ["next", ...args], { stdio: "inherit", env: process.env });
  const stop = () => { next.kill("SIGTERM"); process.exit(0); };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
  next.on("exit", (code) => { console.error(`[proxy] next exited with ${code}`); process.exit(code ?? 1); });
}

server.listen(PORT, "0.0.0.0", () => {
  console.log(`[proxy] http://0.0.0.0:${PORT} → api ${BACKEND}, app ${NEXT}`);
});
