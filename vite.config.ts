/** @format */

import react from "@vitejs/plugin-react";
import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, loadEnv } from "vite";

const __dirname = fileURLToPath(new URL(".", import.meta.url));

function getVersion(): string {
  try {
    // 优先用 git describe 获取最近的 tag（如 V0.4 或 V0.4-3-gabc123）
    const gitDesc = execSync("git describe --tags --always --dirty", {
      cwd: __dirname,
      encoding: "utf-8",
    }).trim();
    return gitDesc || "unknown";
  } catch {
    // 没有 git 时回退到 package.json 的 version
    try {
      const pkg = JSON.parse(
        readFileSync(new URL("./package.json", import.meta.url), "utf-8"),
      ) as {
        version?: string;
      };
      return pkg.version ?? "unknown";
    } catch {
      return "unknown";
    }
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, "");
  const riskApiTarget = env.RISK_API_PROXY_TARGET || "http://127.0.0.1:5004";

  return {
    base: "/2_options_calc/",
    plugins: [react()],
    define: {
      __APP_VERSION__: JSON.stringify(getVersion()),
    },
    server: {
      port: 5173,
      proxy: {
        "/2_options_calc/risk/api": {
          target: riskApiTarget,
          rewrite: (path) => path.replace(/^\/2_options_calc\/risk\/api/, "/api/options/liquidation-risk"),
        },
        "/2_options_calc/api": {
          target: "http://127.0.0.1:5004",
          rewrite: (path) => path.replace(/^\/2_options_calc\/api/, "/api"),
        },
        "/api": "http://127.0.0.1:5004",
      },
    },
  };
});
