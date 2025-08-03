"""
Voice v2 MinIO Manager - Performance-optimized file storage
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor

from minio import Minio
from minio.error import S3Error

from ..core.interfaces import FileManagerInterface
from ..core.exceptions import VoiceServiceError
from ..core.schemas import VoiceFileInfo, AudioFormat


logger = logging.getLogger(__name__)


class MinioFileManager(FileManagerInterface):
    """
    High-performance MinIO file manager for voice_v2

    Features:
    - Async operations with connection pooling
    - Presigned URLs for secure file access
    - Automatic bucket management
    - Performance-optimized file operations
    - SOLID principles compliance (SRP, OCP, LSP, ISP, DIP)
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket_name: str = "voice-files",
        secure: bool = False,
        region: str = "us-east-1",
        max_pool_size: int = 10
    ):
        """
        Initialize MinIO file manager

        Args:
            endpoint: MinIO server endpoint
            access_key: Access key for authentication
            secret_key: Secret key for authentication
            bucket_name: Default bucket name for voice files
            secure: Use HTTPS connection
            region: MinIO region
            max_pool_size: Maximum connection pool size
        """
        # MinIO configuration with proper typing
        self._endpoint = endpoint
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket_name = bucket_name
        self._secure = secure
        self._region = region
        self._max_pool_size = max_pool_size

        # Internal state - lazy initialization pattern
        self._client: Optional[Minio] = None
        self._initialized = False
        self._executor: Optional[ThreadPoolExecutor] = None

    async def initialize(self) -> None:
        """Initialize MinIO client with connection pooling"""
        try:
            # Create thread pool executor for blocking I/O
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_pool_size,
                thread_name_prefix="minio-"
            )

            # Create MinIO client
            self._client = Minio(
                endpoint=self._endpoint,
                access_key=self._access_key,
                secret_key=self._secret_key,
                secure=self._secure,
                region=self._region
            )

            # Ensure bucket exists
            await self._ensure_bucket_exists()

            self._initialized = True
            logger.info("MinioFileManager initialized - bucket: %s",
                       self._bucket_name)

        except Exception as e:
            logger.error("Failed to initialize MinioFileManager: %s", e, exc_info=True)
            raise VoiceServiceError(f"MinIO initialization failed: {e}") from e

    async def cleanup(self) -> None:
        """Cleanup resources"""
        if self._executor:
            self._executor.shutdown(wait=True)
            self._executor = None

        self._client = None
        self._initialized = False
        logger.info("MinioFileManager cleaned up")

    async def upload_file(
        self,
        file_data: bytes,
        object_key: str,
        content_type: str = "application/octet-stream",
        metadata: Optional[Dict[str, str]] = None
    ) -> VoiceFileInfo:
        """
        Upload file to MinIO storage

        Args:
            file_data: File binary data
            object_key: Object key in bucket
            content_type: MIME type of the file
            metadata: Optional metadata dictionary

        Returns:
            VoiceFileInfo: Information about uploaded file

        Raises:
            VoiceServiceError: If upload fails
        """
        if self._client is None:
            raise VoiceServiceError("MinIO client not initialized")
        await self._ensure_initialized()

        try:
            # Prepare metadata
            upload_metadata = metadata or {}
            upload_metadata.update({
                "upload-time": datetime.utcnow().isoformat(),
                "content-length": str(len(file_data))
            })

            # Perform async upload
            await asyncio.get_event_loop().run_in_executor(
                self._executor,
                self._upload_sync,
                file_data,
                object_key,
                content_type,
                upload_metadata
            )

            logger.debug("File uploaded: %s (%s bytes)", object_key, len(file_data))

            return VoiceFileInfo(
                file_id=str(uuid.uuid4()),
                original_filename=metadata.get("original_filename", object_key),
                mime_type=content_type,
                size_bytes=len(file_data),
                format=content_type.split('/')[-1] if '/' in content_type else 'unknown',
                created_at=datetime.utcnow().isoformat(),
                minio_bucket=self._bucket_name,
                minio_key=object_key
            )

        except Exception as e:
            logger.error("File upload failed for %s: %s", object_key, e, exc_info=True)
            raise VoiceServiceError(f"File upload failed: {e}") from e

    async def download_file(self, object_key: str, bucket_name: Optional[str] = None) -> bytes:
        """
        Download file from MinIO storage

        Args:
            object_key: Object key to download
            bucket_name: Bucket name (optional, defaults to configured bucket)

        Returns:
            File binary data

        Raises:
            VoiceServiceError: If download fails
        """
        await self._ensure_initialized()

        try:
            bucket = bucket_name or self._bucket_name
            data = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                lambda: self._download_sync_with_bucket(object_key, bucket)
            )

            logger.debug("File downloaded: %s (%s bytes)", object_key, len(data))
            return data

        except Exception as e:
            logger.error("File download failed for %s: %s", object_key, e, exc_info=True)
            raise VoiceServiceError(f"File download failed: {e}") from e

    def _download_sync_with_bucket(self, object_key: str, bucket_name: str) -> bytes:
        """Synchronous download operation with bucket selection"""
        if self._client is None:
            raise VoiceServiceError("MinIO client not initialized")

        response = self._client.get_object(
            bucket_name=bucket_name,
            object_name=object_key
        )
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    async def delete_file(self, object_key: str) -> bool:
        """
        Delete file from MinIO storage

        Args:
            object_key: Object key to delete

        Returns:
            True if deleted successfully

        Raises:
            VoiceServiceError: If deletion fails
        """
        await self._ensure_initialized()

        try:
            await asyncio.get_event_loop().run_in_executor(
                self._executor,
                self._delete_sync,
                object_key
            )

            logger.debug("File deleted: %s", object_key)
            return True

        except Exception as e:
            logger.error("File deletion failed for %s: %s", object_key, e, exc_info=True)
            raise VoiceServiceError(f"File deletion failed: {e}") from e

    async def generate_presigned_url(
        self,
        object_key: str,
        expires_hours: int = 1,
        method: str = "GET"
    ) -> str:
        """
        Generate presigned URL for secure file access

        Args:
            object_key: Object key for URL generation
            expires_hours: URL expiration time in hours
            method: HTTP method (GET, PUT, DELETE)

        Returns:
            Presigned URL string

        Raises:
            VoiceServiceError: If URL generation fails
        """
        await self._ensure_initialized()

        try:
            expires = timedelta(hours=expires_hours)

            url = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                self._generate_presigned_url_sync,
                object_key,
                expires,
                method
            )

            logger.debug("Presigned URL generated: %s (expires in %sh)", object_key, expires_hours)
            return url

        except Exception as e:
            logger.error("Presigned URL generation failed for %s: %s", object_key, e, exc_info=True)
            raise VoiceServiceError(f"Presigned URL generation failed: {e}") from e

    async def list_files(
        self,
        prefix: str = "",
        limit: int = 1000
    ) -> List[VoiceFileInfo]:
        """
        List files in bucket with optional prefix filter

        Args:
            prefix: Object key prefix filter
            limit: Maximum number of files to return

        Returns:
            List of VoiceFileInfo objects

        Raises:
            VoiceServiceError: If listing fails
        """
        await self._ensure_initialized()

        try:
            objects = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                self._list_objects_sync,
                prefix,
                limit
            )

            logger.debug("Listed %s files with prefix: '%s'", len(objects), prefix)
            return objects

        except Exception as e:
            logger.error("File listing failed with prefix '%s': %s", prefix, e, exc_info=True)
            raise VoiceServiceError(f"File listing failed: {e}") from e

    async def file_exists(self, object_key: str) -> bool:
        """
        Check if file exists in storage

        Args:
            object_key: Object key to check

        Returns:
            True if file exists
        """
        await self._ensure_initialized()

        try:
            await asyncio.get_event_loop().run_in_executor(
                self._executor,
                self._client.stat_object,
                self._bucket_name,
                object_key
            )
            return True

        except S3Error as e:
            if e.code == "NoSuchKey":
                return False
            raise VoiceServiceError(f"File existence check failed: {e}") from e
        except Exception as e:
            logger.error("File existence check failed for %s: %s", object_key, e, exc_info=True)
            raise VoiceServiceError(f"File existence check failed: {e}") from e

    def generate_object_key(
        self,
        agent_id: str,
        user_id: str,
        file_format: Optional[AudioFormat] = None,
        prefix: str = "voice"
    ) -> str:
        """
        Generate unique object key for file storage

        Args:
            agent_id: Agent identifier
            user_id: User identifier
            file_format: Audio file format
            prefix: Key prefix

        Returns:
            Unique object key
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]

        key = f"{prefix}/{agent_id}/{user_id}/{timestamp}_{unique_id}"

        if file_format:
            key += f".{file_format.value}"

        return key

    # Private methods
    async def _ensure_initialized(self) -> None:
        """Ensure manager is initialized"""
        if not self._initialized or not self._client:
            raise VoiceServiceError("MinioFileManager not initialized")

    async def _ensure_bucket_exists(self) -> None:
        """Ensure bucket exists, create if necessary"""
        try:
            bucket_exists = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                self._client.bucket_exists,
                self._bucket_name
            )

            if not bucket_exists:
                await asyncio.get_event_loop().run_in_executor(
                    self._executor,
                    self._client.make_bucket,
                    self._bucket_name,
                    self._region
                )
                logger.info("Created bucket: %s", self._bucket_name)

        except Exception as e:
            logger.error("Bucket creation/check failed: %s", e, exc_info=True)
            raise VoiceServiceError(f"Bucket operation failed: {e}") from e

    def _upload_sync(
        self,
        file_data: bytes,
        object_key: str,
        content_type: str,
        metadata: Dict[str, str]
    ) -> None:
        """Synchronous upload operation"""
        self._client.put_object(
            bucket_name=self._bucket_name,
            object_name=object_key,
            data=BytesIO(file_data),
            length=len(file_data),
            content_type=content_type,
            metadata=metadata
        )

    def _download_sync(self, object_key: str) -> bytes:
        """Synchronous download operation"""
        response = self._client.get_object(
            bucket_name=self._bucket_name,
            object_name=object_key
        )
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def _delete_sync(self, object_key: str) -> None:
        """Synchronous delete operation"""
        self._client.remove_object(
            bucket_name=self._bucket_name,
            object_name=object_key
        )

    def _generate_presigned_url_sync(
        self,
        object_key: str,
        expires: timedelta,
        method: str
    ) -> str:
        """Synchronous presigned URL generation"""
        if method.upper() == "GET":
            return self._client.get_presigned_url(
                "GET",
                self._bucket_name,
                object_key,
                expires=expires
            )
        if method.upper() == "PUT":
            return self._client.get_presigned_url(
                "PUT",
                self._bucket_name,
                object_key,
                expires=expires
            )
        return self._client.get_presigned_url(
            method.upper(),
            self._bucket_name,
            object_key,
            expires=expires
        )

    def _list_objects_sync(self, prefix: str, limit: int) -> List[VoiceFileInfo]:
        """Synchronous object listing"""
        objects = []
        for obj in self._client.list_objects(
            bucket_name=self._bucket_name,
            prefix=prefix,
            recursive=True
        ):
            if len(objects) >= limit:
                break

            # Get object metadata
            stat = self._client.stat_object(self._bucket_name, obj.object_name)

            objects.append(VoiceFileInfo(
                file_id=obj.object_name.split('/')[-1] if '/' in obj.object_name else obj.object_name,
                original_filename=obj.object_name.split('/')[-1] if '/' in obj.object_name else obj.object_name,
                mime_type=stat.content_type or "application/octet-stream",
                size_bytes=obj.size or 0,
                format=(stat.content_type or "unknown").split('/')[-1] if '/' in (stat.content_type or "") else "unknown",
                created_at=obj.last_modified.isoformat() if obj.last_modified else datetime.utcnow().isoformat(),
                minio_bucket=self._bucket_name,
                minio_key=obj.object_name
            ))

        return objects
