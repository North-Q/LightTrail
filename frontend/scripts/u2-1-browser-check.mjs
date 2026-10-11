/** U2-1 延迟离线浏览器检查，复用本地 Chrome/Edge。 */
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
const browser = [process.env.CHROME_PATH, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "/usr/bin/chromium"].filter(Boolean).find(existsSync);
if (!browser) throw new Error("未找到本机 Chrome/Edge");
const base = process.env.LIGHTTRAIL_UI_BASE ?? "http://127.0.0.1:5173";
const response = spawnSync(browser, ["--headless=new", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=15000", "--dump-dom", `${base}/tests/u2-1.html`], { encoding: "utf-8", maxBuffer: 8 * 1024 * 1024, timeout: 30000 });
if (response.error) throw response.error;
const result = response.stdout.match(/<p id="u2-result">([^<]*)<\/p>/)?.[1] ?? "无浏览器结果";
console.log(result);
if (response.status !== 0 || !result.startsWith("PASS:")) process.exit(1);
