# 🎯 АРХИТЕКТУРНЫЕ ПАТТЕРНЫ APP/SERVICES/VOICE

**Дата:** 27 июля 2025  
**Фаза:** 1.1.4 - Документирование успешных паттернов  
**Статус:** ✅ **ЗАВЕРШЕНО**

---

## 🎯 **EXECUTIVE SUMMARY**

Детальный анализ архитектурных паттернов референсной системы `app/services/voice` для применения в `voice_v2`. Изучены успешные решения, error handling стратегии и performance optimization техники.

### **Ключевые успешные паттерны:**
- ✅ **Orchestrator Pattern** - централизованная координация провайдеров
- ✅ **Provider Abstraction** - единые интерфейсы для STT/TTS
- ✅ **Fallback Chain** - автоматическое переключение провайдеров
- ✅ **Redis Caching** - эффективное кэширование результатов
- ✅ **Metrics Collection** - comprehensive monitoring

---

## 🏗️ **ORCHESTRATOR PATTERN ANALYSIS**

### **VoiceServiceOrchestrator как Central Coordinator**

**Файл:** `app/services/voice/voice_orchestrator.py` (1,040 строк)

### **Ключевые принципы:**
```python
class VoiceServiceOrchestrator:
    def __init__(self, redis_service, logger):
        self.redis_service = redis_service
        self.logger = logger
        self.minio_manager = None
        self.voice_providers = {}
        self.metrics_collector = None
```

### **Паттерн Single Responsibility:**
- **Orchestrator** - координация провайдеров
- **MinIOManager** - файловое хранилище
- **MetricsCollector** - сбор метрик
- **RateLimiter** - управление нагрузкой

### **Успешные решения для voice_v2:**
1. **Dependency Injection** через constructor
2. **Lazy initialization** провайдеров по требованию
3. **Configuration validation** при инициализации
4. **Graceful cleanup** ресурсов

### **Архитектурный код (референс):**
```python
async def initialize_voice_services_for_agent(self, agent_id: str, agent_config: dict) -> bool:
    """Инициализация voice сервисов для агента"""
    voice_settings = self._extract_voice_settings(agent_config)
    if not voice_settings.get('enabled', False):
        return False
        
    providers = []
    for provider_config in voice_settings.get('providers', []):
        provider = await self._create_provider(provider_config)
        if provider:
            providers.append(provider)
    
    self.voice_providers[agent_id] = providers
    return len(providers) > 0
```

---

## 🔧 **PROVIDER ABSTRACTION PATTERN**

### **Unified STT/TTS Interface Design**

### **Base Provider Structure:**
```python
# Базовая архитектура провайдеров
class BaseSTTProvider:
    async def transcribe(self, audio_file: str, language: str = "auto") -> str
    
class BaseTTSProvider:
    async def synthesize(self, text: str, voice_settings: dict) -> str
```

### **Provider Implementation Pattern:**
**OpenAI STT Provider** (~350 строк):
```python
class OpenAISTTProvider(BaseSTTProvider):
    def __init__(self, api_key: str, logger):
        self.client = OpenAI(api_key=api_key)
        self.logger = logger
        
    async def transcribe(self, audio_file: str, language: str = "auto") -> str:
        try:
            with open(audio_file, "rb") as f:
                response = await self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    language=language if language != "auto" else None
                )
                return response.text
        except Exception as e:
            self.logger.error(f"OpenAI STT error: {e}")
            raise
```

### **Успешные принципы для voice_v2:**
1. **Consistent interface** - все провайдеры имеют одинаковые методы
2. **Error propagation** - стандартизированная обработка ошибок
3. **Configuration injection** - настройки через constructor
4. **Resource management** - proper cleanup

---

## 🔄 **FALLBACK CHAIN PATTERN**

### **Provider Priority System**

