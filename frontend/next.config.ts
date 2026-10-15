import type { NextConfig } from "next";

/**
 * The browser never talks to the backend directly: in development `server.mjs` (port 3000)
 * proxies `/api` and `/mock-platform` to the FastAPI service and everything else to Next
 * (port 3001). In production the same proxy (or nginx) does the job, so cookies stay
 * first-party and WebSockets work without CORS gymnastics.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  poweredByHeader: false,
  // Accept the dev-server preview hosts (sandbox / tunnels) for HMR and server actions.
  allowedDevOrigins: ["*.e2b.app", "localhost", "127.0.0.1"],
  images: { unoptimized: true },
};

export default nextConfig;
