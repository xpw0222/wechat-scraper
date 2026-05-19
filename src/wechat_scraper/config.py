# src/wechat_scraper/config.py
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.ini"


@dataclass(frozen=True)
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str

    def as_pymysql_kwargs(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "charset": self.charset,
        }


@dataclass(frozen=True)
class HTTPConfig:
    user_agent: str
    accept_language: str
    timeout_seconds: int
    sleep_min: float
    sleep_max: float


@dataclass(frozen=True)
class NetworkConfig:
    # 解析后的本地 IP 列表，若为空则表示不绑定，使用系统默认单 IP
    ip_pool: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AppConfig:
    db: DBConfig
    http: HTTPConfig
    network: NetworkConfig  # 新增网络配置分区


def load_config(path: Path | str | None = None) -> AppConfig:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"Config not found at {p}. Copy config/config.example.ini to config/config.ini "
            f"and fill in credentials."
        )
    cp = configparser.ConfigParser()
    cp.read(p, encoding="utf-8")
    
    # 解析 IP 池字符串为 list
    ip_pool_str = cp.get("network", "ip_pool", fallback="")
    ip_pool = [ip.strip() for ip in ip_pool_str.split(",") if ip.strip()]

    return AppConfig(
        db=DBConfig(
            host=cp.get("database", "host"),
            port=cp.getint("database", "port"),
            user=cp.get("database", "user"),
            password=cp.get("database", "password"),
            database=cp.get("database", "database"),
            charset=cp.get("database", "charset", fallback="utf8mb4"),
        ),
        http=HTTPConfig(
            user_agent=cp.get("http", "user_agent"),
            accept_language=cp.get("http", "accept_language", fallback="zh-CN,zh;q=0.9"),
            timeout_seconds=cp.getint("http", "timeout_seconds", fallback=30),
            sleep_min=cp.getfloat("http", "sleep_min", fallback=0.8),
            sleep_max=cp.getfloat("http", "sleep_max", fallback=2.5),
        ),
        network=NetworkConfig(
            ip_pool=ip_pool
        ),
    )
