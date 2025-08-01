# Phase 1.3.3 - Testing Strategy Planning voice_v2

## 📊 Общий обзор

**Фаза**: 1.3.3  
**Дата выполнения**: 2024-12-31  
**Статус**: ✅ ЗАВЕРШЕНА  

## 🎯 Цели этапа

1. Разработка comprehensive testing strategy для voice_v2
2. Планирование unit testing approach с 100% покрытием
3. Проектирование LangGraph integration testing framework
4. Определение performance testing methodology

## 🧪 Testing Strategy Overview

### Testing Pyramid для Voice_v2

```
                 🔺
               /     \
              /   E2E   \    ← Integration Tests (20%)
             /           \     - LangGraph workflows
            /_____________\    - Provider chains
           /               \   - Performance benchmarks
          /  Integration     \ 
         /                   \
        /___________________\ 
       /                     \
      /    Unit Tests         \  ← Unit Tests (70%)
     /                         \   - Component isolation
    /_________________________\    - Mock dependencies
                                    - Business logic

   /           Мocks            \  ← Test Infrastructure (10%)
  /___________________________\    - Provider mocks
                                    - Redis/MinIO mocks
                                    - Performance fixtures
```

## 🔬 Unit Testing Strategy

### 1. Component Testing Structure

```python
# tests/unit/voice_v2/
├── core/
│   ├── test_voice_orchestrator.py      # Orchestrator unit tests
│   ├── test_provider_manager.py        # Provider management
│   ├── test_cache_manager.py           # Caching logic
│   └── test_metrics_collector.py       # Metrics collection
├── providers/
│   ├── test_openai_provider.py         # OpenAI provider
│   ├── test_google_provider.py         # Google provider  
│   ├── test_yandex_provider.py         # Yandex provider
│   └── test_provider_base.py           # Base provider logic
├── infrastructure/
│   ├── test_redis_cache.py             # Redis operations
│   ├── test_file_manager.py            # MinIO operations
│   └── test_performance_metrics.py     # Metrics infrastructure
└── integration/
    ├── test_langgraph_tools.py         # LangGraph tools
    ├── test_voice_workflow.py          # Workflow components
    └── test_api_interface.py           # API interface layer
```

### 2. Unit Test Examples

#### Voice Orchestrator Testing

