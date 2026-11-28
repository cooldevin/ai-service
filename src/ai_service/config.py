"""
全局配置管理模块（Configuration Module）
【业务含义】：统一管理服务名称、日志级别、大模型网关地址、API 密钥以及计费单价等环境配置。
【技术含义】：基于 `pydantic-settings` 实现“环境变量 / .env 文件 -> 强类型 Python 对象”的自动映射与校验（类似 Spring Boot 的 `@ConfigurationProperties`）。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    配置模型类：继承自 `BaseSettings` 后，实例化时会自动按优先级读取配置：
    系统环境变量 > `.env` 文件 > 类属性默认值（忽略大小写，如环境变量 `LLM_API_KEY` 会自动匹配 `llm_api_key`）。
    """

    # `model_config` 是 Pydantic v2 的固定配置属性名，用于定制当前配置类的加载行为：
    # - `env_file=".env"`：默认从工作目录下的 `.env` 文件读取键值对；
    # - `env_file_encoding="utf-8"`：防止 `.env` 中包含中文注释或字符时在 Windows 下乱码；
    # - `extra="ignore"`：如果 `.env` 或环境变量里有多余未定义的配置项，直接忽略而不抛校验异常，保证向前兼容。
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 【业务】：应用服务名称，用于日志标识或文档展示
    # 【技术】：`字段名: 类型 = 默认值`，Pydantic 会自动把读到的字符串转换并校验为声明的类型
    app_name: str = "ai-service"

    # 【业务】：全局日志打印级别（DEBUG / INFO / WARNING / ERROR）
    log_level: str = "INFO"

    # ⚠️【注意】：下面第 29 行和第 31 行重复定义了 `llm_api_key`，Python 类定义中同名属性后者会覆盖前者，可删掉一处冗余定义。
    llm_api_key: str = ""
    # llm_secret: str = ""
    # 【业务】：调用阿里云百炼（或兼容 OpenAI 协议服务）的密钥
    # 【为什么这么写】：默认给空字符串而不是硬编码真实 Key，强制通过 `.env` 或环境变量注入，防止密钥泄露到 Git 仓库
    llm_api_key: str = ""  # 只从 .env 读，绝不写进代码

    # 【业务】：大模型接口的基础地址，这里指向阿里云百炼提供的 OpenAI 兼容网关
    # 【为什么这么写】：国内很多大模型（百炼 Qwen、DeepSeek 等）都兼容 OpenAI 接口协议，只需替换 `base_url` 就能复用官方 `openai` SDK，无需换 SDK
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 【业务】：默认选用的大模型名称（通义千问 Plus）
    llm_model: str = "qwen-plus"

    # 【业务】：输入（Prompt）与输出（Completion）每百万 Token 的人民币单价，用于可观测性成本估算
    # 【技术】：声明为 `float` 类型，即使 `.env` 里写成字符串 `"0.8"`，Pydantic 也会自动转换成浮点数 `0.8`
    llm_price_input_per_1m: float = 0.0   # 每百万输入 token 单价（元），去百炼控制台价格页查了填，不填就只记 token
    llm_price_output_per_1m: float = 0.0  # 每百万输出 token 单价（元）


# 【技术含义】：模块级单例（Module-level Singleton）。
# Python 模块在进程生命周期内首次被 `import` 时执行一次，后续所有地方 `from ai_service.config import settings` 拿到的都是同一个实例，避免重复读磁盘解析 `.env`。
settings = Settings()
