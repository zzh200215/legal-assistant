"""前后端联调检测：前端 api/*.js 及 src 下视图/组件里的每个 HTTP 调用 vs 后端真实路由。

用法: python scripts/check_frontend_backend_contract.py
输出: 不匹配列表（前端调用但后端无此路由 = 联调 404 风险）。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "frontend" / "src"


def extract_frontend_calls() -> list[tuple[str, str, str]]:
    """返回 [(文件, METHOD, 规范化路径)]，路径参数 `${x}` → `{}`，去掉 query。

    覆盖 api/*.js 的方法体，以及视图/组件里直接 `http.get('/...')` 的调用。
    """
    calls = []
    method_re = re.compile(r"http\.(get|post|put|patch|delete)\s*\(")
    files = sorted(list(FRONTEND_SRC.rglob("*.js")) + list(FRONTEND_SRC.rglob("*.vue")))
    for f in files:
        if "node_modules" in f.parts:
            continue
        src = f.read_text(encoding="utf-8")
        for m in method_re.finditer(src):
            method = m.group(1).upper()
            # 从 ( 后找到开引号（` 或 ' 或 "）
            i = m.end()
            if i >= len(src):
                continue
            quote = src[i]
            if quote not in "`'\"\"'":
                continue
            # 模板字符串感知扫描：`` 关闭外层；${...} 内的 ` 视为字面量；深度0的 `?` 为 query 起点
            j = i + 1
            depth = 0
            escaped = False
            while j < len(src):
                ch = src[j]
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == "$" and j + 1 < len(src) and src[j + 1] == "{":
                    depth += 1
                    j += 1
                elif ch == "}":
                    depth = max(0, depth - 1)
                elif ch == "?" and depth == 0:
                    break  # query 起点，路径匹配只关心 base path
                elif ch == quote and depth == 0:
                    break
                j += 1
            raw = src[i + 1:j]
            if quote != "`":
                raw = raw.replace("${", "{").replace("}", "")
            else:
                # 先移除含反引号的模板条件块（它们是 query 构建器，不属于路径）
                raw = re.sub(r"\$\{[^{}]*`[^`]*`[^{}]*\}", "", raw)
                # 再替换普通路径参数
                raw = re.sub(r"\$\{[^}]*\}", "{}", raw)
            path = raw.strip()
            if not path.startswith("/"):
                continue
            # 前端 baseURL=/api，补前缀后与后端 openapi 路径对齐
            calls.append((f.name, method, "/api" + path))
    return calls


def get_backend_routes():
    """用 app.openapi() 取全部真实路由（include_router 延迟展开为 _IncludedRouter）。

    返回 {规范化路径: [METHOD,...]}，`{param}` → `{}`。
    """
    sys.path.insert(0, str(ROOT))
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite:///./data/app.db")
    from app.main import app
    routes: dict[str, list[str]] = {}
    for path, operations in app.openapi().get("paths", {}).items():
        norm = re.sub(r"\{[^}]*\}", "{}", path)
        methods = [m.upper() for m in operations if m.upper() in
                   ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")]
        routes[norm] = methods
    return routes


def main() -> int:
    calls = extract_frontend_calls()
    routes = get_backend_routes()
    print(f"前端调用 {len(calls)} 个 | 后端路由 {len(routes)} 条")
    missing = []
    for fname, method, path in calls:
        if path not in routes or method not in routes[path]:
            missing.append((fname, method, path, routes.get(path)))
    if not missing:
        print("✅ 前端全部调用路径在后端都有对应路由")
        return 0
    print(f"\n❌ {len(missing)} 个前端调用后端无匹配路由（联调 404 风险）:")
    for fname, method, path, have in missing:
        print(f"  {fname}: {method} {path}  <- 后端 {have}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