```python
# tests/unit/voice_v2/core/test_voice_orchestrator.py
import pytest
from unittest.mock import AsyncMock, Mock, patch
from app.services.voice_v2.core.voice_orchestrator import VoiceOrchestrator
from app.services.voice_v2.core.models import TranscriptionResult, SynthesisResult

class TestVoiceOrchestrator:
    """Comprehensive unit tests для VoiceOrchestrator"""
    
    @pytest.fixture
    async def orchestrator(self):
        """Create orchestrator с mocked dependencies"""
        with patch.multiple(
            'app.services.voice_v2.core.voice_orchestrator',
            RedisCacheManager=AsyncMock(),
            PerformanceMetricsCollector=AsyncMock(),
            MinIOFileManager=AsyncMock(),
            OpenAIVoiceProvider=AsyncMock(),
            GoogleVoiceProvider=AsyncMock(),
            YandexVoiceProvider=AsyncMock()
        ):
            orchestrator = VoiceOrchestrator()
            await orchestrator.initialize()
            return orchestrator
    
    @pytest.mark.asyncio
    async def test_transcribe_audio_success(self, orchestrator):
        """Test successful audio transcription"""
        # Arrange
        audio_data = b"fake_audio_data"
        expected_result = TranscriptionResult(
            text="Hello world",
            confidence=0.95,
            detected_language="en",
            provider="openai",
            duration_ms=1250
        )
        
        # Mock provider response
        orchestrator._stt_providers[0].transcribe_audio.return_value = expected_result
        
        # Act
        result = await orchestrator.transcribe_audio(audio_data, language="en")
        
        # Assert
        assert result.text == "Hello world"
        assert result.confidence == 0.95
        assert result.provider == "openai"
        orchestrator._stt_providers[0].transcribe_audio.assert_called_once_with(
            audio_data, language="en"
        )
    
    @pytest.mark.asyncio
    async def test_transcribe_audio_provider_fallback(self, orchestrator):
        """Test provider fallback при первом провайдере failure"""
        # Arrange
        audio_data = b"fake_audio_data"
        
        # First provider fails
        orchestrator._stt_providers[0].transcribe_audio.side_effect = Exception("Provider error")
        
        # Second provider succeeds
        expected_result = TranscriptionResult(
            text="Hello world",
            confidence=0.90,
            detected_language="en", 
            provider="google",
            duration_ms=1500
        )
        orchestrator._stt_providers[1].transcribe_audio.return_value = expected_result
        
        # Act
        result = await orchestrator.transcribe_audio(audio_data)
        
        # Assert
        assert result.provider == "google"
        assert orchestrator._stt_providers[0].transcribe_audio.call_count == 1
        assert orchestrator._stt_providers[1].transcribe_audio.call_count == 1
    
    @pytest.mark.asyncio 
    async def test_synthesize_speech_with_caching(self, orchestrator):
        """Test speech synthesis с caching logic"""
        # Arrange
        text = "Hello world"
        cache_key = "tts_cache:hash123:en:natural"
        
        # Mock cache miss, then hit
        orchestrator.cache_manager.get.side_effect = [None, "cached_audio_data"]
        
        synthesis_result = SynthesisResult(
            audio_data=b"synthesized_audio",
            format="mp3",
            duration=2.5,
            provider="openai"
        )
        orchestrator._tts_providers[0].synthesize_speech.return_value = synthesis_result
        
        # Act - First call (cache miss)
        result1 = await orchestrator.synthesize_speech(text, language="en")
        
        # Act - Second call (cache hit)  
        result2 = await orchestrator.synthesize_speech(text, language="en")
        
        # Assert
        assert result1.provider == "openai"
        assert orchestrator.cache_manager.set.call_count == 1  # Cached result
        assert orchestrator._tts_providers[0].synthesize_speech.call_count == 1  # Only called once
```

#### Provider Testing

```python
# tests/unit/voice_v2/providers/test_openai_provider.py
import pytest
from unittest.mock import AsyncMock, patch
import aiohttp
from app.services.voice_v2.providers.openai_provider import OpenAIVoiceProvider

class TestOpenAIVoiceProvider:
    """Unit tests для OpenAI voice provider"""
    
    @pytest.fixture
    def provider(self):
        """Create OpenAI provider с mock configuration"""
        config = {
            "api_key": "test_api_key",
            "base_url": "https://api.openai.com/v1", 
            "timeout": 30,
            "max_retries": 3
        }
        return OpenAIVoiceProvider(config)
    
    @pytest.mark.asyncio
    async def test_transcribe_audio_success(self, provider):
        """Test successful STT request"""
        # Arrange
        audio_data = b"fake_audio_data"
        mock_response = {
            "text": "Hello world",
            "language": "en"
        }
        
        with patch.object(provider, '_make_stt_request') as mock_request:
            mock_request.return_value = mock_response
            
            # Act
            result = await provider.transcribe_audio(audio_data, language="en")
            
            # Assert
            assert result.text == "Hello world"
            assert result.detected_language == "en"
            assert result.provider == "openai"
            assert result.confidence > 0
    
    @pytest.mark.asyncio
    async def test_synthesize_speech_success(self, provider):
        """Test successful TTS request"""
        # Arrange  
        text = "Hello world"
        mock_audio_data = b"fake_synthesized_audio"
        
        with patch.object(provider, '_make_tts_request') as mock_request:
            mock_request.return_value = {
                "audio": mock_audio_data,
                "format": "mp3"
            }
            
            # Act
            result = await provider.synthesize_speech(
                text, 
                language="en",
                voice_style="natural"
            )
            
            # Assert
            assert result.audio_data == mock_audio_data
            assert result.format == "mp3"
            assert result.provider == "openai"
    
    @pytest.mark.asyncio
    async def test_rate_limiting_handling(self, provider):
        """Test rate limiting error handling"""
        # Arrange
        with patch.object(provider, '_make_stt_request') as mock_request:
            mock_request.side_effect = aiohttp.ClientResponseError(
                request_info=Mock(),
                history=(),
                status=429,  # Rate limit
                message="Rate limit exceeded"
            )
            
            # Act & Assert
            with pytest.raises(aiohttp.ClientResponseError):
                await provider.transcribe_audio(b"audio_data")
    
    @pytest.mark.asyncio
    async def test_connection_pooling(self, provider):
        """Test HTTP connection pooling"""
        # Arrange
        await provider.initialize()
        
        # Act
        session = await provider._get_session()
        session2 = await provider._get_session()
        
        # Assert
        assert session is session2  # Same session instance
        assert isinstance(session, aiohttp.ClientSession)
        assert session.connector.limit == 100  # Pool limit
```

