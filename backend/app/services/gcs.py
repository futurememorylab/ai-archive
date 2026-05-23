"""GcsService — thin wrapper over google-cloud-storage. Uploads proxy
files to the configured bucket and returns gs:// URIs for the GCS
AIInputStore adapter."""

from pathlib import Path

from google.cloud import storage  # type: ignore[import-not-found]


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
        # Setting chunk_size flips upload_from_filename into resumable mode,
        # so a slow upload of a multi-hundred-MB proxy isn't bounded by the
        # default 120s single-shot timeout.
        blob = self._bucket.blob(blob_name, chunk_size=8 * 1024 * 1024)
        if not blob.exists():
            blob.upload_from_filename(str(local_path), content_type=mime, timeout=1800)
        return f"gs://{self._bucket.name}/{blob_name}"

    def delete(self, clip_id: int) -> None:
        blob = self._bucket.blob(f"clips/{clip_id}.mov")
        blob.delete()
