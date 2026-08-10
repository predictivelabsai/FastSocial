from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.config import Config

from fastsocial.config import settings


class MediaStorage:
    def put(self, key: str, body: bytes, content_type: str) -> str:
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def url(self, key: str, expires: int = 3600) -> str:
        raise NotImplementedError

    def health(self) -> dict:
        raise NotImplementedError


class LocalStorage(MediaStorage):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid storage key")
        return path

    def put(self, key: str, body: bytes, content_type: str) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def url(self, key: str, expires: int = 3600) -> str:
        return f"/media/file/{key}"

    def health(self) -> dict:
        return {"backend": "local", "ok": self.root.exists(), "path": str(self.root)}


class R2Storage(MediaStorage):
    def __init__(self):
        cfg = settings()
        self.bucket = cfg.r2_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=cfg.r2_endpoint,
            aws_access_key_id=cfg.r2_access_key_id,
            aws_secret_access_key=cfg.r2_secret_access_key,
            region_name=cfg.r2_region,
            config=Config(signature_version="s3v4"),
        )

    def put(self, key: str, body: bytes, content_type: str) -> str:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            ChecksumSHA256=base64.b64encode(hashlib.sha256(body).digest()).decode(),
        )
        return key

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def url(self, key: str, expires: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires,
        )

    def health(self) -> dict:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return {"backend": "r2", "bucket": self.bucket, "ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"backend": "r2", "bucket": self.bucket, "ok": False, "error": str(exc)}


@lru_cache(maxsize=1)
def media_storage() -> MediaStorage:
    cfg = settings()
    if cfg.media_storage.lower() == "r2":
        missing = [
            name
            for name, value in {
                "R2_ENDPOINT": cfg.r2_endpoint,
                "R2_ACCESS_KEY_ID": cfg.r2_access_key_id,
                "R2_SECRET_ACCESS_KEY": cfg.r2_secret_access_key,
                "R2_BUCKET": cfg.r2_bucket,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"R2 storage selected but configuration is missing: {', '.join(missing)}"
            )
        return R2Storage()
    return LocalStorage(cfg.media_local_dir)


def object_key(workspace_id: str, media_id: str, filename: str) -> str:
    cfg = settings()
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
    return f"{cfg.r2_key_prefix}/{workspace_id}/{media_id}/{safe_name}"