### 3. Performance Unit Testing

```python
# tests/unit/voice_v2/performance/test_performance_targets.py
import pytest
import time
import asyncio
from app.services.voice_v2.core.voice_orchestrator import VoiceOrchestrator

class TestPerformanceTargets:
    """Unit tests для performance requirements"""
    
    @pytest.mark.asyncio
    async def test_redis_operation_latency(self):
        """Test Redis операции выполняются в ≤200µs"""
        # Arrange
        cache_manager = RedisCacheManager()
        await cache_manager.initialize()
        
        # Act & Measure
        start_time = time.perf_counter()
        await cache_manager.set("test_key", "test_value")
        end_time = time.perf_counter()
        
        latency_us = (end_time - start_time) * 1_000_000
        
        # Assert
        assert latency_us <= 200, f"Redis latency {latency_us}µs exceeds 200µs target"
    
    @pytest.mark.asyncio
    async def test_intent_detection_performance(self):
        """Test intent detection выполняется в ≤8µs"""
        # Arrange
        intent_detector = VoiceIntentDetector()
        test_message = "Can you read this message aloud please?"
        
        # Act & Measure
        start_time = time.perf_counter()
        intent = await intent_detector.detect_voice_intent(test_message)
        end_time = time.perf_counter()
        
        latency_us = (end_time - start_time) * 1_000_000
        
        # Assert
        assert latency_us <= 8, f"Intent detection {latency_us}µs exceeds 8µs target"
        assert intent.needs_voice_response is True
    
    @pytest.mark.asyncio
    async def test_orchestrator_initialization_time(self):
        """Test orchestrator initialization в ≤5ms"""
        # Act & Measure
        start_time = time.perf_counter()
        orchestrator = VoiceOrchestrator()
        await orchestrator.initialize()
        end_time = time.perf_counter()
        
        init_time_ms = (end_time - start_time) * 1000
        
        # Assert
        assert init_time_ms <= 5, f"Orchestrator init {init_time_ms}ms exceeds 5ms target"
    
    @pytest.mark.asyncio
    async def test_concurrent_sessions_capacity(self):
        """Test system handles ≥100 concurrent sessions"""
        # Arrange
        orchestrator = VoiceOrchestrator()
        await orchestrator.initialize()
        
        async def simulate_session():
            await orchestrator.transcribe_audio(b"fake_audio", language="en")
            await asyncio.sleep(0.1)  # Simulate processing time
        
        # Act
        start_time = time.time()
        tasks = [simulate_session() for _ in range(100)]
        await asyncio.gather(*tasks)
        end_time = time.time()
        
        total_time = end_time - start_time
        
        # Assert
        assert total_time <= 10, f"100 concurrent sessions took {total_time}s, too slow"
```

