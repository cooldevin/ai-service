"""
包入口模块（Package Entrypoint）
【业务含义】：定义整个 `ai-service` 包被当作命令行工具直接运行时的默认入口动作。
【技术含义】：
1. `__init__.py` 的存在告诉 Python 解释器：`ai_service` 目录是一个可导入的 Python 包（类似 Java 的 package）。
2. 在 `pyproject.toml` 的 `[project.scripts]` 中配置了 `ai-service = "ai_service:main"`，
   执行 `ai-service` 命令时就会调用这里的 `main()` 函数。
"""


# `-> None` 是 Python 的类型注解（Type Hint），表示该函数不返回有意义的值（等价于返回 None，类似 Java 的 void）。
# 为什么写类型注解：Python 运行时不强制检查它，但能让 IDE 智能提示、静态检查工具更准确，代码可读性更高。
def main() -> None:
    print("Hello from ai-service!")
