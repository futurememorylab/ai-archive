"""GcsService — thin wrapper over google-cloud-storage. Uploads proxy
files to the configured bucket and returns gs:// URIs for the GCS
AIInputStore adapter."""

import base64
import hashlib
from datetime import timedelta
from pathlib import Path

import google.auth
import google.auth.transport.requests
from google.cloud import storage  # type: ignore[import-not-found]

_HASH_CHUNK = 8 * 1024 * 1024


def _local_md5_b64(path: Path) -> str:
    """Base64-encoded MD5 of a local file, matching GCS's blob.md5_hash."""
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode()


class GcsService:
    def __init__(self, bucket_name: str) -> None:
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)

    @property
    def bucket_name(self) -> str:
        return self._bucket.name

    def gs_uri(self, clip_id: int) -> str:
        return f"gs://{self._bucket.name}/clips/{clip_id}.mov"

    def upload_if_absent(self, clip_id: int, local_path: Path, mime: str) -> str:
        blob_name = f"clips/{clip_id}.mov"
        # Blob names are keyed only on clip_id, and a stale/orphan blob can
        # outlive its DB row (an old GCS object whose uploaded_clips parent is
        # gone). Presence alone is NOT proof of content: re-uploading a reused
        # clip_id with different bytes must overwrite, or playback silently
        # serves the stale media. Compare the stored md5 (a metadata read, no
        # download) and only skip the upload when the content already matches.
        existing = self._bucket.get_blob(blob_name)
        if existing is None or existing.md5_hash != _local_md5_b64(local_path):
            # Setting chunk_size flips upload_from_filename into resumable mode,
            # so a slow upload of a multi-hundred-MB proxy isn't bounded by the
            # default 120s single-shot timeout.
            blob = self._bucket.blob(blob_name, chunk_size=_HASH_CHUNK)
            blob.upload_from_filename(str(local_path), content_type=mime, timeout=1800)
        return f"gs://{self._bucket.name}/{blob_name}"

    def delete(self, clip_id: int) -> None:
        blob = self._bucket.blob(f"clips/{clip_id}.mov")
        blob.delete()

    def thumb_uri(self, clip_id: int) -> str:
        return f"gs://{self._bucket.name}/thumbs/{clip_id}.jpg"

    def download_thumb(self, clip_id: int, dest: Path) -> bool:
        """Download thumbs/{clip_id}.jpg to dest. Return True only if the blob
        existed and a non-empty file was written; False on miss or empty body.
        Blocking (network) — call via asyncio.to_thread."""
        blob = self._bucket.get_blob(f"thumbs/{clip_id}.jpg")
        if blob is None:
            return False
        try:
            blob.download_to_filename(str(dest))
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        if dest.exists() and dest.stat().st_size > 0:
            return True
        dest.unlink(missing_ok=True)
        return False

    def upload_thumb(self, clip_id: int, local_path: Path) -> str:
        """Upload local_path to thumbs/{clip_id}.jpg, overwriting
        unconditionally. JPEGs are tiny, so overwriting on every write kills
        the stale-blob / clip-id-reuse risk (ADR 0070) at write time without
        an md5 compare. Blocking — call via asyncio.to_thread."""
        blob = self._bucket.blob(f"thumbs/{clip_id}.jpg")
        blob.upload_from_filename(str(local_path), content_type="image/jpeg")
        return self.thumb_uri(clip_id)

    def signed_url(self, gs_uri: str, *, expires_s: int = 3600) -> str:
        """V4 signed URL for a gs:// handle (e.g. an UploadedRef.handle).

        Blocking (may call the IAM credentials API) -- callers in async
        context must wrap in asyncio.to_thread. With a key file
        (GOOGLE_APPLICATION_CREDENTIALS, local dev) the library signs
        directly; on Cloud Run ADC has no private key, so fall back to
        IAM signBlob (needs roles/iam.serviceAccountTokenCreator on the
        runtime SA -- see deploy/README.md).
        """
        bucket_name, _, blob_name = gs_uri.removeprefix("gs://").partition("/")
        blob = self._client.bucket(bucket_name).blob(blob_name)
        expiration = timedelta(seconds=expires_s)
        try:
            return blob.generate_signed_url(version="v4", expiration=expiration)
        except AttributeError:
            # ADC without a private key (Cloud Run): sign via IAM.
            credentials, _ = google.auth.default()
            credentials.refresh(google.auth.transport.requests.Request())
            return blob.generate_signed_url(
                version="v4",
                expiration=expiration,
                service_account_email=credentials.service_account_email,
                access_token=credentials.token,
            )