## 🔗 Integration Testing Strategy

### 1. LangGraph Integration Tests

```python
# tests/integration/voice_v2/test_langgraph_integration.py
import pytest
from langgraph.graph import StateGraph
from app.services.voice_v2.integration.voice_tools import VoiceLangGraphTools
from app.services.voice_v2.integration.voice_workflow import VoiceEnabledWorkflow

class TestLangGraphIntegration:
    """Integration tests для LangGraph voice workflow"""
    
    @pytest.fixture
    async def voice_workflow(self):
        """Create voice-enabled LangGraph workflow"""
        workflow = VoiceEnabledWorkflow.create_voice_workflow()
        return workflow.compile()
    
    @pytest.mark.asyncio
    async def test_voice_intent_detection_flow(self, voice_workflow):
        """Test complete voice intent detection workflow"""
        # Arrange
        user_input = "Can you read this response out loud?"
        config = {"configurable": {"thread_id": "test_user_123"}}
        
        # Act
        result = await voice_workflow.ainvoke(
            {
                "messages": [{"role": "user", "content": user_input}],
                "user_data": {"voice_enabled": True},
                "voice_settings": {"language": "en", "style": "natural"}
            },
            config
        )
        
        # Assert
        assert "voice_intent" in result
        assert result["voice_intent"]["needs_voice_response"] is True
        assert "voice_response_url" in result
        assert result["voice_response_url"] is not None
    
    @pytest.mark.asyncio
    async def test_voice_synthesis_tool_integration(self):
        """Test voice synthesis tool в LangGraph context"""
        # Arrange
        tool = VoiceLangGraphTools.synthesize_voice_response
        text = "Hello, this is a test message"
        voice_config = {
            "language": "en",
            "style": "natural", 
            "speed": 1.0
        }
        
        # Mock state
        mock_state = {
            "user_data": {"voice_enabled": True},
            "chat_id": "test_chat"
        }
        
        # Act
        result = await tool.ainvoke({
            "text": text,
            "voice_config": voice_config,
            "state": mock_state
        })
        
        # Assert
        assert result["success"] is True
        assert "audio_url" in result
        assert result["format"] in ["mp3", "wav", "ogg"]
        assert result["synthesis_time_ms"] > 0
    
    @pytest.mark.asyncio
    async def test_voice_transcription_tool_integration(self):
        """Test voice transcription tool в LangGraph context"""
        # Arrange
        tool = VoiceLangGraphTools.transcribe_voice_message
        
        # Create fake audio data (in real test would use actual audio file)
        fake_audio = b"fake_audio_data_representing_speech"
        
        # Act
        result = await tool.ainvoke({
            "audio_data": fake_audio,
            "language": "en",
            "state": {"user_data": {"voice_enabled": True}}
        })
        
        # Assert
        assert result["success"] is True
        assert "text" in result
        assert result["confidence"] > 0
        assert result["processing_time_ms"] > 0
```

### 2. Provider Chain Integration Tests

