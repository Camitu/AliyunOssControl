"""
OSS 配置管理 - 基于 SQLite 本地存储

用于持久化保存 OSS 连接配置（AccessKey、Bucket、Endpoint），
避免每次启动手动输入。
"""

import sqlite3
import os
import sys
from typing import Optional, Dict


def _get_app_dir() -> str:
    """获取应用程序所在目录（兼容 PyInstaller 打包后的 exe）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后：exe 所在目录
        return os.path.dirname(sys.executable)
    else:
        # 源码运行：脚本所在目录
        return os.path.dirname(os.path.abspath(__file__))


# 数据库文件路径，与 exe / 脚本同目录
_DB_PATH = os.path.join(_get_app_dir(), "oss_config.db")


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接"""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表（首次运行自动建表）"""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_key_id       TEXT NOT NULL,
            access_key_secret   TEXT NOT NULL,
            bucket              TEXT NOT NULL,
            endpoint            TEXT NOT NULL,
            region              TEXT NOT NULL DEFAULT '',
            created_at          TEXT DEFAULT (datetime('now','localtime')),
            updated_at          TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    conn.close()


def save_config(
    access_key_id: str,
    access_key_secret: str,
    bucket: str,
    endpoint: str,
    region: str = "",
):
    """保存或更新配置（仅保留一条记录）"""
    init_db()
    conn = _get_conn()
    conn.execute("""
        INSERT INTO config (id, access_key_id, access_key_secret, bucket, endpoint, region, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, datetime('now','localtime'))
        ON CONFLICT(id) DO UPDATE SET
            access_key_id   = excluded.access_key_id,
            access_key_secret = excluded.access_key_secret,
            bucket          = excluded.bucket,
            endpoint        = excluded.endpoint,
            region          = excluded.region,
            updated_at      = datetime('now','localtime')
    """, (access_key_id, access_key_secret, bucket, endpoint, region))
    conn.commit()
    conn.close()


def load_config() -> Optional[Dict[str, str]]:
    """加载已保存的配置，不存在则返回 None"""
    init_db()
    conn = _get_conn()
    row = conn.execute("SELECT * FROM config WHERE id = 1").fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "access_key_id": row["access_key_id"],
        "access_key_secret": row["access_key_secret"],
        "bucket": row["bucket"],
        "endpoint": row["endpoint"],
        "region": row["region"],
    }


def delete_config():
    """删除已保存的配置"""
    init_db()
    conn = _get_conn()
    conn.execute("DELETE FROM config WHERE id = 1")
    conn.commit()
    conn.close()


def has_config() -> bool:
    """检查是否已有保存的配置"""
    return load_config() is not None
