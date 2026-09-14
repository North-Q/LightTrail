/**
 * 导出后端 OpenAPI JSON（契约单一真源流水线第一步，B4-1）。
 *
 * 流水线：FastAPI（复用 contracts 模型）→ OpenAPI JSON（本脚本）
 *        → openapi-typescript → src/api/generated.ts
 * 由 `npm run gen:api` 调用；中间产物 `.openapi.json` 不入库（只看 generated.ts 的 diff）。
 */
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(here, "..");
const repoRoot = path.resolve(frontendDir, "..");
const output = path.join(frontendDir, ".openapi.json");

/** 解析后端解释器：显式环境变量 > 项目 venv > 系统 python。 */
function resolvePython() {
  const candidates = [
    process.env.LIGHTTRAIL_PYTHON,
    path.join(repoRoot, ".venv", "Scripts", "python.exe"),
    path.join(repoRoot, ".venv", "bin", "python"),
    "python3",
    "python",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (candidate === "python" || candidate === "python3") {
      return candidate;
    }
    if (existsSync(candidate)) {
      return candidate;
    }
  }
  throw new Error("未找到可用的 Python 解释器（可用 LIGHTTRAIL_PYTHON 指定）");
}

const python = resolvePython();
const result = spawnSync(python, ["-m", "lighttrail.api.openapi_export", output], {
  cwd: repoRoot,
  stdio: "inherit",
  env: { ...process.env, PYTHONPATH: path.join(repoRoot, "src") },
});
if (result.error) {
  throw result.error;
}
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}
console.log(`OpenAPI 已导出：${path.relative(repoRoot, output)}`);