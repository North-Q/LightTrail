#!/usr/bin/env node
/**
 * 令牌对齐核对脚本（E7-6 验收：diff=0）
 *
 * 对比「真源原型 html 的 :root 令牌」与「frontend/src/styles.css 的 :root 令牌」：
 * - 逐一比对真源令牌名是否在 styles.css 中存在且取值一致（规范化后忽略空白差异）；
 * - 输出缺失/不一致清单；全部一致时打印 count 并退出 0。
 *
 * 运行：
 *   node scripts/token-diff.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const prototypePath = path.resolve(here, "../../docs/design/delivery/lighttrail-prototype.html");
const stylesPath = path.resolve(here, "../src/styles.css");
const printSection = process.argv.includes("--print");

function extractRootBlock(text) {
  const matchRoot = /:root\s*\{/.exec(text);
  if (!matchRoot) {
    throw new Error("未找到 :root 块");
  }
  const start = matchRoot.index + matchRoot[0].length;
  const end = text.indexOf("}", start);
  if (end < 0) {
    throw new Error(":root 块未闭合");
  }
  return text.slice(start, end);
}

function parseTokens(block) {
  const tokens = {};
  for (const line of block.split("\n")) {
    const match = line.match(/^\s*(--[a-zA-Z0-9-]+)\s*:\s*(.*?)\s*;?\s*$/);
    if (match) {
      tokens[match[1]] = match[2].trim();
    }
  }
  return tokens;
}

function normalize(value) {
  return value.replace(/\s+/g, "");
}

const prototype = parseTokens(extractRootBlock(readFileSync(prototypePath, "utf8")));
const styles = parseTokens(extractRootBlock(readFileSync(stylesPath, "utf8")));

const names = Object.keys(prototype);
const missing = [];
const mismatched = [];
for (const name of names) {
  if (!(name in styles)) {
    missing.push(name);
    continue;
  }
  if (normalize(styles[name]) !== normalize(prototype[name])) {
    mismatched.push(`${name}: 真源 ${prototype[name]} ≠ 前端 ${styles[name]}`);
  }
}

if (printSection) {
  for (const name of names) {
    console.log(`${name}: ${prototype[name]}`);
  }
}

if (missing.length > 0) {
  console.error(`❌ 真源令牌缺失（前端正缺少 ${missing.length} 个）：`);
  for (const name of missing) {
    console.error(`   - ${name}`);
  }
}
if (mismatched.length > 0) {
  console.error(`❌ 令牌取值不一致（${mismatched.length} 处）：`);
  for (const row of mismatched) {
    console.error(`   - ${row}`);
  }
}
if (missing.length === 0 && mismatched.length === 0) {
  console.log(`✅ 令牌对齐：真源 ${names.length} 项令牌与前端 :root 逐项 diff=0`);
  process.exit(0);
}
process.exit(1);
