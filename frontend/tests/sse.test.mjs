/** SSE 流在代理不断开连接时也须按 done 结束，并保留错误边界。 */

import assert from "node:assert/strict";
import test from "node:test";

import { pipeSSE } from "../src/api/sse.ts";

const encoder = new TextEncoder();

test("CRLF 帧中的 done 立即结束并释放未关闭的流", async () => {
  let canceled = false;
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode('event: queued\r\ndata: {"position":1}\r\n\r\n'));
      controller.enqueue(encoder.encode('event: done\r\ndata: {"text":"完成"}\r\n\r\n'));
    },
    cancel() { canceled = true; },
  });
  const events = [];
  await pipeSSE(new Response(stream), (event) => events.push(event));
  assert.deepEqual(events.map((event) => event.type), ["queued", "done"]);
  assert.equal(canceled, true);
});

test("没有 done 的关闭流报告错误，不再静默当作成功", async () => {
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode('event: queued\ndata: {"position":1}\n\n'));
      controller.close();
    },
  });
  await assert.rejects(
    pipeSSE(new Response(stream), () => undefined),
    /未收到完成事件/,
  );
});
