/** S1-5 URL 状态只含非敏感、可验证的规划选择。 */

import assert from "node:assert/strict";
import test from "node:test";
import { buildPlanHash, parsePlanUrl } from "../src/pages/planUrl.ts";

test("规划链接恢复题材、日期和机位 ID，不含坐标", () => {
  const hash = buildPlanHash({ subject: "日落", date: "2026-10-03", spotId: "spot-east" });
  assert.deepEqual(parsePlanUrl(hash), {
    state: { subject: "日落", date: "2026-10-03", spotId: "spot-east" }, error: "",
  });
  assert.equal(hash.includes("latitude"), false);
  assert.equal(hash.includes("longitude"), false);
  assert.equal(hash.includes("121.95"), false);
});

test("非法日期、题材和机位 ID 不进入恢复状态", () => {
  for (const hash of [
    "#/d2?subject=未知&date=2026-10-03&spot=spot-east",
    "#/d2?subject=日落&date=2026-02-30&spot=spot-east",
    "#/d2?subject=日落&date=2026-10-03&spot=bad%20id",
  ]) {
    const parsed = parsePlanUrl(hash);
    assert.notEqual(parsed.error, "");
    assert.equal(parsed.state.spotId, "");
  }
});
