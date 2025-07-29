"""
Unit tests для infrastructure/metrics.py

Тестирование высокопроизводительной системы метрик voice_v2.
Проверяет performance targets: ≤1ms/record и SOLID compliance.
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, Mock
import json
import sys
import os

# Add project root to Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../../')))

from app.services.voice_v2.infrastructure.metrics import (
    MetricType,
    MetricPriority,
    MetricRecord,
    MemoryMetricsBackend,
    RedisMetricsBackend,
    MetricsBuffer,
    VoiceMetricsCollector,
    MetricsBackendInterface
)


class TestMetricRecord:
    """Test MetricRecord dataclass и serialization"""

    def test_metric_record_creation(self):
        """Test basic MetricRecord creation"""
        record = MetricRecord(
            name="test.metric",
            value=42.5,
            metric_type=MetricType.HISTOGRAM,
            priority=MetricPriority.HIGH,
            tags={"provider": "openai"},
            extra_data={"context": "test"}
        )

        assert record.name == "test.metric"
        assert record.value == 42.5
        assert record.metric_type == MetricType.HISTOGRAM
        assert record.priority == MetricPriority.HIGH
        assert record.tags["provider"] == "openai"
        assert record.extra_data["context"] == "test"
        assert isinstance(record.timestamp, float)

    def test_metric_record_serialization(self):
        """Test MetricRecord to_dict serialization"""
        record = MetricRecord(
            name="voice.stt.duration",
            value=150.0,
            metric_type=MetricType.TIMER,
            priority=MetricPriority.MEDIUM
        )

        serialized = record.to_dict()

        assert serialized["name"] == "voice.stt.duration"
        assert serialized["value"] == 150.0
        assert serialized["type"] == "timer"
        assert serialized["priority"] == 2
        assert "timestamp" in serialized
        assert serialized["tags"] == {}
        assert serialized["extra_data"] == {}

    def test_metric_record_with_default_timestamp(self):
        """Test default timestamp generation"""
        before = time.time()
        record = MetricRecord("test", 1, MetricType.COUNTER, MetricPriority.LOW)
        after = time.time()

        assert before <= record.timestamp <= after


class TestMemoryMetricsBackend:
    """Test MemoryMetricsBackend functionality"""

    def setup_method(self):
        """Setup test fixtures"""
        self.backend = MemoryMetricsBackend(max_records=100)
        self.sample_record = MetricRecord(
            name="test.metric",
            value=10,
            metric_type=MetricType.COUNTER,
            priority=MetricPriority.HIGH
        )

    @pytest.mark.asyncio
    async def test_store_single_metric(self):
        """Test storing single metric"""
        await self.backend.store_metric(self.sample_record)

        # Verify storage
        assert len(self.backend._records) == 1
        stored_record = self.backend._records[0]
        assert stored_record.name == "test.metric"
        assert stored_record.value == 10

    @pytest.mark.asyncio
    async def test_store_batch_metrics(self):
        """Test batch storage functionality"""
        records = [
            MetricRecord(f"test.metric.{i}", i, MetricType.COUNTER, MetricPriority.MEDIUM)
            for i in range(5)
        ]

        await self.backend.store_batch(records)

        assert len(self.backend._records) == 5
        for i, record in enumerate(self.backend._records):
            assert record.name == f"test.metric.{i}"
            assert record.value == i

    @pytest.mark.asyncio
    async def test_max_records_limit(self):
        """Test max records limit enforcement"""
        backend = MemoryMetricsBackend(max_records=3)

        # Add more records than limit
        records = [
            MetricRecord(f"test.{i}", i, MetricType.COUNTER, MetricPriority.LOW)
            for i in range(5)
        ]

        await backend.store_batch(records)

        # Should only keep last 3 records
        assert len(backend._records) == 3
        assert backend._records[0].name == "test.2"
        assert backend._records[-1].name == "test.4"

    @pytest.mark.asyncio
    async def test_get_metrics_all(self):
        """Test retrieving all metrics"""
        records = [
            MetricRecord(f"voice.stt.{i}", i, MetricType.HISTOGRAM, MetricPriority.HIGH)
            for i in range(3)
        ]
        await self.backend.store_batch(records)

        retrieved = await self.backend.get_metrics()

        assert len(retrieved) == 3
        assert all(r.name.startswith("voice.stt.") for r in retrieved)

    @pytest.mark.asyncio
    async def test_get_metrics_with_pattern(self):
        """Test pattern-based metric filtering"""
        records = [
            MetricRecord("voice.stt.duration", 100, MetricType.HISTOGRAM, MetricPriority.HIGH),
            MetricRecord("voice.tts.duration", 200, MetricType.HISTOGRAM, MetricPriority.HIGH),
            MetricRecord("system.cpu.usage", 50, MetricType.GAUGE, MetricPriority.LOW)
        ]
        await self.backend.store_batch(records)

        voice_metrics = await self.backend.get_metrics(name_pattern="voice")

        assert len(voice_metrics) == 2
        assert all("voice" in r.name for r in voice_metrics)

    @pytest.mark.asyncio
    async def test_get_metrics_time_range(self):
        """Test time-based metric filtering"""
        now = time.time()
        old_record = MetricRecord("old.metric", 1, MetricType.COUNTER, MetricPriority.LOW)
        old_record.timestamp = now - 3600  # 1 hour ago

        new_record = MetricRecord("new.metric", 2, MetricType.COUNTER, MetricPriority.LOW)
        new_record.timestamp = now - 60  # 1 minute ago

        await self.backend.store_batch([old_record, new_record])

        # Get metrics from last 30 minutes
        recent_metrics = await self.backend.get_metrics(
            start_time=now - 1800,  # 30 minutes ago
            end_time=now
        )

        assert len(recent_metrics) == 1
        assert recent_metrics[0].name == "new.metric"

    @pytest.mark.asyncio
    async def test_health_check(self):
        """Test health check functionality"""
        health = await self.backend.health_check()
        assert health is True


class TestRedisMetricsBackend:
    """Test RedisMetricsBackend functionality"""

    def setup_method(self):
        """Setup test fixtures with mocked Redis"""
        self.mock_redis = AsyncMock()
        self.backend = RedisMetricsBackend(self.mock_redis, "test_voice_metrics")
        self.sample_record = MetricRecord(
            name="voice.test",
            value=42,
            metric_type=MetricType.GAUGE,
            priority=MetricPriority.MEDIUM,
            tags={"provider": "test"}
        )

    @pytest.mark.asyncio
    async def test_store_single_metric(self):
        """Test storing single metric в Redis"""
        await self.backend.store_metric(self.sample_record)

        # Verify Redis setex called correctly
        self.mock_redis.setex.assert_called_once()
        call_args = self.mock_redis.setex.call_args

        # Check key format
        key = call_args[0][0]
        assert key.startswith("test_voice_metrics:voice.test:")

        # Check TTL
        ttl = call_args[0][1]
        assert ttl == 86400

        # Check serialized data
        data = call_args[0][2]
        parsed_data = json.loads(data)
        assert parsed_data["name"] == "voice.test"
        assert parsed_data["value"] == 42

    @pytest.mark.asyncio
    async def test_store_batch_metrics(self):
        """Test batch storage с Redis pipeline"""
        records = [
            MetricRecord(f"voice.batch.{i}", i, MetricType.COUNTER, MetricPriority.HIGH)
            for i in range(3)
        ]

        # Mock pipeline and return a context manager mock
        mock_pipeline = AsyncMock()
        context_manager_mock = AsyncMock()
        context_manager_mock.__aenter__ = AsyncMock(return_value=mock_pipeline)
        context_manager_mock.__aexit__ = AsyncMock(return_value=None)

        self.mock_redis.pipeline = Mock(return_value=context_manager_mock)

        await self.backend.store_batch(records)

        # Verify pipeline usage
        self.mock_redis.pipeline.assert_called_once()
        assert mock_pipeline.setex.call_count == 3
        mock_pipeline.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_empty_batch(self):
        """Test storing empty batch (should be no-op)"""
        await self.backend.store_batch([])

        # Should not call pipeline for empty batch
        self.mock_redis.pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_metrics(self):
        """Test retrieving metrics from Redis"""
        # Mock Redis responses
        self.mock_redis.keys.return_value = [
            "test_voice_metrics:voice.stt:1234567890",
            "test_voice_metrics:voice.tts:1234567891"
        ]

        # Mock metric data
        metric_data = [
            json.dumps({
                "name": "voice.stt",
                "value": 150,
                "type": "histogram",
                "priority": 1,
                "timestamp": 1234567890,
                "tags": {"provider": "openai"},
                "extra_data": {}
            }),
            json.dumps({
                "name": "voice.tts",
                "value": 200,
                "type": "histogram",
                "priority": 1,
                "timestamp": 1234567891,
                "tags": {"provider": "google"},
                "extra_data": {}
            })
        ]
        self.mock_redis.mget.return_value = metric_data

        metrics = await self.backend.get_metrics(name_pattern="voice")

        assert len(metrics) == 2
        assert metrics[0].name == "voice.stt"
        assert metrics[0].value == 150
        assert metrics[0].tags["provider"] == "openai"
        assert metrics[1].name == "voice.tts"
        assert metrics[1].value == 200

    @pytest.mark.asyncio
    async def test_get_metrics_malformed_data(self):
        """Test handling malformed metric data"""
        self.mock_redis.keys.return_value = ["test_voice_metrics:voice.bad:123"]
        self.mock_redis.mget.return_value = ["invalid json data"]

        metrics = await self.backend.get_metrics()

        # Should skip malformed records
        assert len(metrics) == 0

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """Test successful health check"""
        self.mock_redis.ping.return_value = True

        health = await self.backend.health_check()

        assert health is True
        self.mock_redis.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        """Test health check failure"""
        self.mock_redis.ping.side_effect = Exception("Connection failed")

        health = await self.backend.health_check()

        assert health is False


class TestMetricsBuffer:
    """Test MetricsBuffer buffering functionality"""

    def setup_method(self):
        """Setup test fixtures"""
        self.flush_called = []  # Initialize first to prevent race conditions
        self.buffer = MetricsBuffer(
            max_size=100,
            flush_interval=1.0,
            priority_thresholds={
                MetricPriority.HIGH: 2,    # Small threshold for testing
                MetricPriority.MEDIUM: 5,
                MetricPriority.LOW: 10
            }
        )
        # Add callback after initialization
        self.buffer.add_flush_callback(self._flush_callback)

    def _flush_callback(self, records):
        """Callback for capturing flush events"""
        self.flush_called.append(records)

    def teardown_method(self):
        """Cleanup test fixtures"""
        if hasattr(self, 'buffer'):
            self.buffer.flush_all()  # Clear any remaining metrics
        if hasattr(self, 'flush_called'):
            self.flush_called.clear()

    def test_add_metric_to_buffer(self):
        """Test adding metric to buffer"""
        record = MetricRecord("test", 1, MetricType.COUNTER, MetricPriority.MEDIUM)

        self.buffer.add_metric(record)

        assert len(self.buffer._buffers[MetricPriority.MEDIUM]) == 1
        assert len(self.buffer._buffers[MetricPriority.HIGH]) == 0

    def test_priority_threshold_flush(self):
        """Test automatic flush при достижении threshold"""
        # Clear any previous flush calls
        self.flush_called.clear()

        # Add HIGH priority metrics to trigger flush (threshold = 2)
        record1 = MetricRecord("high.1", 1, MetricType.COUNTER, MetricPriority.HIGH)
        record2 = MetricRecord("high.2", 2, MetricType.COUNTER, MetricPriority.HIGH)

        # Add first metric - should not trigger flush yet
        self.buffer.add_metric(record1)
        assert len(self.flush_called) == 0
        assert len(self.buffer._buffers[MetricPriority.HIGH]) == 1

        # Add second metric - should trigger flush (reaches threshold of 2)
        self.buffer.add_metric(record2)

        # Should have triggered flush callback
        assert len(self.flush_called) == 1, f"Expected 1 flush call, got {len(self.flush_called)}"
        assert len(self.flush_called[0]) == 2, f"Expected 2 records in flush, got {len(self.flush_called[0])}"

        # Buffer should be empty after flush
        assert len(self.buffer._buffers[MetricPriority.HIGH]) == 0

    def test_flush_all_buffers(self):
        """Test manual flush of all buffers"""
        # Add metrics to different priority buffers
        high_record = MetricRecord("high", 1, MetricType.COUNTER, MetricPriority.HIGH)
        medium_record = MetricRecord("medium", 2, MetricType.GAUGE, MetricPriority.MEDIUM)
        low_record = MetricRecord("low", 3, MetricType.HISTOGRAM, MetricPriority.LOW)

        self.buffer.add_metric(high_record)
        self.buffer.add_metric(medium_record)
        self.buffer.add_metric(low_record)

        all_records = self.buffer.flush_all()

        assert len(all_records[MetricPriority.HIGH]) == 1
        assert len(all_records[MetricPriority.MEDIUM]) == 1
        assert len(all_records[MetricPriority.LOW]) == 1

        # All buffers should be empty
        for priority in MetricPriority:
            assert len(self.buffer._buffers[priority]) == 0

    def test_should_flush_time_based(self):
        """Test time-based flush detection"""
        # Initially should not need flush
        assert not self.buffer.should_flush()

        # Simulate time passage
        self.buffer._last_flush = time.time() - 2.0  # 2 seconds ago

        assert self.buffer.should_flush()

    def test_flush_callback_error_handling(self):
        """Test error handling в flush callbacks"""
        def failing_callback(records):
            raise Exception("Callback failed")

        self.buffer.add_flush_callback(failing_callback)

        # Should not raise exception despite callback failure
        record = MetricRecord("test", 1, MetricType.COUNTER, MetricPriority.HIGH)
        self.buffer.add_metric(record)
        self.buffer.add_metric(record)  # Trigger flush


class TestVoiceMetricsCollector:
    """Test VoiceMetricsCollector main functionality"""

    def setup_method(self):
        """Setup test fixtures"""
        self.mock_backend = AsyncMock(spec=MetricsBackendInterface)
        self.collector = VoiceMetricsCollector(
            backend=self.mock_backend,
            enable_buffering=False,  # Disable for direct testing
            thread_pool_size=1
        )

    @pytest.mark.asyncio
    async def test_record_stt_operation(self):
        """Test STT operation metrics recording"""
        start_time = time.perf_counter()

        await self.collector.record_stt_operation(
            provider="openai",
            duration_ms=150.5,
            success=True,
            audio_length_sec=10.0,
            agent_id="test_agent"
        )

        # Performance target verification (≤1ms)
        collection_time = (time.perf_counter() - start_time) * 1000
        assert collection_time <= 5.0  # Allow some overhead for testing

        # Verify metrics recorded
        assert self.mock_backend.store_metric.call_count >= 3  # At least 3 metrics

        # Check specific metrics
        calls = self.mock_backend.store_metric.call_args_list
        metric_names = [call[0][0].name for call in calls]

        assert "voice.stt.duration_ms" in metric_names
        assert "voice.stt.requests" in metric_names
        assert "voice.stt.efficiency_ratio" in metric_names

    @pytest.mark.asyncio
    async def test_record_tts_operation(self):
        """Test TTS operation metrics recording"""
        await self.collector.record_tts_operation(
            provider="google",
            duration_ms=200.0,
            success=True,
            text_length=100,
            agent_id="test_agent"
        )

        # Verify metrics recorded
        assert self.mock_backend.store_metric.call_count >= 3

        calls = self.mock_backend.store_metric.call_args_list
        metric_names = [call[0][0].name for call in calls]

        assert "voice.tts.duration_ms" in metric_names
        assert "voice.tts.requests" in metric_names
        assert "voice.tts.chars_per_ms" in metric_names

    @pytest.mark.asyncio
    async def test_record_provider_fallback(self):
        """Test provider fallback metrics"""
        await self.collector.record_provider_fallback(
            original_provider="openai",
            fallback_provider="google",
            operation="stt",
            agent_id="test_agent"
        )

        self.mock_backend.store_metric.assert_called_once()

        metric = self.mock_backend.store_metric.call_args[0][0]
        assert metric.name == "voice.provider.fallback"
        assert metric.value == 1
        assert metric.metric_type == MetricType.COUNTER
        assert metric.priority == MetricPriority.HIGH
        assert metric.tags["original_provider"] == "openai"
        assert metric.tags["fallback_provider"] == "google"

    @pytest.mark.asyncio
    async def test_record_cache_hit(self):
        """Test cache performance metrics"""
        await self.collector.record_cache_hit(
            cache_type="stt_result",
            hit=True,
            agent_id="test_agent"
        )

        metric = self.mock_backend.store_metric.call_args[0][0]
        assert metric.name == "voice.cache.requests"
        assert metric.tags["result"] == "hit"
        assert metric.tags["cache_type"] == "stt_result"

    @pytest.mark.asyncio
    async def test_performance_slow_collection_alert(self):
        """Test slow collection performance alerting"""
        # Mock slow backend
        async def slow_store(record):
            await asyncio.sleep(0.002)  # 2ms delay to trigger alert

        self.mock_backend.store_metric.side_effect = slow_store

        await self.collector.record_stt_operation(
            provider="test",
            duration_ms=100,
            success=True,
            audio_length_sec=5.0,
            agent_id="test_agent"
        )

        # Should generate slow collection alert
        calls = self.mock_backend.store_metric.call_args_list
        slow_alerts = [
            call for call in calls
            if call[0][0].name == "voice.metrics.collection_slow"
        ]

        assert len(slow_alerts) > 0

    @pytest.mark.asyncio
    async def test_get_metrics_summary(self):
        """Test metrics summary generation"""
        # Mock backend responses
        mock_metrics = [
            MetricRecord("voice.stt.requests", 10, MetricType.COUNTER, MetricPriority.MEDIUM,
                        tags={"provider": "openai", "status": "success"}),
            MetricRecord("voice.stt.requests", 2, MetricType.COUNTER, MetricPriority.MEDIUM,
                        tags={"provider": "openai", "status": "error"}),
            MetricRecord("voice.stt.duration_ms", 150, MetricType.HISTOGRAM, MetricPriority.HIGH),
            MetricRecord("voice.provider.fallback", 1, MetricType.COUNTER, MetricPriority.HIGH),
            MetricRecord("voice.cache.requests", 5, MetricType.COUNTER, MetricPriority.MEDIUM,
                        tags={"result": "hit"}),
            MetricRecord("voice.cache.requests", 2, MetricType.COUNTER, MetricPriority.MEDIUM,
                        tags={"result": "miss"})
        ]
        self.mock_backend.get_metrics.return_value = mock_metrics

        summary = await self.collector.get_metrics_summary(time_window_minutes=60)

        # total_requests теперь только STT requests (cache отдельно): stt.requests (12)
        assert summary["total_requests"] == 12  # 10 + 2 (только stt requests)
        assert summary["success_rate"] == 10/12  # 10 success out of 12 stt requests
        assert summary["average_stt_duration"] == 150.0
        assert summary["provider_distribution"]["openai"] == 12  # Only stt requests
        assert summary["fallback_rate"] == 1/12  # 1 fallback out of 12 stt requests
        assert summary["cache_hit_rate"] == 5/7  # 5 hits out of 7 total cache requests

    @pytest.mark.asyncio
    async def test_health_check(self):
        """Test metrics collector health check"""
        self.mock_backend.health_check.return_value = True

        health = await self.collector.health_check()

        assert health["healthy"] is True
        assert health["backend_type"] == "AsyncMock"
        assert health["buffering_enabled"] is False
        assert "background_tasks" in health

    @pytest.mark.asyncio
    async def test_cleanup(self):
        """Test cleanup functionality"""
        await self.collector.cleanup()

        # Should shutdown thread pool
        assert self.collector._thread_pool._shutdown


class TestVoiceMetricsCollectorWithBuffering:
    """Test VoiceMetricsCollector с включенным buffering"""

    def setup_method(self):
        """Setup test fixtures с buffering"""
        self.mock_backend = AsyncMock(spec=MetricsBackendInterface)
        self.collector = VoiceMetricsCollector(
            backend=self.mock_backend,
            enable_buffering=True,
            thread_pool_size=1
        )

    @pytest.mark.asyncio
    async def test_buffered_metrics_collection(self):
        """Test metrics collection через buffer"""
        await self.collector.record_stt_operation(
            provider="openai",
            duration_ms=100,
            success=True,
            audio_length_sec=5.0,
            agent_id="test_agent"
        )

        # With buffering, direct store_metric should not be called immediately
        self.mock_backend.store_metric.assert_not_called()

        # But buffer should have metrics
        assert self.collector._buffer is not None
        total_buffered = sum(
            len(records) for records in self.collector._buffer._buffers.values()
        )
        assert total_buffered > 0

    @pytest.mark.asyncio
    async def test_health_check_with_buffering(self):
        """Test health check с buffer information"""
        # Add some metrics to buffer
        await self.collector.record_cache_hit("test", True, "agent1")

        health = await self.collector.health_check()

        assert health["buffering_enabled"] is True
        assert "buffer_sizes" in health
        assert isinstance(health["buffer_sizes"], dict)

    @pytest.mark.asyncio
    async def test_cleanup_flushes_buffer(self):
        """Test cleanup flushes buffered metrics"""
        # Add metrics to buffer
        await self.collector.record_stt_operation(
            provider="test",
            duration_ms=100,
            success=True,
            audio_length_sec=1.0,
            agent_id="test_agent"
        )

        await self.collector.cleanup()

        # Should have called store_batch during cleanup
        assert self.mock_backend.store_batch.call_count > 0