### **Fallback Logic Implementation:**
```python
async def _execute_with_fallback(self, operation: str, providers: List, *args, **kwargs):
    """Выполнение операции с fallback между провайдерами"""
    last_error = None
    
    for provider in sorted(providers, key=lambda p: p.priority):
        try:
            result = await getattr(provider, operation)(*args, **kwargs)
            await self._record_success_metric(provider, operation)
            return result
        except Exception as e:
            last_error = e
            await self._record_error_metric(provider, operation, e)
            self.logger.warning(f"Provider {provider.name} failed: {e}")
            continue
    
    raise last_error or Exception("All providers failed")
```

### **Успешные аспекты:**
1. **Priority ordering** - провайдеры упорядочены по приоритету
2. **Error logging** - каждая ошибка логируется
3. **Metrics recording** - success/failure записываются
4. **Last error propagation** - сохранение последней ошибки

### **Improvements для voice_v2:**
- **Circuit breaker pattern** - временное отключение failed провайдеров
- **Retry logic** - повторные попытки с exponential backoff
- **Health checking** - проверка доступности провайдеров

---

## 🔴 **REDIS CACHING STRATEGY**

### **Efficient Caching Implementation**

### **Cache Key Design:**
```python
def _generate_cache_key(self, operation: str, content_hash: str, settings: dict = None) -> str:
    """Генерация ключа кэша"""
    settings_hash = hashlib.md5(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:8]
    return f"voice:{operation}:{content_hash}:{settings_hash}"
```

### **STT Cache Pattern:**
```python
async def transcribe_with_cache(self, audio_file: str, language: str = "auto") -> str:
    # Проверка кэша
    file_hash = await self._calculate_file_hash(audio_file)
    cache_key = self._generate_cache_key("stt", file_hash, {"language": language})
    
    cached_result = await self.redis_service.get(cache_key)
    if cached_result:
        return cached_result
    
    # Выполнение STT
    result = await self._execute_stt_with_fallback(audio_file, language)
    
    # Сохранение в кэш
    await self.redis_service.set(cache_key, result, ex=86400)  # 24 часа
    return result
```

### **TTS Cache Pattern:**
```python
async def synthesize_with_cache(self, text: str, voice_settings: dict) -> str:
    text_hash = hashlib.md5(text.encode()).hexdigest()
    cache_key = self._generate_cache_key("tts", text_hash, voice_settings)
    
    cached_url = await self.redis_service.get(cache_key)
    if cached_url:
        return cached_url
    
    # Выполнение TTS и upload в MinIO
    audio_url = await self._execute_tts_with_upload(text, voice_settings)
    await self.redis_service.set(cache_key, audio_url, ex=86400)
    return audio_url
```

### **Cache Success Factors:**
1. **Smart key generation** - учет всех параметров
2. **Appropriate TTL** - 24 часа для voice кэша
3. **Hash-based identification** - эффективная идентификация
4. **Hit ratio optimization** - минимизация duplicate requests

---

## 📊 **METRICS COLLECTION PATTERN**

### **Comprehensive Performance Monitoring**

### **VoiceMetrics Data Model:**
```python
@dataclass
class VoiceMetrics:
    timestamp: float
    agent_id: str
    user_id: str
    operation: str  # "stt" | "tts"
    provider: str
    success: bool
    processing_time: float
    input_size_bytes: int
    output_size_bytes: int
    error_message: Optional[str] = None
```

### **Redis Metrics Storage:**
```python
async def record_metric(self, metric: VoiceMetrics):
    """Запись метрики в Redis"""
    # Daily aggregation key
    date_key = datetime.fromtimestamp(metric.timestamp).strftime("%Y-%m-%d")
    redis_key = f"voice_metrics:{metric.agent_id}:{date_key}"
    
    # Структурированное сохранение
    metric_data = {
        "timestamp": metric.timestamp,
        "operation": metric.operation,
        "provider": metric.provider,
        "success": metric.success,
        "processing_time": metric.processing_time,
        "user_id": metric.user_id
    }
    
    await self.redis_service.zadd(redis_key, {
        json.dumps(metric_data): metric.timestamp
    })
```

