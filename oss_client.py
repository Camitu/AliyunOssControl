"""
阿里云 OSS 管理工具 - API 封装层
基于 alibabacloud_oss_v2 (Python SDK V2)

提供以下功能：
- Bucket 信息/容量查询
- 文件/目录列举（支持按前缀、分隔符过滤）
- 文件上传（简单上传 / 大文件分片上传，支持断点续传）
- 文件下载（简单下载 / 大文件分片下载，支持断点续传）
- 文件删除（单个 / 批量）
- 创建/删除目录（OSS 无真实目录，通过零字节对象模拟）
- 获取预签名 URL（上传/下载）
- 文件重命名（通过拷贝 + 删除实现）
- 判断文件是否存在
"""

import os
import datetime
import alibabacloud_oss_v2 as oss
from alibabacloud_oss_v2.credentials import StaticCredentialsProvider
from typing import Optional, Callable, List, Dict, Any


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def fmt_size(size_bytes: int) -> str:
    """将字节数格式化为可读字符串"""
    if size_bytes is None or size_bytes < 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}" if unit != "B" else f"{size_bytes} B"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def fmt_time(dt: Optional[datetime.datetime]) -> str:
    """格式化时间"""
    if dt is None:
        return "—"
    # V2 SDK 的 last_modified 可能是字符串
    if isinstance(dt, str):
        return dt
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# OSS 客户端封装
# ---------------------------------------------------------------------------

