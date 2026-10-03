/** POST SSE 的帧解析与终止语义；收到 done 即结束，异常时释放响应流。 */

import type { SSEEvent } from "./events";

/** 从一帧 SSE 文本解析事件。 */
export function parseFrame(frame: string): SSEEvent | null {
  let type = "";
  const dataLines: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      type = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }
  if (!type || dataLines.length === 0) {
    return null;
  }
  try {
    return { type, ...JSON.parse(dataLines.join("\n")) } as SSEEvent;
  } catch {
    return null;
  }
}

/** 读取 SSE 流；done 是协议终点，不依赖代理是否立即关闭连接。 */
export async function pipeSSE(resp: Response, onEvent: (event: SSEEvent) => void): Promise<void> {
  if (!resp.body) {
    throw new Error("响应无流式内容");
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      for (;;) {
        const boundary = /\r?\n\r?\n/.exec(buffer);
        if (!boundary) {
          break;
        }
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const event = parseFrame(frame);
        if (event) {
          onEvent(event);
          if (event.type === "done") {
            return;
          }
        }
      }
      if (done) {
        throw new Error("事件流提前结束，未收到完成事件");
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
