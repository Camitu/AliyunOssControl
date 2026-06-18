"""一键将 key.py 中的配置导入 SQLite，并验证连接"""
from config_manager import save_config
from oss_client import OssClient
from key import (
    OSS_ACCESS_KEY_ID,
    OSS_ACCESS_KEY_SECRET,
    BUCKET,
    ENDPOINT,
)

# 自动提取 region
region = ENDPOINT.replace(".aliyuncs.com", "").replace(".internal", "").replace("oss-", "")

print(f"导入配置: bucket={BUCKET}, endpoint={ENDPOINT}, region={region}")
save_config(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, BUCKET, ENDPOINT, region)

print("验证连接...")
client = OssClient(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, ENDPOINT, BUCKET, region)
info = client.get_bucket_info()
print(f"连接成功 — Bucket: {info['name']} ({info['region']})")
print("配置已导入 SQLite，可删除 key.py 中的硬编码密钥。")
