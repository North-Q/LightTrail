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

test("响应读取挂起时 abort 主动释放 reader 并停止等待", async () => {
  let canceled = false;
  const controller = new AbortController();
  const stream = new ReadableStream({ cancel() { canceled = true; } });
  const waiting = pipeSSE(new Response(stream), () => assert.fail("停止后不得回调"), controller.signal);
  controller.abort();
  await assert.rejects(waiting, { name: "AbortError" });
  assert.equal(canceled, true);
  assert.equal(stream.locked, false);
});

test("已经停止的迟到响应不派发任何帧", async () => {
  const controller = new AbortController();
  controller.abort();
  const stream = new ReadableStream({ start(reader) {
    reader.enqueue(encoder.encode('event: done\ndata: {"text":"旧结果"}\n\n'));
  } });
  await assert.rejects(pipeSSE(new Response(stream), () => assert.fail("旧结果回写"), controller.signal), { name: "AbortError" });
  assert.equal(stream.locked, false);
});

test("同一读取块内停止后剩余帧不能继续回写", async () => {
  const controller = new AbortController();
  const stream = new ReadableStream({ start(reader) {
    reader.enqueue(encoder.encode('event: token\ndata: {"content":"部分"}\n\nevent: done\ndata: {"text":"完成"}\n\n'));
  } });
  const events = [];
  await assert.rejects(pipeSSE(new Response(stream), (event) => {
    events.push(event.type);
    controller.abort();
  }, controller.signal), { name: "AbortError" });
  assert.deepEqual(events, ["token"]);
});
