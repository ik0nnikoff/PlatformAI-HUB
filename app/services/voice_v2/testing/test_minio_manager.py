"""
Tests for MinioFileManager - voice_v2 infrastructure
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from ..infrastructure.minio_manager import MinioFileManager
from ..core.exceptions import VoiceServiceError
from ..core.schemas import VoiceFileInfo, AudioFormat


@pytest.fixture
def minio_config():
    """MinIO configuration for testing"""
    return {
        "endpoint": "localhost:9000",
        "access_key": "test_access_key",
        "secret_key": "test_secret_key",
        "bucket_name": "test-voice-bucket",
        "secure": False,
        "region": "us-east-1"
    }


@pytest.fixture
def mock_minio_client():
    """Mock MinIO client"""
    client = MagicMock()
    client.bucket_exists.return_value = True
    client.put_object.return_value = None
    client.get_object.return_value = MagicMock()
    client.remove_object.return_value = None
    client.stat_object.return_value = MagicMock()
    client.presigned_url.return_value = "https://test-url.com"
    client.list_objects.return_value = []
    return client


@pytest_asyncio.fixture
async def minio_manager(minio_config):
    """MinioFileManager instance with mocked bucket operations"""
    manager = MinioFileManager(**minio_config)
    
    # Mock the problematic _ensure_bucket_exists method
    with patch.object(manager, '_ensure_bucket_exists', new_callable=AsyncMock):
        await manager.initialize()
        
        # Mock all client operations for this manager
        manager._client = MagicMock()
        manager._client.bucket_exists.return_value = True
        manager._client.put_object.return_value = None
        manager._client.get_object.return_value = MagicMock()
        manager._client.remove_object.return_value = None
        manager._client.stat_object.return_value = MagicMock()
        manager._client.presigned_url.return_value = "https://test-url.com"
        manager._client.list_objects.return_value = []
        
        yield manager
        
        await manager.cleanup()
class TestMinioFileManagerInitialization:
    """Test MinioFileManager initialization and cleanup"""
    
    @pytest.mark.asyncio
    async def test_initialization_success(self, minio_config):
        """Test successful initialization"""
        manager = MinioFileManager(**minio_config)
        
        # Mock the problematic method instead of the entire client
        with patch.object(manager, '_ensure_bucket_exists', new_callable=AsyncMock) as mock_ensure:
            await manager.initialize()
            
            assert manager._initialized is True
            assert manager._client is not None
            assert manager._executor is not None
            mock_ensure.assert_called_once()
            
            await manager.cleanup()

    @pytest.mark.asyncio
    async def test_initialization_failure(self, minio_config):
        """Test initialization failure handling"""
        manager = MinioFileManager(**minio_config)
        
        # Mock _ensure_bucket_exists to raise an exception
        with patch.object(manager, '_ensure_bucket_exists', new_callable=AsyncMock, side_effect=Exception("Bucket error")):
            with pytest.raises(VoiceServiceError, match="MinIO initialization failed"):
                await manager.initialize()

    @pytest.mark.asyncio
    async def test_cleanup(self, minio_config):
        """Test cleanup process"""
        manager = MinioFileManager(**minio_config)
        
        with patch.object(manager, '_ensure_bucket_exists', new_callable=AsyncMock):
            await manager.initialize()
            
            assert manager._initialized is True
            await manager.cleanup()
            
            assert manager._initialized is False
            assert manager._executor is None


class TestFileOperations:
    """Test file upload, download, and delete operations"""
    
    @pytest.mark.asyncio
    async def test_upload_file_success(self, minio_manager):
        """Test successful file upload"""
        file_data = b"test audio data"
        object_key = "test/audio/file.wav"
        content_type = "audio/wav"
        metadata = {"agent_id": "test_agent", "user_id": "test_user"}
        
        result = await minio_manager.upload_file(
            file_data=file_data,
            object_key=object_key,
            content_type=content_type,
            metadata=metadata
        )
        
        assert isinstance(result, VoiceFileInfo)
        assert result.object_key == object_key
        assert result.bucket_name == minio_manager.bucket_name
        assert result.file_size == len(file_data)
        assert result.content_type == content_type
        assert "upload-time" in result.metadata

    @pytest.mark.asyncio
    async def test_upload_file_not_initialized(self, minio_config):
        """Test upload with uninitialized manager"""
        manager = MinioFileManager(**minio_config)
        
        with pytest.raises(VoiceServiceError, match="MinioFileManager not initialized"):
            await manager.upload_file(b"test", "test.wav")

    @pytest.mark.asyncio
    async def test_download_file_success(self, minio_manager):
        """Test successful file download"""
        object_key = "test/audio/file.wav"
        expected_data = b"test audio content"
        
        # Mock the response
        response_mock = MagicMock()
        response_mock.read.return_value = expected_data
        minio_manager._client.get_object.return_value = response_mock
        
        result = await minio_manager.download_file(object_key)
        
        assert result == expected_data
        response_mock.close.assert_called_once()
        response_mock.release_conn.assert_called_once()

    @pytest.mark.asyncio
    async def test_download_file_failure(self, minio_manager):
        """Test download failure handling"""
        object_key = "nonexistent/file.wav"
        
        minio_manager._client.get_object.side_effect = Exception("File not found")
        
        with pytest.raises(VoiceServiceError, match="File download failed"):
            await minio_manager.download_file(object_key)

    @pytest.mark.asyncio
    async def test_delete_file_success(self, minio_manager):
        """Test successful file deletion"""
        object_key = "test/audio/file.wav"
        
        result = await minio_manager.delete_file(object_key)
        
        assert result is True
        minio_manager._client.remove_object.assert_called_once_with(
            bucket_name=minio_manager.bucket_name,
            object_name=object_key
        )

    @pytest.mark.asyncio
    async def test_delete_file_failure(self, minio_manager):
        """Test delete failure handling"""
        object_key = "test/audio/file.wav"
        
        minio_manager._client.remove_object.side_effect = Exception("Delete failed")
        
        with pytest.raises(VoiceServiceError, match="File deletion failed"):
            await minio_manager.delete_file(object_key)


class TestPresignedUrls:
    """Test presigned URL generation"""
    
    @pytest.mark.asyncio
    async def test_generate_presigned_url_success(self, minio_manager):
        """Test successful presigned URL generation"""
        object_key = "test/audio/file.wav"
        expected_url = "https://test-presigned-url.com"
        
        minio_manager._client.presigned_url.return_value = expected_url
        
        result = await minio_manager.generate_presigned_url(
            object_key=object_key,
            expires_hours=2,
            method="GET"
        )
        
        assert result == expected_url
        minio_manager._client.presigned_url.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_presigned_url_failure(self, minio_manager):
        """Test presigned URL generation failure"""
        object_key = "test/audio/file.wav"
        
        minio_manager._client.presigned_url.side_effect = Exception("URL generation failed")
        
        with pytest.raises(VoiceServiceError, match="Presigned URL generation failed"):
            await minio_manager.generate_presigned_url(object_key)


class TestFileUtilities:
    """Test file utility functions"""
    
    @pytest.mark.asyncio
    async def test_file_exists_true(self, minio_manager):
        """Test file existence check - file exists"""
        object_key = "test/audio/file.wav"
        
        # Mock successful stat_object call
        minio_manager._client.stat_object.return_value = MagicMock()
        
        result = await minio_manager.file_exists(object_key)
        
        assert result is True

    @pytest.mark.asyncio
    async def test_file_exists_false(self, minio_manager):
        """Test file existence check - file doesn't exist"""
        object_key = "nonexistent/file.wav"
        
        from minio.error import S3Error
        from unittest.mock import MagicMock
        
        # Create a mock response for S3Error
        mock_response = MagicMock()
        mock_response.status = 404
        
        s3_error = S3Error(
            code="NoSuchKey",
            message="The specified key does not exist",
            resource="test-bucket",
            request_id="test-request-id",
            host_id="test-host-id",
            response=mock_response,
            bucket_name="test-bucket",
            object_name="nonexistent/file.wav"
        )
        minio_manager._client.stat_object.side_effect = s3_error
        
        result = await minio_manager.file_exists(object_key)
        
        assert result is False

    @pytest.mark.asyncio
    async def test_list_files_success(self, minio_manager):
        """Test successful file listing"""
        prefix = "test/audio/"
        
        # Mock list_objects response
        mock_object = MagicMock()
        mock_object.object_name = "test/audio/file1.wav"
        mock_object.size = 1024
        mock_object.last_modified = datetime.utcnow()
        
        minio_manager._client.list_objects.return_value = [mock_object]
        
        # Mock stat_object for metadata
        mock_stat = MagicMock()
        mock_stat.content_type = "audio/wav"
        mock_stat.metadata = {"agent_id": "test"}
        minio_manager._client.stat_object.return_value = mock_stat
        
        result = await minio_manager.list_files(prefix=prefix, limit=10)
        
        assert len(result) == 1
        assert isinstance(result[0], VoiceFileInfo)
        assert result[0].object_key == "test/audio/file1.wav"

    def test_generate_object_key(self, minio_manager):
        """Test object key generation"""
        agent_id = "agent123"
        user_id = "user456"
        file_format = AudioFormat.WAV
        
        key = minio_manager.generate_object_key(
            agent_id=agent_id,
            user_id=user_id,
            file_format=file_format
        )
        
        assert agent_id in key
        assert user_id in key
        assert key.endswith(".wav")
        assert key.startswith("voice/")

    def test_generate_object_key_no_format(self, minio_manager):
        """Test object key generation without format"""
        agent_id = "agent123"
        user_id = "user456"
        
        key = minio_manager.generate_object_key(
            agent_id=agent_id,
            user_id=user_id
        )
        
        assert agent_id in key
        assert user_id in key
        assert "." not in key.split("/")[-1]  # No extension


@pytest.mark.asyncio
async def test_minio_manager_integration():
    """Integration test for MinioFileManager"""
    config = {
        "endpoint": "localhost:9000",
        "access_key": "test_key",
        "secret_key": "test_secret",
        "bucket_name": "test-bucket"
    }
    
    manager = MinioFileManager(**config)
    
    # Test without initialization
    with pytest.raises(VoiceServiceError):
        await manager.upload_file(b"test", "test.wav")
    
    # Mock successful initialization using the same approach as other tests
    with patch.object(manager, '_ensure_bucket_exists', new_callable=AsyncMock):
        await manager.initialize()
        
        # Test that operations work after initialization
        assert manager._initialized is True
        
        await manager.cleanup()
        assert manager._initialized is False
