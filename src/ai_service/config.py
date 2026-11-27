from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    app_name: str = "ai-service"
    log_level: str = "INFO"
    llm_api_key: str = ""
    # llm_secret: str = ""
    llm_api_key: str = ""  # 只从 .env 读，绝不写进代码
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"
    llm_price_input_per_1m: float = 0.0   # 每百万输入 token 单价（元），去百炼控制台价格页查了填，不填就只记 token
    llm_price_output_per_1m: float = 0.0  # 每百万输出 token 单价（元）


settings = Settings()
