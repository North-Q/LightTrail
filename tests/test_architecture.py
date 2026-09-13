"""架构边界的 pytest 兜底（B1-5 / v4 §2.2 第二道闸）。

职责（与 import-linter 分工，见 v4 §2.2）：
- import-linter 抓模块级分层与禁止依赖，抓不到**函数内 import**；本模块用 AST 遍历源码
  兜底，并承载 import-linter 表达不了的自定义规则（品牌字面量红线，ADR-002）；
- 本批强制三条：① 契约层零依赖（仅标准库 + pydantic）；② 新层（contracts/adapters）
  代码字符串常量不含供应商品牌；③ 领域/运行时/契约不得依赖适配层与交互层（禁止边，
  含函数内 import——import-linter 抓不到的部分）；
- 目标分层（interface/application/runtime/domain）就位后，由 B3-5 把范围扩展到旧包
  （agent/orchestrator/tools/memory/infra/llm/api），届时同步开启 import-linter 三段契约。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "lighttrail"
CONTRACTS_DIR = SRC / "contracts"
ADAPTERS_DIR = SRC / "adapters"

# 品牌字面量（ADR-002 红线）：只允许出现在 adapters/llm 与 settings 默认值处
_BRAND_LITERALS = ("ecnu", "openai.com", "deepseek")


def _python_files(directory: Path) -> list[Path]:
    """列出目录下全部 .py 文件（递归，稳定排序）。"""
    return sorted(directory.rglob("*.py"))


def _import_names(path: Path) -> set[str]:
    """收集模块的完整 import 名（含函数内 import / from-import）。"""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def _import_roots(path: Path) -> set[str]:
    """收集模块的所有 import 根名（含函数内 import / from-import）。"""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _docstring_ids(tree: ast.AST) -> set[int]:
    """收集文档字符串常量节点的 id（品牌字面量规则只针对代码常量，不针对 docstring）。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        is_docstring = (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        )
        if is_docstring:
            ids.add(id(first.value))
    return ids


# 禁止边：领域 / 运行时 / 契约不得依赖适配层与交互层（v4 §2.2，import-linter 的 AST 兜底）
_FORBIDDEN_SOURCES = ("contracts", "runtime", "tools", "memory")
_FORBIDDEN_TARGETS = ("lighttrail.adapters", "lighttrail.api", "lighttrail.cli", "lighttrail.orchestrator")


def test_forbidden_layer_edges_absent() -> None:
    """禁止边（AST，含函数内 import）：领域/运行时/契约不得依赖适配层与交互层。"""
    offenders: list[str] = []
    for source in _FORBIDDEN_SOURCES:
        for path in _python_files(SRC / source):
            for name in _import_names(path):
                if any(name == target or name.startswith(f'{target}.') for target in _FORBIDDEN_TARGETS):
                    offenders.append(f'{path.name}: {name}')
    assert offenders == []


def test_contracts_have_no_layer_dependencies() -> None:
    """契约层零依赖：contracts/ 不得 import 其他 lighttrail 层（含函数内 import）。"""
    offenders: list[str] = []
    for path in _python_files(CONTRACTS_DIR):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.startswith("lighttrail.") and not name.startswith("lighttrail.contracts"):
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []


def test_contracts_third_party_whitelist() -> None:
    """契约层第三方依赖白名单：只允许 pydantic（其余必须是标准库或自身）。"""
    allowed = {"pydantic", "lighttrail", "__future__"}
    offenders: list[str] = []
    for path in _python_files(CONTRACTS_DIR):
        for root in _import_roots(path):
            if root in sys.stdlib_module_names or root in allowed:
                continue
            offenders.append(f"{path.name}: {root}")
    assert offenders == []


def test_new_layers_have_no_brand_literals() -> None:
    """品牌字面量红线：contracts/ 与 adapters/ 的代码字符串常量不得含供应商品牌。"""
    offenders: list[str] = []
    for path in _python_files(CONTRACTS_DIR) + _python_files(ADAPTERS_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstring_ids(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            lowered = node.value.lower()
            for brand in _BRAND_LITERALS:
                if brand in lowered:
                    offenders.append(f"{path.name}:{node.lineno}: {brand}")
    assert offenders == []