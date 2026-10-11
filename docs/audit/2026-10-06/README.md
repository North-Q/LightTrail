# 2026-10-06 审查证据

[报告](../../AUDIT-2026-10-06.md)说明结论与限制；[verification.json](verification.json)保存验证汇总与原始反例结果；[module-inventory.json](module-inventory.json)记录全部模块范围。不是完整分支覆盖率报告。

两份*.py.txt为当次临时重现脚本原件，不是产品实现或新测试套件。需重现时在项目根建立 .workbuddy/audit-20261006，将它们复制为 reproduce.py / cancel_repro.py，使用项目venv的Python运行。reproduce.py根据该目录计算源码根并将合成数据写在同目录下，每次生成独立用户/会话；调用前替换模型Key为假值并清空地图Key，数据源及模型均为本地替身。脚本针对4b04b81私有实现，后续修复可能需要更新预期。

两个浏览器*.txt保存临时探查源码，ui-result.txt记录harness限制：M1创建成功，D3虚拟时间超时。此项不列为产品故障或通过，正式合成浏览器检查结果另见verification。临时前端文件已移出产品测试目录。

未发送真实机位、照片或Key；未构建wheel、安装系统包或调用供应商。文档/归档收口检查见 [document-checks.json](document-checks.json)。
