"""Private immutable S3 bundles; the database pointer remains publication authority."""
import base64
import hashlib
import json
import os
import re

from api.storage import canonical


class ArtifactStorageError(OSError):
    """Storage failed; no result pointer may be committed."""


class S3Artifacts:
    def __init__(self, bucket, prefix="urban-air", client=None, kms_key=None):
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
            raise ValueError("An explicit S3 bucket name is required")
        prefix = prefix.strip("/")
        if not prefix or any(p in {"", ".", ".."} for p in prefix.split("/")) or not re.fullmatch(r"[A-Za-z0-9/_-]+", prefix):
            raise ValueError("Asset prefix must contain bounded safe path components")
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client("s3", region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"),
                                  endpoint_url=os.getenv("UA_ASSET_ENDPOINT") or None,
                                  config=Config(connect_timeout=5, read_timeout=20,
                                                retries={"mode":"standard", "max_attempts":3}))
        self.bucket, self.prefix, self.client, self.kms_key = bucket, prefix, client, kms_key

    @classmethod
    def from_env(cls):
        return cls(os.environ["UA_ASSET_BUCKET"], os.getenv("UA_ASSET_PREFIX", "urban-air"),
                   kms_key=os.getenv("UA_ASSET_KMS_KEY") or None)

    def _key(self, run_id, fence):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", run_id) or isinstance(fence, bool) or not isinstance(fence, int) or fence < 1:
            raise ValueError("A safe run ID and positive fencing token are required")
        return f"{self.prefix}/artifacts/{run_id}/{fence}/bundle.json"

    def atomic_artifact(self, run_id, fence, payload):
        from botocore.exceptions import BotoCoreError, ClientError
        content = canonical(payload).encode("utf-8")
        sha = hashlib.sha256(content).hexdigest()
        checksum = "sha256:" + sha
        key = self._key(run_id, fence)
        pointer = f"s3://{self.bucket}/{key}"
        arguments = {"Bucket":self.bucket, "Key":key, "Body":content, "ContentType":"application/json",
                     "IfNoneMatch":"*", "ChecksumSHA256":base64.b64encode(bytes.fromhex(sha)).decode("ascii"),
                     "Metadata":{"sha256":sha}, "ServerSideEncryption":"aws:kms" if self.kms_key else "AES256"}
        if self.kms_key:
            arguments["SSEKMSKeyId"] = self.kms_key
        try:
            self.client.put_object(**arguments)
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise ArtifactStorageError("Immutable asset upload failed; publication is blocked") from exc
            # A retry may reuse an already durable identical bundle, never overwrite it.
        except BotoCoreError as exc:
            raise ArtifactStorageError("Asset transport failed; publication is blocked") from exc
        # Read after write verifies bytes, including a pre-existing conditional-write retry.
        self.read_artifact(pointer, checksum)
        return pointer, checksum

    def read_artifact(self, pointer, expected_checksum=None):
        from botocore.exceptions import BotoCoreError, ClientError
        prefix = f"s3://{self.bucket}/{self.prefix}/artifacts/"
        if not isinstance(pointer, str) or not pointer.startswith(prefix):
            raise ValueError("Artifact pointer is outside configured asset scope")
        suffix = pointer[len(prefix):]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}/[1-9][0-9]*/bundle\.json", suffix):
            raise ValueError("Artifact pointer has invalid attempt path")
        if not isinstance(expected_checksum, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_checksum):
            raise ValueError("A committed SHA-256 checksum is required")
        key = pointer[len(f"s3://{self.bucket}/"):]
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key, ChecksumMode="ENABLED")
            stream = response["Body"]
            try:
                length = response.get("ContentLength")
                if not isinstance(length, int) or not 0 <= length <= 128_000_000:
                    raise ArtifactStorageError("Asset length is absent or exceeds the bounded bundle allowance")
                content = stream.read(length + 1)
                if len(content) != length:
                    raise ArtifactStorageError("Asset stream was incomplete")
            finally:
                stream.close()
        except (BotoCoreError, ClientError) as exc:
            raise ArtifactStorageError("Committed asset could not be read") from exc
        if "sha256:" + hashlib.sha256(content).hexdigest() != expected_checksum:
            raise ValueError("Committed artifact checksum mismatch")
        return json.loads(content.decode("utf-8"))
