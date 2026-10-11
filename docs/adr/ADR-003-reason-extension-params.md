# ADR-003：深推理扩展参数适配

> 历史记录日期：2026-09-07；2026-10-06核对。代码已采用配置开关与SDK签名探测；历史Accepted的具体用户确认来源本次未找到，[原件](../archive/audit-20261006/docs/adr/ADR-003-reason-extension-params.md)保留。

## 动机与现行约束

reason通道可能携带thinking/reasoning_effort，SDK或供应商未必接受相同命名参数。业务声明意图，扩展字段适配收敛在客户端：探测原生签名，必要时使用extra_body，未启用则不发送相应思考扩展。

LLM_REASON_THINKING开关保留，默认面向当前配置。切换供应商需验证模型能力、扩展字段、网关和计价，不能保证“关闭开关即任意兼容API开箱可用”。原SDK3.1.0例子是历史记录，不是本次确认的当前通用规格。

## 证据与边界

当前实现：[adapters/llm/client.py](../../src/lighttrail/adapters/llm/client.py)、[runtime/agent.py](../../src/lighttrail/runtime/agent.py)、[config.py](../../src/lighttrail/config.py)；相关验证见 [test_reason.py](../../tests/test_reason.py)。旧llm/client.py、agent/core.py与test_client_serial.py路径已失效，不继续引用。

平台中立原则见 [ADR-002](ADR-002-platform-neutrality.md)，并发见 [ADR-004](ADR-004-concurrency-as-config.md)。本次离线测试不证明供应商当前扩展参数行为；真实联调需单独授权。
