/** R0-6 前端确定性规则回归：曝光守恒、当前规划选择和事件写入门禁。 */

import assert from "node:assert/strict";
import test from "node:test";

import { changeExposure, exposureValue } from "../src/pages/exposure.ts";
import { buildPlanRequest, usableSpotName } from "../src/pages/planRequest.ts";
import { canWriteReviewEvent } from "../src/pages/reviewEvent.ts";

test("调整任一曝光滑块时 EV100 保持不变，ISO 增档允许快门减半", () => {
  const base = { aperture: 4, shutter: 1 / 250, iso: 100 };
  const isoUp = changeExposure(base, "iso", 200);
  assert.equal(isoUp.shutter, 1 / 500);
  assert.ok(Math.abs(exposureValue(base) - exposureValue(isoUp)) < 1e-10);

  for (const control of ["aperture", "shutter", "iso"]) {
    for (const requested of [0, 1, 2.8, 30, 400, 25600]) {
      const changed = changeExposure(base, control, requested);
      assert.ok(changed.aperture >= 1 && changed.aperture <= 22);
      assert.ok(changed.shutter >= 1 / 8000 && changed.shutter <= 30);
      assert.ok(changed.iso >= 50 && changed.iso <= 25600);
      assert.ok(Math.abs(exposureValue(base) - exposureValue(changed)) < 1e-9);
    }
  }

  const chained = changeExposure(changeExposure(base, "shutter", 30), "aperture", 1);
  assert.ok(Math.abs(exposureValue(base) - exposureValue(chained)) < 1e-9);
});

test("规划请求只包含本轮明确选择的日期、机位和题材", () => {
  assert.equal(
    buildPlanRequest({ date: "2026-10-03", spot: "西岸", subject: "火烧云" }),
    "2026-10-03去西岸拍火烧云，请制定拍摄计划，列出该机位的天象与天气窗口。",
  );
  assert.equal(buildPlanRequest({ date: "", spot: "西岸", subject: "火烧云" }), null);
  assert.equal(buildPlanRequest({ date: "2026-02-30", spot: "西岸", subject: "火烧云" }), null);
  assert.equal(buildPlanRequest({ date: "2026-10-03", spot: "", subject: "火烧云" }), null);
  assert.equal(usableSpotName({ 名称: "旧机位", 纬度: "31.1", 经度: "121.4" }), null);
  assert.equal(usableSpotName({ name: "已核实机位", latitude: 31.1, longitude: 121.4, coordinate_system: "WGS84" }), "已核实机位");
  assert.equal(usableSpotName({ name: "偏移坐标", latitude: 31.1, longitude: 121.4, coordinate_system: "GCJ-02" }), null);
  assert.equal(usableSpotName({ name: "坏坐标", latitude: 120, longitude: 121.4 }), null);
  assert.equal(usableSpotName({ name: "无坐标" }), null);
});

test("复盘缺少实际结果时不能写事件，成功后不能重复写入", () => {
  assert.equal(canWriteReviewEvent(true, "西岸", "", false, false), false);
  assert.equal(canWriteReviewEvent(false, "西岸", "success", false, false), false);
  assert.equal(canWriteReviewEvent(true, "", "fail", false, false), false);
  assert.equal(canWriteReviewEvent(true, "西岸", "fail", true, false), false);
  assert.equal(canWriteReviewEvent(true, "西岸", "fail", false, true), false);
  assert.equal(canWriteReviewEvent(true, "西岸", "fail", false, false), true);
});
