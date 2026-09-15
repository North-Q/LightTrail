/**
 * SPA 壳层自检（零依赖，走本机 Chrome/Edge 的 headless --dump-dom）。
 *
 * 六页路由逐个打开，断言「页面标题渲染出来了」+ 根节点非空（防白屏 / 路由断裂 /
 * JS 运行时错误这类回归）；可选 `--screenshots <dir>` 顺带出双视口截图（390×844 / 1440×900），
 * 对应设计规范里的「双视口自检」遗留项。
 *
 * 用法（先起前端：npm run dev 或 npm run preview）：
 *   node scripts/shell-check.mjs                        # 默认 http://127.0.0.1:5173
 *   node scripts/shell-check.mjs --base http://127.0.0.1:4173 --screenshots ../.checks
 *
 * 说明：壳层自检不需要后端（各页面对接口失败都有空态兜底）；要验真实数据链路请用
 * docs 里的真实联调脚本（scripts/live_check.py + 无头浏览器流程自检）。
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";

const ROUTES = [
  { hash: "#/home", title: "今日拍摄旅程", label: "总览" },
  { hash: "#/d1", title: "灵感", label: "D1" },
  { hash: "#/d2", title: "规划", label: "D2" },
  { hash: "#/d3", title: "决策", label: "D3" },
  { hash: "#/d4", title: "复盘", label: "D4" },
  { hash: "#/m1", title: "M1 · 我的记忆", label: "M1" },
];

function arg(name, fallback) {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

/** 找本机 Chrome/Edge（可用 CHROME_PATH 覆盖）。 */
function resolveBrowser() {
  const candidates = [
    process.env.CHROME_PATH,
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (!candidate.includes("/") || existsSync(candidate)) {
      return candidate;
    }
  }
  throw new Error("未找到 Chrome/Edge（可用 CHROME_PATH 指定）");
}

function runBrowser(binary, args) {
  const result = spawnSync(binary, args, { encoding: "utf-8", maxBuffer: 64 * 1024 * 1024 });
  if (result.error) {
    throw result.error;
  }
  return result;
}

const base = arg("base", "http://127.0.0.1:5173").replace(/\/$/, "");
const screenshotDir = arg("screenshots", "");
const binary = resolveBrowser();
const failures = [];

if (screenshotDir) {
  mkdirSync(screenshotDir, { recursive: true });
}

for (const route of ROUTES) {
  const url = `${base}/${route.hash}`;
  const dumped = runBrowser(binary, [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--virtual-time-budget=5000",
    "--dump-dom",
    url,
  ]);
  const dom = dumped.stdout ?? "";
  const hasTitle = dom.includes(route.title);
  const hasRoot = /<div id="root">\s*<\/div>/.test(dom) === false && dom.includes('id="root"');
  const problems = [];
  if (!hasTitle) problems.push(`未渲染标题「${route.title}」`);
  if (!hasRoot) problems.push("根节点为空（疑似白屏）");
  if (problems.length > 0) {
    failures.push({ route, problems });
    console.log(`[FAIL] ${route.label} ${route.hash} —— ${problems.join("；")}`);
  } else {
    console.log(`[OK] ${route.label} ${route.hash} —— 标题「${route.title}」已渲染（DOM ${dom.length} 字节）`);
  }
  if (screenshotDir) {
    for (const viewport of [
      { name: "desktop", size: "1440,900" },
      { name: "mobile", size: "390,844" },
    ]) {
      const file = path.join(screenshotDir, `${route.label.toLowerCase()}-${viewport.name}.png`);
      runBrowser(binary, [
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        `--window-size=${viewport.size}`,
        "--virtual-time-budget=5000",
        `--screenshot=${file}`,
        url,
      ]);
    }
  }
}

// 未知路由：应落到总览（不白屏）
const fallback = runBrowser(binary, [
  "--headless=new",
  "--disable-gpu",
  "--no-sandbox",
  "--virtual-time-budget=5000",
  "--dump-dom",
  `${base}/#/no-such-page`,
]);
if ((fallback.stdout ?? "").includes("今日拍摄旅程")) {
  console.log("[OK] 未知路由回落总览");
} else {
  failures.push({ route: { hash: "#/no-such-page", label: "fallback" }, problems: ["未知路由未回落总览"] });
  console.log("[FAIL] 未知路由未回落总览");
}

console.log("---");
if (failures.length > 0) {
  console.log(`壳层自检失败 ${failures.length} 项（base=${base}）`);
  process.exit(1);
}
console.log(`壳层自检全部通过（${ROUTES.length + 1} 项，base=${base}）`);