### **Daily Stats Aggregation:**
```python
async def get_daily_stats(self, agent_id: str) -> dict:
    """Получение дневной статистики"""
    stats = {"agent_id": agent_id, "daily_stats": []}
    
    for days_ago in range(7):  # Последние 7 дней
        date = datetime.now() - timedelta(days=days_ago)
        date_key = date.strftime("%Y-%m-%d")
        redis_key = f"voice_metrics:{agent_id}:{date_key}"
        
        metrics = await self.redis_service.zrange(redis_key, 0, -1)
        day_stats = self._aggregate_day_metrics(date_key, metrics)
        stats["daily_stats"].append(day_stats)
    
    return stats
```

### **Monitoring Success Factors:**
1. **Structured data** - consistent метрики format
2. **Time-based aggregation** - daily/weekly stats
3. **Multi-dimensional analysis** - по провайдерам, операциям
4. **Real-time availability** - быстрый доступ к статистике

---

## 🚀 **INTENT DETECTION PATTERN**

### **Keyword-Based Detection System**

### **VoiceIntentDetector Implementation:**
```python
class VoiceIntentDetector:
    def __init__(self, logger):
        self.logger = logger
        
    def detect_tts_intent(self, text: str, intent_keywords: List[str]) -> bool:
        """Определение намерения голосового ответа"""
        text_lower = text.lower()
        for keyword in intent_keywords:
            if keyword.lower() in text_lower:
                return True
        return False
        
    def extract_voice_settings(self, agent_config: dict) -> dict:
        """Извлечение voice настроек из конфигурации агента"""
        return agent_config.get("config", {}).get("simple", {}).get("settings", {}).get("voice_settings", {})
```

### **Integration с LangGraph Tools:**
```python
# Текущий pattern в LangGraph
async def voice_capabilities_tool(
    text: Annotated[str, "Text to synthesize"],
    voice_enabled: Annotated[bool, "Enable voice synthesis"] = True,
    state: Annotated[Dict, InjectedState] = None
) -> str:
    if not voice_enabled:
        return "Voice synthesis disabled"
        
    # Intent detection
    intent_keywords = ["голос", "озвучь", "скажи голосом"]
    has_intent = detector.detect_tts_intent(text, intent_keywords)
    
    if has_intent:
        # TTS execution
        return await orchestrator.synthesize_speech(text)
    else:
        return "Text response only"
```

### **Areas for Improvement в voice_v2:**
1. **Context awareness** - использовать LangGraph state для лучшего понимания
2. **ML-based detection** - замена keyword matching на semantic analysis
3. **User preferences** - персонализированный intent detection
4. **A/B testing** - экспериментирование с алгоритмами

---

## 🔧 **ERROR HANDLING STRATEGIES**

### **Comprehensive Error Management**

### **Exception Hierarchy:**
```python
class VoiceServiceError(Exception):
    """Base voice service exception"""
    pass

class ProviderError(VoiceServiceError):
    """Provider-specific error"""
    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(f"Provider {provider}: {message}")

class RateLimitError(VoiceServiceError):
    """Rate limit exceeded"""
    pass

class AudioFormatError(VoiceServiceError):
    """Audio format not supported"""
    pass
```

### **Error Handling Pattern:**
```python
async def _safe_provider_operation(self, provider, operation: str, *args, **kwargs):
    """Безопасное выполнение операции провайдера"""
    try:
        start_time = time.time()
        result = await getattr(provider, operation)(*args, **kwargs)
        processing_time = time.time() - start_time
        
        # Record success metric
        await self.metrics_collector.record_metric(VoiceMetrics(
            timestamp=time.time(),
            agent_id=self.current_agent_id,
            operation=operation,
            provider=provider.name,
            success=True,
            processing_time=processing_time
        ))
        
        return result
        
    except Exception as e:
        # Record failure metric
        await self.metrics_collector.record_metric(VoiceMetrics(
            timestamp=time.time(),
            agent_id=self.current_agent_id,
            operation=operation,
            provider=provider.name,
            success=False,
            processing_time=0,
            error_message=str(e)
        ))
        
        self.logger.error(f"Provider {provider.name} {operation} failed: {e}")
        raise ProviderError(provider.name, str(e))
```

### **Resilience Patterns:**
1. **Graceful degradation** - fallback на следующий провайдер
2. **Error categorization** - разные типы ошибок обрабатываются по-разному  
3. **Metrics on failures** - все ошибки записываются в метрики
4. **Context preservation** - сохранение контекста для debugging

