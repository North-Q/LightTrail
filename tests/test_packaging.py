"""依赖声明的 pytest 门禁（离线，不装包、不触网）。

背景（B4 期真 bug）：`httpx` / `tenacity` / `pydantic` 在源码里是**运行期直接 import**
（`infra/http.py` 数据源、`adapters/llm/client.py` LLM 重试、契约层与 API 层），但只在
`[project.optional-dependencies].dev` 里声明——干净的 `pip install .` 会在核心链路
ImportError。人工核对容易漏，这里固化成静态门禁：

- 扫描 `src/lighttrail/**/*.py` 的顶层 import，剔除标准库与本包；
- 其余模块名（做发行名别名映射）必须出现在 `[project].dependencies` 里。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "lighttrail"

# import 名 → 发行名（PyPI 名）差异映射（小写比较）
_DIST_ALIAS = {
    "pil": "pillow",
    "pydantic_ai": "pydantic-ai-slim",
    "pydantic_settings": "pydantic-settings",
}


def _declared_distributions() -> set[str]:
    """读取 pyproject 的 [project].dependencies，归一为发行名集合（小写）。"""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared: set[str] = set()
    for spec in project["project"]["dependencies"]:
        name = spec.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip().lower()
        declared.add(name)
    return declared


def _imported_third_party() -> dict[str, str]:
    """扫描源码顶层 import，返回 {模块名: 首个出现文件}（剔除标准库与 lighttrail）。"""
    stdlib = set(sys.stdlib_module_names)
    found: dict[str, str] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name.split(".")[0] for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if not name or name in stdlib or name == "lighttrail":
                    continue
                found.setdefault(name, str(path.relative_to(ROOT)))
    return found


def test_third_party_imports_are_declared() -> None:
    """源码里直接 import 的第三方包必须写进 [project].dependencies。"""
    declared = _declared_distributions()
    missing = []
    for module, source in sorted(_imported_third_party().items()):
        dist = _DIST_ALIAS.get(module.lower(), module).lower()  # import 名可能带大写（如 PIL）
        if dist not in declared and dist.replace("_", "-") not in declared:
            missing.append(f"{module}（发行名 {dist}，首次出现 {source}）")
    assert missing == [], "以下依赖被源码 import 但未在 pyproject 声明：" + "；".join(missing)


def test_dev_extra_does_not_shadow_runtime_deps() -> None:
    """运行期依赖不应只挂在 dev extra 里（dev 是给测试工具用的）。"""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = _declared_distributions()
    dev = {
        spec.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip().lower()
        for spec in project["project"]["optional-dependencies"].get("dev", [])
    }
    overlap = sorted(name for name in dev & runtime)
    assert overlap == [], f"同一依赖同时出现在 dependencies 与 dev extra：{overlap}"
