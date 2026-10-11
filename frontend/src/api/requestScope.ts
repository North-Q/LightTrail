/** 一个页面操作的等待生命周期；停止或替换后，旧响应没有回写资格。 */
export const STOP_WAITING_NOTICE = "已停止等待，浏览器不再接收本次结果；不能确认服务端或供应商请求已取消。";

export class RequestScope {
  private controller: AbortController | null = null;
  private mounted = true;

  activate(): void { this.mounted = true; }

  begin(): AbortController {
    this.stop();
    const controller = new AbortController();
    this.controller = controller;
    return controller;
  }

  isCurrent(controller: AbortController): boolean {
    return this.mounted && this.controller === controller && !controller.signal.aborted;
  }

  finish(controller: AbortController, update: () => void): void {
    if (!this.isCurrent(controller)) return;
    this.controller = null;
    update();
  }

  stop(): void {
    const controller = this.controller;
    this.controller = null;
    controller?.abort();
  }

  dispose(): void {
    this.mounted = false;
    this.stop();
  }
}