---

## 🎯 **PERFORMANCE OPTIMIZATION TECHNIQUES**

### **Proven Optimization Strategies**

### **1. Connection Pooling:**
```python
# Redis connection management
class RedisService:
    def __init__(self):
        self._pool = None
        
    async def initialize(self):
        self._pool = await init_redis_pool()
        self.client = redis.Redis.from_pool(self._pool)
```

### **2. Async Operations:**
```python
# Все операции асинхронные
async def transcribe_batch(self, audio_files: List[str]) -> List[str]:
    """Batch STT обработка"""
    tasks = []
    for audio_file in audio_files:
        task = self.transcribe_with_cache(audio_file)
        tasks.append(task)
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if not isinstance(r, Exception)]
```

### **3. File Size Optimization:**
```python
# Audio format conversion для совместимости
async def _prepare_audio_for_provider(self, audio_file: str, provider: str) -> str:
    """Подготовка аудио файла для провайдера"""
    if provider == "google" and audio_file.endswith(".ogg"):
        # Convert OGG to WAV for Google
        wav_file = audio_file.replace(".ogg", ".wav")
        await convert_audio_format(audio_file, wav_file)
        return wav_file
    return audio_file
```

### **4. Memory Management:**
```python
# Cleanup временных файлов
async def cleanup(self):
    """Очистка ресурсов"""
    if self.minio_manager:
        await self.minio_manager.cleanup()
        
    # Cleanup temp files
    temp_dir = Path("/tmp/voice_processing")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
```

---

## 📋 **ARCHITECTURE DECISIONS ДЛЯ VOICE_V2**

### **Применение паттернов в voice_v2:**

### **✅ Keep (Успешные паттерны):**
1. **Orchestrator Pattern** - централизованная координация
2. **Provider Abstraction** - единые интерфейсы
3. **Redis Caching** - эффективное кэширование
4. **Metrics Collection** - comprehensive monitoring
5. **Async Operations** - все операции асинхронные

### **🔧 Improve (Улучшения):**
1. **Intent Detection** → LangGraph context awareness
2. **Error Handling** → Circuit breaker patterns
3. **Fallback Logic** → Retry с exponential backoff
4. **Provider Health** → Active health checking
5. **Configuration** → Pydantic schemas с validation

### **➕ Add (Новый функционал):**
1. **Circuit Breaker** - временное отключение failed провайдеров
2. **Rate Limiting** - advanced quota management
3. **Memory Profiling** - tracking memory usage
4. **Load Balancing** - распределение нагрузки между провайдерами
5. **A/B Testing** - экспериментирование с алгоритмами

---

## ✅ **CHECKLIST UPDATE**

### **Фаза 1.1.4 - Документирование успешных паттернов**: ✅ **ЗАВЕРШЕНО**
- [x] Architecture patterns из app/services/voice ✅ **Проанализированы**
- [x] Error handling strategies ✅ **Задокументированы**
- [x] Performance optimization techniques ✅ **Изучены**

### **Следующие шаги:**
1. **Фаза 1.2.1** - Определение file structure (≤50 файлов)
2. **Фаза 1.2.2** - SOLID принципы планирование
3. **Фаза 1.2.3** - Dependency injection design

---

## 🎯 **ЗАКЛЮЧЕНИЕ**

Анализ `app/services/voice` выявил множество **успешных архитектурных паттернов**, которые должны быть перенесены в `voice_v2`:

### **Core Success Factors:**
- **Simple but effective architecture** - без оверинжиниринга
- **Proven patterns** - Orchestrator, Provider abstraction, Fallback chain
- **Robust error handling** - comprehensive error management
- **Performance optimizations** - caching, async operations, connection pooling

### **Voice_v2 Architecture Foundation:**
Используя успешные паттерны как foundation, `voice_v2` будет иметь solid architecture baseline с targeted improvements для performance, reliability и maintainability.

**Готовность к следующей фазе:** ✅ **READY FOR PHASE 1.2**
