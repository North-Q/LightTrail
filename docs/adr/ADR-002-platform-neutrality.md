# ADR-002：平台中立性

> 历史记录日期：2026-09-07；2026-10-06核对。原则已体现在代码。原稿“Accepted”的具体用户确认来源未在本次找到，保留 [原件](../archive/audit-20261006/docs/adr/ADR-002-platform-neutrality.md)，不补造批准。ADR-001当前缺失，原Supersedes引用仅作为历史缺口记录。

## 动机与现行约束

平台建议不应变成领域架构前提。业务通过能力声明选择模型，供应商请求差异留在适配层；配置使用LLM_命名，ECNU_兼容别名按实际Settings处理。ECNU是默认示例，不等于所有模型支持同样能力。

ModelRouter能力矩阵可注入；业务不得以模型品牌判断代替RouteIntent。配置层保留默认型号合法，器材品牌不属于此限制。未知模型还需配置能力与价格，不能承诺任意OpenAI兼容服务只改一个环境变量便完整可用。

## 历史变化

原稿的LLM_SERIAL_LLM和默认串行已被 [ADR-004](ADR-004-concurrency-as-config.md)替代。现行为LLM_CONCURRENCY默认4，设1串行；学校平台实际验证建议设1，不改变配置架构。模型能力随服务变化，本次没有外部核验原模型规格表。

## 现有证据与边界

见 [ModelRouter](../../src/lighttrail/llm/router.py)、[适配客户端](../../src/lighttrail/adapters/llm/client.py)、[Settings](../../src/lighttrail/config.py)与 [当前架构](../architecture.md)。能力矩阵注入测试不等于生产已提供完整用户配置界面；取消/预算缺陷见审查A04。新增主要平台机制先评估，不从历史文档推导自动采用。
