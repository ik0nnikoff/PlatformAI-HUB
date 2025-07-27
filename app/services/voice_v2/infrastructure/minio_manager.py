"""
Voice v2 MinIO Manager - Performance-optimized file storage
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Union, Tuple
from io import BytesIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from minio import Minio
from minio.error import S3Error
from urllib3.exceptions import MaxRetryError

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
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket_name = bucket_name
        self.secure = secure
        self.region = region
        self.max_pool_size = max_pool_size
        
        self._client: Optional[Minio] = None
        self._initialized = False
        self._executor = None

    async def initialize(self) -> None:
        """Initialize MinIO client with connection pooling"""
        try:
            # Create thread pool executor for blocking I/O
            self._executor = ThreadPoolExecutor(
                max_workers=self.max_pool_size,
                thread_name_prefix="minio-"
            )
            
            # Create MinIO client
            self._client = Minio(
                endpoint=self.endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=self.secure,
                region=self.region
            )
            
            # Ensure bucket exists
            await self._ensure_bucket_exists()
            
            self._initialized = True
            logger.info(f"MinioFileManager initialized - bucket: {self.bucket_name}")
            
        except Exception as e:
            logger.error(f"Failed to initialize MinioFileManager: {e}", exc_info=True)
            raise VoiceServiceError(f"MinIO initialization failed: {e}")

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
            file_data: Binary file data
            object_key: Unique object key for the file
            content_type: MIME content type
            metadata: Additional file metadata
            
        Returns:
            VoiceFileInfo with upload details
            
        Raises:
            VoiceServiceError: If upload fails
        """
        await self._ensure_initialized()
        
        try:
            # Prepare metadata
            upload_metadata = metadata or {}
            upload_metadata.update({
                "upload-time": datetime.utcnow().isoformat(),
                "content-length": str(len(file_data))
            })
            
            # Perform async upload
            result = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                self._upload_sync,
                file_data,
                object_key,
                content_type,
                upload_metadata
            )
            
            logger.debug(f"File uploaded: {object_key} ({len(file_data)} bytes)")
            
            return VoiceFileInfo(
                object_key=object_key,
                bucket_name=self.bucket_name,
                file_size=len(file_data),
                content_type=content_type,
                upload_time=datetime.utcnow(),
                metadata=upload_metadata
            )
            
        except Exception as e:
            logger.error(f"File upload failed for {object_key}: {e}", exc_info=True)
            raise VoiceServiceError(f"File upload failed: {e}")

    async def download_file(self, object_key: str) -> bytes:
        """
        Download file from MinIO storage
        
        Args:
            object_key: Object key to download
            
        Returns:
            File binary data
            
        Raises:
            VoiceServiceError: If download fails
        """
        await self._ensure_initialized()
        
        try:
            data = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                self._download_sync,
                object_key
            )
            
            logger.debug(f"File downloaded: {object_key} ({len(data)} bytes)")
            return data
            
        except Exception as e:
            logger.error(f"File download failed for {object_key}: {e}", exc_info=True)
            raise VoiceServiceError(f"File download failed: {e}")

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
            
            logger.debug(f"File deleted: {object_key}")
            return True
            
        except Exception as e:
            logger.error(f"File deletion failed for {object_key}: {e}", exc_info=True)
            raise VoiceServiceError(f"File deletion failed: {e}")

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
            
            logger.debug(f"Presigned URL generated: {object_key} (expires in {expires_hours}h)")
            return url
            
        except Exception as e:
            logger.error(f"Presigned URL generation failed for {object_key}: {e}", exc_info=True)
            raise VoiceServiceError(f"Presigned URL generation failed: {e}")

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
            
            logger.debug(f"Listed {len(objects)} files with prefix: '{prefix}'")
            return objects
            
        except Exception as e:
            logger.error(f"File listing failed with prefix '{prefix}': {e}", exc_info=True)
            raise VoiceServiceError(f"File listing failed: {e}")

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
                self.bucket_name,
                object_key
            )
            return True
            
        except S3Error as e:
            if e.code == "NoSuchKey":
                return False
            raise VoiceServiceError(f"File existence check failed: {e}")
        except Exception as e:
            logger.error(f"File existence check failed for {object_key}: {e}", exc_info=True)
            raise VoiceServiceError(f"File existence check failed: {e}")

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
                self.bucket_name
            )
            
            if not bucket_exists:
                await asyncio.get_event_loop().run_in_executor(
                    self._executor,
                    self._client.make_bucket,
                    self.bucket_name,
                    self.region
                )
                logger.info(f"Created bucket: {self.bucket_name}")
                
        except Exception as e:
            logger.error(f"Bucket creation/check failed: {e}", exc_info=True)
            raise VoiceServiceError(f"Bucket operation failed: {e}")

    def _upload_sync(
        self,
        file_data: bytes,
        object_key: str,
        content_type: str,
        metadata: Dict[str, str]
    ) -> None:
        """Synchronous upload operation"""
        self._client.put_object(
            bucket_name=self.bucket_name,
            object_name=object_key,
            data=BytesIO(file_data),
            length=len(file_data),
            content_type=content_type,
            metadata=metadata
        )

    def _download_sync(self, object_key: str) -> bytes:
        """Synchronous download operation"""
        response = self._client.get_object(
            bucket_name=self.bucket_name,
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
            bucket_name=self.bucket_name,
            object_name=object_key
        )

    def _generate_presigned_url_sync(
        self,
        object_key: str,
        expires: timedelta,
        method: str
    ) -> str:
        """Synchronous presigned URL generation"""
        return self._client.presigned_url(
            method=method,
            bucket_name=self.bucket_name,
            object_name=object_key,
            expires=expires
        )

    def _list_objects_sync(self, prefix: str, limit: int) -> List[VoiceFileInfo]:
        """Synchronous object listing"""
        objects = []
        for obj in self._client.list_objects(
            bucket_name=self.bucket_name,
            prefix=prefix,
            recursive=True
        ):
            if len(objects) >= limit:
                break
                
            # Get object metadata
            stat = self._client.stat_object(self.bucket_name, obj.object_name)
            
            objects.append(VoiceFileInfo(
                object_key=obj.object_name,
                bucket_name=self.bucket_name,
                file_size=obj.size,
                content_type=stat.content_type or "application/octet-stream",
                upload_time=obj.last_modified,
                metadata=stat.metadata or {}
            ))
            
        return objects
