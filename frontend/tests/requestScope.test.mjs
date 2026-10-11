/** 页面等待生命周期：旧事件、旧 finally 和卸载不得回写。 */
import assert from "node:assert/strict";
import test from "node:test";
import { RequestScope, STOP_WAITING_NOTICE } from "../src/api/requestScope.ts";

test("停止立即撤销当前请求的回写资格", () => {
  const scope = new RequestScope();
  const first = scope.begin();
  scope.stop();
  assert.equal(first.signal.aborted, true);
  assert.equal(scope.isCurrent(first), false);
  assert.match(STOP_WAITING_NOTICE, /不能确认.*已取消/);
});

test("旧 finally 不会清除后发请求状态", () => {
  const scope = new RequestScope();
  const first = scope.begin();
  const second = scope.begin();
  let changed = false;
  scope.finish(first, () => { changed = true; });
  assert.equal(changed, false);
  assert.equal(scope.isCurrent(second), true);
  scope.finish(second, () => { changed = true; });
  assert.equal(changed, true);
  assert.equal(scope.isCurrent(second), false);
});

test("卸载和重新挂载不能恢复旧请求资格", () => {
  const scope = new RequestScope();
  const first = scope.begin();
  scope.dispose();
  scope.activate();
  const second = scope.begin();
  assert.equal(first.signal.aborted, true);
  assert.equal(scope.isCurrent(first), false);
  assert.equal(scope.isCurrent(second), true);
});