class OssClient:
    """阿里云 OSS 操作客户端（V2 SDK）"""

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        endpoint: str,
        bucket: str,
        region: str = "cn-hangzhou",
    ):
        """
        初始化 OSS 客户端

        Args:
            access_key_id:     AccessKey ID
            access_key_secret: AccessKey Secret
            endpoint:          OSS 访问域名，如 oss-cn-hangzhou.aliyuncs.com
            bucket:            默认操作的 Bucket 名称
            region:            地域 ID，如 cn-hangzhou
        """
        self.bucket = bucket
        self.endpoint = endpoint
        self.region = region

        creds = StaticCredentialsProvider(access_key_id, access_key_secret)
        cfg = oss.Config(
            region=region,
            endpoint=endpoint,
            credentials_provider=creds,
        )
        self._client = oss.Client(cfg)

    # ---- Bucket 相关 --------------------------------------------------

    def get_bucket_info(self) -> dict:
        """获取 Bucket 基本信息（创建时间、地域、权限等）"""
        req = oss.GetBucketInfoRequest(bucket=self.bucket)
        result = self._client.get_bucket_info(req)
        bi = result.bucket_info
        owner_id = bi.owner.id if bi.owner else "—"
        owner_name = bi.owner.display_name if bi.owner else "—"
        return {
            "name": bi.name,
            "region": bi.location,
            "storage_class": bi.storage_class,
            "creation_date": fmt_time(bi.creation_date),
            "acl": bi.acl,
            "owner_id": owner_id,
            "owner_name": owner_name,
            "intranet_endpoint": bi.intranet_endpoint,
            "extranet_endpoint": bi.extranet_endpoint,
        }

    def get_bucket_stat(self) -> dict:
        """获取 Bucket 存储容量统计"""
        req = oss.GetBucketStatRequest(bucket=self.bucket)
        result = self._client.get_bucket_stat(req)
        return {
            "storage": fmt_size(result.storage),
            "object_count": result.object_count,
            "multi_part_upload_count": result.multi_part_upload_count,
            "live_channel_count": result.live_channel_count,
            "standard_storage": fmt_size(result.standard_storage),
            "infrequent_access_storage": fmt_size(result.infrequent_access_storage),
            "archive_storage": fmt_size(result.archive_storage),
            "cold_archive_storage": fmt_size(result.cold_archive_storage),
        }

    # ---- 文件/目录列举 -------------------------------------------------

    def list_objects(
        self,
        prefix: str = "",
        delimiter: str = "",
        max_keys: int = 1000,
    ) -> Dict[str, List[dict]]:
        """
        列举 Bucket 中的对象/目录

        Args:
            prefix:    对象名前缀（用于列举指定目录，如 "images/"）
            delimiter: 分隔符（设为 "/" 时可列出子目录层级）
            max_keys:  单次返回最大对象数

        Returns:
            dict: {"files": [...], "dirs": [...], "is_truncated": bool}
                files 每个元素: {key, size, size_display, last_modified, etag, storage_class}
                dirs  每个元素: {prefix}
        """
        files = []
        dirs = []

        paginator = self._client.list_objects_v2_paginator()

        for page in paginator.iter_page(oss.ListObjectsV2Request(
            bucket=self.bucket,
            prefix=prefix,
            delimiter=delimiter,
            max_keys=max_keys,
        )):
            # 文件
            if page.contents:
                for obj in page.contents:
                    files.append({
                        "key": obj.key,
                        "size": obj.size,
                        "size_display": fmt_size(obj.size),
                        "last_modified": fmt_time(obj.last_modified),
                        "etag": obj.etag,
                        "storage_class": obj.storage_class,
                    })
            # 子目录（当 delimiter="/" 时返回 common_prefixes）
            if page.common_prefixes:
                for cp in page.common_prefixes:
                    dirs.append({"prefix": cp.prefix})

        return {"files": files, "dirs": dirs}

    def list_all_objects(self, prefix: str = "") -> List[dict]:
        """列举指定前缀下的所有文件（不区分目录层级）"""
        result = self.list_objects(prefix=prefix, delimiter="")
        return result["files"]

    def list_directory(self, prefix: str = "") -> Dict[str, List[dict]]:
        """列举指定目录下的文件和子目录（使用 "/" 作为分隔符）"""
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        return self.list_objects(prefix=prefix, delimiter="/")

    # ---- 文件上传 ------------------------------------------------------

    def upload_file(
        self,
        local_path: str,
        oss_key: str,
        progress_fn: Optional[Callable] = None,
    ) -> dict:
        """
        上传文件到 OSS（自动选择简单上传或分片上传）

        Args:
            local_path:  本地文件路径
            oss_key:     OSS 中的对象名
            progress_fn: 进度回调，签名: callback(bytes_sent, total_bytes)

        Returns:
            dict: {status_code, request_id, etag}
        """
        file_size = os.path.getsize(local_path)
        # 大于 100MB 使用分片上传（断点续传）
        if file_size > 100 * 1024 * 1024:
            return self._upload_big_file(local_path, oss_key, progress_fn)
        else:
            return self._upload_small_file(local_path, oss_key, progress_fn)

    def _upload_small_file(
        self,
        local_path: str,
        oss_key: str,
        progress_fn: Optional[Callable] = None,
    ) -> dict:
        """简单上传（≤100MB）"""
        req = oss.PutObjectRequest(
            bucket=self.bucket,
            key=oss_key,
            progress_fn=progress_fn,
        )
        result = self._client.put_object_from_file(req, local_path)
        return {
            "status_code": result.status_code,
            "request_id": result.request_id,
            "etag": result.etag,
        }

    def _upload_big_file(
        self,
        local_path: str,
        oss_key: str,
        progress_fn: Optional[Callable] = None,
    ) -> dict:
        """分片上传（>100MB，支持断点续传）"""
        req = oss.PutObjectRequest(
            bucket=self.bucket,
            key=oss_key,
        )
        uploader = oss.Uploader(self._client, enable_checkpoint=True)
        result = uploader.upload_file(req, local_path, progress_fn=progress_fn)
        return {
            "status_code": result.status_code,
            "request_id": result.request_id,
            "upload_id": result.upload_id,
        }

    # ---- 文件下载 ------------------------------------------------------

    def download_file(
        self,
        oss_key: str,
        local_path: str,
        progress_fn: Optional[Callable] = None,
    ) -> dict:
        """
        从 OSS 下载文件到本地

        Args:
            oss_key:     OSS 中的对象名
            local_path:  本地保存路径
            progress_fn: 进度回调

        Returns:
            dict: {status_code, request_id, content_length}
        """
        req = oss.GetObjectRequest(
            bucket=self.bucket,
            key=oss_key,
            progress_fn=progress_fn,
        )
        # 先获取文件大小判断是否需要分片下载
        info = self.get_file_info(oss_key)
        file_size = info.get("size", 0) or 0

        if file_size > 100 * 1024 * 1024:
            downloader = oss.Downloader(self._client, enable_checkpoint=True)
            result = downloader.download_file(req, local_path)
            return {
                "status_code": result.status_code,
                "request_id": result.request_id,
            }
        else:
            result = self._client.get_object_to_file(req, local_path)
            return {
                "status_code": result.status_code,
                "request_id": result.request_id,
                "content_length": result.content_length,
            }

    # ---- 文件删除 ------------------------------------------------------

    def delete_file(self, oss_key: str) -> dict:
        """
        删除单个文件

        Args:
            oss_key: OSS 中的对象名

        Returns:
            dict: {status_code, request_id}
        """
        req = oss.DeleteObjectRequest(bucket=self.bucket, key=oss_key)
        result = self._client.delete_object(req)
        return {
            "status_code": result.status_code,
            "request_id": result.request_id,
        }

    def delete_files(self, oss_keys: List[str]) -> dict:
        """
        批量删除文件

        Args:
            oss_keys: OSS 对象名列表

        Returns:
            dict: {status_code, deleted: [...], request_id}
        """
        objects = [oss.DeleteObject(key=k) for k in oss_keys]
        req = oss.DeleteMultipleObjectsRequest(
            bucket=self.bucket,
            objects=objects,
        )
        result = self._client.delete_multiple_objects(req)
        return {
            "status_code": result.status_code,
            "request_id": result.request_id,
            "deleted": [d.key for d in result.deleted_objects],
        }

    # ---- 目录操作（OSS 无真实目录，通过零字节对象模拟）--------------------

    def create_directory(self, dir_path: str) -> dict:
        """
        创建目录（上传一个以 "/" 结尾的零字节对象）

        Args:
            dir_path: 目录路径，如 "images/2024/"

        Returns:
            dict: {status_code, request_id}
        """
        if not dir_path.endswith("/"):
            dir_path += "/"
        # 检查是否已存在
        if self.file_exists(dir_path):
            return {"status_code": 200, "request_id": "", "msg": "目录已存在"}
        req = oss.PutObjectRequest(
            bucket=self.bucket,
            key=dir_path,
            content_length=0,
        )
        result = self._client.put_object(req)
        return {
            "status_code": result.status_code,
            "request_id": result.request_id,
        }

    def delete_directory(self, dir_path: str) -> dict:
        """
        删除目录（删除该前缀下所有对象）

        Args:
            dir_path: 目录路径

        Returns:
            dict: {deleted_count, deleted_keys}
        """
        if not dir_path.endswith("/"):
            dir_path += "/"
        all_objects = self.list_all_objects(prefix=dir_path)
        # 加上目录自身（零字节对象）
        keys = [obj["key"] for obj in all_objects]
        if keys:
            self.delete_files(keys)
        # 也尝试删除目录标记对象自身
        try:
            self.delete_file(dir_path)
        except Exception:
            pass
        return {"deleted_count": len(keys), "deleted_keys": keys}

    # ---- 预签名 URL ----------------------------------------------------

    def get_presigned_url(
        self,
        oss_key: str,
        expires: int = 3600,
        method: str = "GET",
    ) -> dict:
        """
        生成预签名 URL

        Args:
            oss_key: OSS 对象名
            expires: 过期时间（秒），默认 3600 秒（1 小时），最大 7 天
            method:  HTTP 方法，"GET" 用于下载，"PUT" 用于上传

        Returns:
            dict: {url, method, expiration, signed_headers}
        """
        if method.upper() == "PUT":
            req = oss.PutObjectRequest(bucket=self.bucket, key=oss_key)
        else:
            req = oss.GetObjectRequest(bucket=self.bucket, key=oss_key)

        result = self._client.presign(
            req,
            expires=datetime.timedelta(seconds=expires),
        )
        return {
            "url": result.url,
            "method": result.method,
            "expiration": fmt_time(result.expiration),
            "signed_headers": dict(result.signed_headers),
        }

    def get_download_url(self, oss_key: str, expires: int = 3600) -> str:
        """获取下载用的预签名 URL"""
        return self.get_presigned_url(oss_key, expires, "GET")["url"]

    def get_upload_url(self, oss_key: str, expires: int = 3600) -> str:
        """获取上传用的预签名 URL"""
        return self.get_presigned_url(oss_key, expires, "PUT")["url"]

    # ---- 文件信息 ------------------------------------------------------

    def get_file_info(self, oss_key: str) -> dict:
        """
        获取文件元信息（不下载文件内容）

        Args:
            oss_key: OSS 对象名

        Returns:
            dict: {size, last_modified, etag, content_type, metadata}
        """
        req = oss.HeadObjectRequest(bucket=self.bucket, key=oss_key)
        result = self._client.head_object(req)
        return {
            "size": result.content_length,
            "size_display": fmt_size(result.content_length),
            "last_modified": fmt_time(result.last_modified),
            "etag": result.etag,
            "content_type": result.content_type,
            "metadata": dict(result.metadata) if result.metadata else {},
        }

    def file_exists(self, oss_key: str) -> bool:
        """判断文件/目录是否存在"""
        try:
            self.get_file_info(oss_key)
            return True
        except oss.exceptions.ServiceError as e:
            if e.code == "NoSuchKey":
                return False
            raise
        except Exception:
            return False

    # ---- 文件拷贝/重命名 ------------------------------------------------

    def copy_object(self, src_key: str, dst_key: str) -> dict:
        """
        拷贝文件（同 Bucket 内）

        Args:
            src_key: 源对象名
            dst_key: 目标对象名

        Returns:
            dict: {status_code, request_id}
        """
        req = oss.CopyObjectRequest(
            bucket=self.bucket,
            key=dst_key,
            source_key=src_key,
            source_bucket=self.bucket,
        )
        result = self._client.copy_object(req)
        return {
            "status_code": result.status_code,
            "request_id": result.request_id,
        }

    def rename_object(self, src_key: str, dst_key: str) -> dict:
        """
        重命名文件（拷贝后再删除源文件）

        Args:
            src_key: 源对象名
            dst_key: 目标对象名（新名称）

        Returns:
            dict: {status_code, request_id}
        """
        result = self.copy_object(src_key, dst_key)
        self.delete_file(src_key)
        return result

    # ---- 列出所有 Bucket ------------------------------------------------

    def list_buckets(self) -> List[dict]:
        """列出当前账号下所有 Bucket"""
        req = oss.ListBucketsRequest()
        result = self._client.list_buckets(req)
        buckets = []
        for b in result.buckets:
            buckets.append({
                "name": b.name,
                "location": b.location,
                "creation_date": fmt_time(b.creation_date),
                "storage_class": b.storage_class,
            })
        return buckets