```python
# tests/integration/voice_v2/test_provider_chains.py
import pytest
from app.services.voice_v2.core.voice_orchestrator import VoiceOrchestrator

class TestProviderChains:
    """Integration tests для provider fallback chains"""
    
    @pytest.mark.asyncio
    async def test_stt_provider_fallback_chain(self):
        """Test STT provider fallback при failures"""
        # Arrange
        orchestrator = VoiceOrchestrator()
        await orchestrator.initialize()
        
        # Simulate first provider failure
        with patch.object(orchestrator._stt_providers[0], 'transcribe_audio') as mock1:
            mock1.side_effect = Exception("Provider 1 failure")
            
            with patch.object(orchestrator._stt_providers[1], 'transcribe_audio') as mock2:
                mock2.return_value = TranscriptionResult(
                    text="Fallback success",
                    confidence=0.90,
                    detected_language="en",
                    provider="google"
                )
                
                # Act
                result = await orchestrator.transcribe_audio(b"audio_data")
                
                # Assert
                assert result.text == "Fallback success"
                assert result.provider == "google"
                assert mock1.call_count == 1  # First provider tried
                assert mock2.call_count == 1  # Second provider used
    
    @pytest.mark.asyncio
    async def test_tts_provider_performance_comparison(self):
        """Test TTS provider performance comparison"""
        # Arrange
        orchestrator = VoiceOrchestrator() 
        await orchestrator.initialize()
        text = "This is a performance test message"
        
        provider_times = {}
        
        # Act - Test each provider
        for provider in orchestrator._tts_providers:
            start_time = time.perf_counter()
            result = await provider.synthesize_speech(text, language="en")
            end_time = time.perf_counter()
            
            provider_times[provider.name] = (end_time - start_time) * 1000
        
        # Assert
        for provider_name, time_ms in provider_times.items():
            assert time_ms <= 2000, f"{provider_name} TTS took {time_ms}ms > 2s limit"
    
    @pytest.mark.asyncio
    async def test_cross_provider_consistency(self):
        """Test consistency между разными providers"""
        # Arrange
        orchestrator = VoiceOrchestrator()
        await orchestrator.initialize()
        
        # Same audio через different STT providers
        audio_data = b"consistent_audio_sample"
        
        results = []
        for provider in orchestrator._stt_providers:
            if provider.is_available:
                result = await provider.transcribe_audio(audio_data, language="en")
                results.append(result.text.lower())
        
        # Assert - All providers должны дать similar результаты
        assert len(set(results)) <= 2, "Providers giving inconsistent transcriptions"
```

## 🚀 Performance Testing Methodology

### 1. Load Testing Framework

```python
# tests/performance/voice_v2/test_load_performance.py
import pytest
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from app.services.voice_v2.core.voice_orchestrator import VoiceOrchestrator

class TestLoadPerformance:
    """Load testing для voice_v2 system"""
    
    @pytest.mark.asyncio
    async def test_concurrent_stt_requests(self):
        """Test concurrent STT request handling"""
        # Arrange
        orchestrator = VoiceOrchestrator()
        await orchestrator.initialize()
        
        audio_samples = [b"audio_sample_{i}" for i in range(50)]
        
        async def stt_request(audio_data):
            start_time = time.perf_counter()
            result = await orchestrator.transcribe_audio(audio_data)
            end_time = time.perf_counter()
            return (end_time - start_time) * 1000  # Return latency в ms
        
        # Act
        start_total = time.time()
        latencies = await asyncio.gather(*[
            stt_request(audio) for audio in audio_samples
        ])
        end_total = time.time()
        
        # Assert
        total_time = end_total - start_total
        avg_latency = sum(latencies) / len(latencies)
        p95_latency = sorted(latencies)[int(0.95 * len(latencies))]
        
        assert total_time <= 10, f"50 concurrent requests took {total_time}s"
        assert avg_latency <= 2000, f"Average latency {avg_latency}ms > 2s"
        assert p95_latency <= 3000, f"P95 latency {p95_latency}ms > 3s"
    
    @pytest.mark.asyncio 
    async def test_memory_usage_under_load(self):
        """Test memory usage при high load"""
        # Arrange
        import psutil
        import os
        
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        orchestrator = VoiceOrchestrator()
        await orchestrator.initialize()
        
        # Act - Simulate high load
        tasks = []
        for i in range(100):
            tasks.append(orchestrator.transcribe_audio(f"audio_data_{i}".encode()))
        
        await asyncio.gather(*tasks)
        
        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory
        
        # Assert
        assert memory_increase <= 100, f"Memory increased by {memory_increase}MB under load"
    
    @pytest.mark.asyncio
    async def test_cache_performance_under_load(self):
        """Test cache performance при concurrent access"""
        # Arrange
        cache_manager = RedisCacheManager()
        await cache_manager.initialize()
        
        # Act - Concurrent cache operations
        async def cache_operations():
            for i in range(100):
                await cache_manager.set(f"key_{i}", f"value_{i}")
                await cache_manager.get(f"key_{i}")
        
        start_time = time.time()
        tasks = [cache_operations() for _ in range(10)]  # 10 concurrent workers
        await asyncio.gather(*tasks)
        end_time = time.time()
        
        total_time = end_time - start_time
        
        # Assert
        ops_per_second = (10 * 100 * 2) / total_time  # 2 ops (set+get) per iteration
        assert ops_per_second >= 5000, f"Cache performance {ops_per_second} ops/s < 5000"
```

### 2. Benchmark Testing

```python
# tests/performance/voice_v2/test_benchmarks.py
import pytest
import time
from app.services.voice_v2.core.voice_orchestrator import VoiceOrchestrator

class TestVoice2Benchmarks:
    """Benchmark tests против performance targets"""
    
    @pytest.mark.asyncio
    async def test_stt_latency_benchmark(self):
        """Benchmark STT latency против target (≤2s)"""
        # Arrange
        orchestrator = VoiceOrchestrator()
        await orchestrator.initialize()
        
        # Different audio lengths to test
        test_cases = [
            (b"short_audio_5s", 5, 1500),    # Short audio - 1.5s target
            (b"medium_audio_15s", 15, 2000), # Medium audio - 2.0s target  
            (b"long_audio_30s", 30, 3000)    # Long audio - 3.0s target
        ]
        
        for audio_data, duration, target_ms in test_cases:
            # Act
            start_time = time.perf_counter()
            result = await orchestrator.transcribe_audio(audio_data)
            end_time = time.perf_counter()
            
            latency_ms = (end_time - start_time) * 1000
            
            # Assert
            assert latency_ms <= target_ms, (
                f"STT latency {latency_ms}ms > {target_ms}ms target "
                f"for {duration}s audio"
            )
    
    @pytest.mark.asyncio
    async def test_tts_latency_benchmark(self):
        """Benchmark TTS latency против target (≤1.5s)"""
        # Arrange  
        orchestrator = VoiceOrchestrator()
        await orchestrator.initialize()
        
        test_cases = [
            ("Short text", 1000),        # ≤1s для короткого текста
            ("Medium length text that contains more words and complexity", 1500),  # ≤1.5s
            ("Very long text " * 50, 2000)  # ≤2s для длинного текста
        ]
        
        for text, target_ms in test_cases:
            # Act
            start_time = time.perf_counter()
            result = await orchestrator.synthesize_speech(text, language="en")
            end_time = time.perf_counter()
            
            latency_ms = (end_time - start_time) * 1000
            
            # Assert
            assert latency_ms <= target_ms, (
                f"TTS latency {latency_ms}ms > {target_ms}ms target "
                f"for text length {len(text)}"
            )
```

## 📊 Test Coverage & Quality Metrics

### Test Coverage Configuration

```python
# pytest.ini
[tool:pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = 
    --cov=app.services.voice_v2
    --cov-report=html:htmlcov  
    --cov-report=term-missing
    --cov-report=xml
    --cov-fail-under=100
    --strict-markers
    --disable-warnings
markers = 
    asyncio: async test marker
    unit: unit test marker  
    integration: integration test marker
    performance: performance test marker
    slow: slow running test marker
```

### Quality Gates

```python
# tests/quality/test_code_quality.py
import pytest
import subprocess
import sys

class TestCodeQuality:
    """Code quality validation tests"""
    
    def test_pylint_score(self):
        """Test Pylint score ≥9.5/10"""
        result = subprocess.run([
            sys.executable, "-m", "pylint", 
            "app/services/voice_v2",
            "--output-format=text",
            "--score=yes"
        ], capture_output=True, text=True)
        
        # Extract score from output
        for line in result.stdout.split('\n'):
            if "Your code has been rated at" in line:
                score = float(line.split()[6].split('/')[0])
                assert score >= 9.5, f"Pylint score {score} < 9.5 requirement"
                return
        
        pytest.fail("Could not extract Pylint score")
    
    def test_complexity_check(self):
        """Test cyclomatic complexity ≤8"""
        result = subprocess.run([
            sys.executable, "-m", "lizard", 
            "app/services/voice_v2",
            "--CCN", "8"
        ], capture_output=True, text=True)
        
        assert result.returncode == 0, (
            f"Complexity check failed: {result.stdout}"
        )
    
    def test_security_scan(self):
        """Test security issues = 0"""
        result = subprocess.run([
            sys.executable, "-m", "semgrep", 
            "--config=auto",
            "app/services/voice_v2"
        ], capture_output=True, text=True)
        
        # Check for no findings
        assert "0 findings" in result.stdout or result.returncode == 0
```

## 🔄 Continuous Testing Pipeline

### CI/CD Integration

```yaml
# .github/workflows/voice_v2_tests.yml
name: Voice_v2 Testing Pipeline

on:
  push:
    paths:
      - 'app/services/voice_v2/**'
  pull_request:
    paths:
      - 'app/services/voice_v2/**'

jobs:
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      
      - name: Install dependencies
        run: |
          pip install uv
          uv sync --frozen
      
      - name: Run unit tests
        run: |
          uv run pytest tests/unit/voice_v2/ -v --cov-report=xml
      
      - name: Upload coverage
        uses: codecov/codecov-action@v3

  integration-tests:
    runs-on: ubuntu-latest
    needs: unit-tests
    services:
      redis:
        image: redis:7
        ports:
          - 6379:6379
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: test
        ports:
          - 5432:5432
    
    steps:
      - uses: actions/checkout@v3
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      
      - name: Run integration tests
        run: |
          uv run pytest tests/integration/voice_v2/ -v

  performance-tests:
    runs-on: ubuntu-latest
    needs: [unit-tests, integration-tests]
    
    steps:
      - uses: actions/checkout@v3
      - name: Setup Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      
      - name: Run performance benchmarks
        run: |
          uv run pytest tests/performance/voice_v2/ -v --benchmark-json=benchmark.json
      
      - name: Performance regression check
        run: |
          python scripts/check_performance_regression.py benchmark.json

  quality-gates:
    runs-on: ubuntu-latest
    needs: [unit-tests, integration-tests]
    
    steps:
      - uses: actions/checkout@v3
      - name: Code quality checks
        run: |
          uv run pytest tests/quality/ -v
      
      - name: Security scan
        run: |
          uv run semgrep --config=auto app/services/voice_v2/
```

## 📋 Testing Implementation Timeline

### Phase 2 Testing Tasks

1. **Week 1**: Unit test infrastructure setup
2. **Week 2**: Core component unit tests
3. **Week 3**: Provider integration tests
4. **Week 4**: LangGraph workflow tests
5. **Week 5**: Performance testing suite
6. **Week 6**: CI/CD pipeline integration

### Testing Maintenance Strategy

- **Daily**: Automated test runs в CI/CD
- **Weekly**: Performance regression analysis
- **Monthly**: Test coverage review и gap analysis
- **Quarterly**: Testing strategy review и improvements

## 🎯 Success Criteria

### Testing Readiness Checklist

- [x] Unit testing strategy определена
- [x] Integration testing framework спроектирован
- [x] Performance testing methodology разработана
- [x] LangGraph testing approach планирован
- [x] Quality gates установлены
- [x] CI/CD pipeline designed
- [x] Test coverage targets (100%) установлены

**Testing Strategy Status**: ✅ **COMPLETE**

**Ready for Implementation**: ✅ **YES**

---

**Статус**: ✅ Testing strategy planning завершено  
**Следующий этап**: Phase 1.3.4 - Migration Strategy Planning
