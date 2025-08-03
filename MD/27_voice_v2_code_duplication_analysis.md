# Анализ дублирования кода в Voice_v2 STT провайдерах

## 📊 Общая ситуация с дублированием

### Codacy метрики репозитория:
- **Общий процент дублирования**: 18% (превышает целевые 10%)
- **Файлы с максимальным дублированием**:
  - `app/services/voice/voice_orchestrator.py`: 468 дублированных строк (17 клонов)
  - `app/integrations/whatsapp/whatsapp_bot.py`: 140 строк (9 клонов)
  - `app/api/routers/integration_api.py`: 89 строк (6 клонов)

### Voice_v2 STT провайдеры состояние:
✅ **Локальный анализ Codacy CLI**: Нет критических проблем
✅ **Pylint**: Нет нарушений
✅ **Semgrep OSS**: Нет уязвимостей безопасности
✅ **Trivy**: Нет проблем с зависимостями

## 🔍 Выявленные паттерны дублирования в STT провайдерах

### 1. **Методы инициализации и очистки** (Высокий приоритет)

#### Дублированная логика в `initialize()`:
```python
# OpenAI STT
async def initialize(self) -> None:
    if not self.api_key:
        raise ProviderNotAvailableError(self.provider_name, "OpenAI API key не настроен")
    try:
        await self._ensure_session()
        self.client = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout, max_retries=self.max_retries)
        health_result = await self._initial_health_check()
        self._initialized = True
        logger.info("OpenAI STT provider initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize OpenAI STT provider: %s", e, exc_info=True)
        raise ProviderNotAvailableError(self.provider_name, f"Ошибка инициализации: {str(e)}")

# Google STT
async def initialize(self) -> None:
    try:
        logger.debug("Initializing Google STT Provider...")
        if (not self._google_config["credentials_path"] and not os.getenv('GOOGLE_APPLICATION_CREDENTIALS')):
            logger.warning("Google Cloud credentials not configured - provider disabled")
            raise ProviderNotAvailableError(provider="Google STT", reason="No credentials configured")
        await self._initialize_credentials()
        await self._initialize_client()
        await self._validate_connection()
        self._initialized = True
        logger.info("Google STT Provider initialized successfully")
    except Exception as e:
        logger.error("Google STT initialization failed: %s", e, exc_info=True)
        raise VoiceConfigurationError(f"Google STT init error: {e}") from e

# Yandex STT
async def initialize(self) -> None:
    if not self.api_key:
        raise ProviderNotAvailableError("Yandex API key not configured")
    if not self.folder_id:
        raise ProviderNotAvailableError("Yandex folder ID not configured")
    await self._setup_connection_pool()
    if not await self.health_check():
        raise ProviderNotAvailableError("Yandex STT service not available")
    logger.info("Yandex STT provider '%s' initialized successfully", self.provider_name)
```

**Общие паттерны**:
- Проверка конфигурации API ключей/credentials
- Try-catch блоки с одинаковой структурой  
- Идентичное логирование ошибок и успеха
- Однотипная обработка исключений
- Health check или connectivity validation

#### Дублированная логика в `cleanup()`:
```python
# OpenAI STT
async def cleanup(self) -> None:
    try:
        if self.client:
            await self.client.close()
        if self._session:
            await self._session.close()
        logger.info("OpenAI STT provider cleaned up")
    except Exception as e:
        logger.warning("Cleanup warning: %s", e)
    finally:
        self.client = None
        self._session = None
        self._initialized = False

# Google STT
async def cleanup(self) -> None:
    try:
        self._client = None
        self._credentials = None
        self._initialized = False
        logger.debug("Google STT Provider cleaned up")
    except Exception as e:
        logger.error("Google STT cleanup error: %s", e, exc_info=True)

# Yandex STT
async def cleanup(self) -> None:
    await self._cleanup_connections()

async def _cleanup_connections(self) -> None:
    if self._session:
        await self._session.close()
        self._session = None
    if self._connector:
        await self._connector.close()
        self._connector = None
```

### 2. **Transcription Implementation Patterns** (Высокий приоритет)

#### ConnectionManager Integration:
```python
# OpenAI, Google STT - идентичный паттерн:
# Use ConnectionManager if available, fallback to legacy retry
if self._has_connection_manager():
    audio_data = await self._perform_transcription(transcription_params)
else:
    # Legacy fallback for backward compatibility
    audio_data = await self._transcribe_with_retry(transcription_params)
```

#### Retry Logic Patterns:
```python
# Во всех STT провайдерах дублируется retry логика:

# OpenAI STT
async def _transcribe_with_retry(self, audio_path: Path, params: Dict[str, Any]) -> Any:
    for attempt in range(self.max_retries + 1):
        try:
            return await self._perform_transcription(session, audio_path, params)
        except (APIError, APIConnectionError, RateLimitError) as e:
            if attempt == self.max_retries:
                raise AudioProcessingError(f"OpenAI transcription failed: {e}") from e
            delay = retry_delay * (2 ** attempt)  # Exponential backoff
            logger.warning("OpenAI API error (attempt %s), retrying in %ss: %s", attempt + 1, delay, e)
            await asyncio.sleep(delay)

# Google STT  
async def _transcribe_with_retry(self, config: speech.RecognitionConfig, audio: speech.RecognitionAudio) -> speech.RecognizeResponse:
    for attempt in range(self._google_config["max_retries"] + 1):
        try:
            return await self._execute_google_transcription(config, audio)
        except google_exceptions.GoogleAPIError as e:
            if not self._should_retry_transient_error(e, attempt):
                raise self._create_timeout_error(e, attempt)
            if attempt < self._google_config["max_retries"]:
                await self._apply_retry_delay(attempt)

# Yandex STT
async def _transcribe_with_retry(self, audio_data: bytes, audio_format: str, language: str, enable_profanity_filter: bool = True) -> STTResult:
    for attempt in range(len(self.RETRY_DELAYS)):
        try:
            return await self._execute_transcription_request(audio_data, params)
        except Exception as error:
            if await self._handle_yandex_general_error(error, attempt, len(self.RETRY_DELAYS)):
                continue
            else:
                break
    raise VoiceServiceError("All retry attempts failed")
```

#### Audio Processing Patterns:
```python
# Общие паттерны обработки аудио файлов:
- Чтение аудио файла в bytes
- Создание временных файлов  
- Валидация формата и размера
- Обработка результатов транскрипции
```

### 3. **Конфигурационные паттерны** (Средний приоритет)

#### Общие config структуры:
```python
# OpenAI STT
self.api_key = config_api_key or settings_api_key
self.model = config.get("model", "whisper-1")
self.timeout = config.get("timeout", 30)
self.max_retries = config.get("max_retries", 3)
self.retry_delay = config.get("retry_delay", 1.0)

# Google STT
self._google_config = {
    "credentials_path": self.config.get('credentials_path'),
    "credentials_json": self.config.get('credentials_json'),
    "project_id": self.config.get('project_id'),
    "language_code": self.config.get('language_code', 'ru-RU'),
    "model": self.config.get('model', 'latest_long'),
    "use_enhanced": self.config.get('use_enhanced', True),
    "max_retries": self.config.get('max_retries', 3),
    "base_delay": self.config.get('base_delay', 1.0),
    "max_delay": self.config.get('max_delay', 60.0),
    "timeout": self.config.get('timeout', 120.0)
}

# Yandex STT
self.max_connections = config.get("max_connections", 10)
self.connection_timeout = config.get("connection_timeout", 30.0)
self.read_timeout = config.get("read_timeout", 60.0)
```

#### Validation Patterns:
```python
# Все провайдеры имеют схожую валидацию:
def _validate_request(self, audio_data: bytes, audio_format: str, language: str) -> Dict[str, Any]:
    - Валидация аудио данных (не пустые, тип bytes)
    - Валидация формата (поддерживаемые форматы)  
    - Валидация языка (список поддерживаемых)
    - Валидация размера файла
    - Provider-specific constraints
```

### 4. **Логирование и error handling** (Средний приоритет)

#### Повторяющиеся logger patterns:
```python
# Инициализация
logger.info("Provider STT provider initialized successfully")
logger.error("Failed to initialize Provider STT provider: %s", e, exc_info=True)

# Cleanup  
logger.debug("Provider STT Provider cleaned up")
logger.error("Provider STT cleanup error: %s", e, exc_info=True)

# Retry логика
logger.warning("Provider API error (attempt %s), retrying in %ss: %s", attempt + 1, delay, e)
logger.debug("Retrying Provider STT (attempt %s) after %ss", attempt + 1, delay)

# Health checks
logger.warning("Provider health check failed: %s", e)
```

#### Error Handling Patterns:
```python
# Общие паттерны обработки ошибок:
try:
    # Provider operation
except ProviderSpecificError as e:
    # Handle provider-specific errors
    logger.error("Provider error: %s", e)
    raise AudioProcessingError(f"Provider failed: {e}") from e
except Exception as e:
    # Handle generic errors  
    logger.error("Unexpected error: %s", e, exc_info=True)
    raise VoiceServiceError(f"Unexpected error: {e}") from e
```

### 5. **Health Check Patterns** (Средний приоритет)

#### Дублированная логика health check:
```python
# OpenAI STT
async def _initial_health_check(self) -> bool:
    try:
        # Test with minimal request
        test_audio = b"minimal_test_audio"
        # Simple API call
        return True
    except Exception:
        return False

# Google STT  
async def _validate_connection(self) -> None:
    try:
        await self._client.list_voices()  # Simple API call
        logger.debug("Google Speech connection validated")
    except Exception as e:
        raise AudioProcessingError(f"Google Cloud TTS connectivity failed: {e}") from e

# Yandex STT
async def health_check(self) -> bool:
    try:
        test_audio = b"dummy_audio_data_for_health_check"
        async with self._session.post(self.STT_API_URL, data=test_audio, params={...}) as response:
            return response.status in [200, 400]  # Service up even if request fails
    except Exception:
        return False
```

## 💡 Рекомендации по устранению дублирования

### 1. **Создать базовый mixin для инициализации** (Приоритет: Высокий)

```python
# app/services/voice_v2/providers/stt/initialization_mixin.py
class STTInitializationMixin:
    """Mixin для стандартизации инициализации STT провайдеров."""
    
    async def _standard_initialize(
        self,
        validation_checks: List[Callable[[], None]],  # Config validation functions
        client_factory: Callable[[], Awaitable[Any]],  # Client creation
        health_check: Optional[Callable[[], Awaitable[bool]]] = None,
        provider_name: str = None
    ) -> None:
        """Стандартная логика инициализации провайдера."""
        
    async def _standard_cleanup(
        self,
        cleanup_tasks: List[Callable[[], Awaitable[None]]],  # Cleanup operations
        provider_name: str = None
    ) -> None:
        """Стандартная логика очистки ресурсов."""
```

### 2. **Создать Retry Mixin для транскрипции** (Приоритет: Высокий)

```python
# app/services/voice_v2/providers/stt/transcription_retry_mixin.py
class STTRetryMixin:
    """Mixin для стандартизации retry логики STT операций."""
    
    async def _standard_transcribe_with_retry(
        self,
        transcription_func: Callable[[], Awaitable[Any]],
        error_handlers: Dict[Type[Exception], Callable[[Exception, int], bool]],
        max_retries: int = 3,
        base_delay: float = 1.0
    ) -> Any:
        """Стандартная retry логика для транскрипции."""
```

### 3. **Configuration Template Pattern** (Приоритет: Средний)

```python
# app/services/voice_v2/providers/stt/config_template.py
class STTConfigTemplate:
    """Шаблон конфигурации для STT провайдеров."""
    
    @classmethod
    def create_standard_config(
        cls,
        config: Dict[str, Any],
        defaults: Dict[str, Any],
        required_fields: List[str] = None
    ) -> Dict[str, Any]:
        """Создает стандартную конфигурацию с валидацией."""
```

### 4. **Health Check Standardization** (Приоритет: Средний)

```python
# app/services/voice_v2/providers/stt/health_check_mixin.py
class STTHealthCheckMixin:
    """Стандартизированные health checks для STT провайдеров."""
    
    async def _standard_health_check(
        self,
        test_operation: Callable[[], Awaitable[bool]],
        provider_name: str,
        timeout: float = 10.0
    ) -> bool:
        """Стандартная проверка здоровья провайдера."""
```

### 5. **Logging Standardization** (Приоритет: Средний)

```python
# app/services/voice_v2/providers/stt/logging_mixin.py
class STTLoggingMixin:
    """Стандартизированное логирование для STT провайдеров."""
    
    def _log_initialization_success(self, provider_details: str) -> None:
    def _log_initialization_error(self, error: Exception) -> None:
    def _log_cleanup_success(self) -> None:
    def _log_transcription_performance(self, duration: float, audio_size: int) -> None:
    def _log_retry_attempt(self, attempt: int, delay: float, error: Exception) -> None:
```

## 📋 План рефакторинга

### Phase 1: Критические дублирования (1-2 дня)
1. ✅ Создать STTInitializationMixin
2. ✅ Создать STTRetryMixin для унификации retry логики
3. ✅ Интегрировать mixins в существующие провайдеры

### Phase 2: Специализированные mixins (1 день)
1. ✅ Создать STTHealthCheckMixin
2. ✅ Создать STTConfigTemplate  
3. ✅ Создать STTLoggingMixin
4. ✅ Обновить провайдеры

### Phase 3: Валидация и тестирование (1 день)
1. ✅ Проверить совместимость с существующими тестами
2. ✅ Провести regress testing STT функционала
3. ✅ Измерить улучшение метрик дублирования

## 🎯 Ожидаемый результат

### Целевые метрики после рефакторинга:
- **Снижение дублирования STT провайдеров**: с ~20% до <5%
- **Уменьшение размера файлов**: каждый провайдер -80-120 строк
- **Pylint score**: поддержание 9.5+/10
- **Упрощение добавления новых провайдеров**: стандартные mixins

### Преимущества:
1. **DRY Principle**: Устранение дублированного кода в retry логике, инициализации, health checks
2. **Maintainability**: Единая точка изменения общей логики транскрипции
3. **Consistency**: Стандартизированное поведение всех STT провайдеров
4. **Extensibility**: Простое добавление новых STT провайдеров через mixins
5. **Error Handling**: Унифицированная обработка ошибок и retry patterns

### Детальный анализ дублирования:
- **Initialization patterns**: ~60 строк дублированного кода на провайдер
- **Retry logic**: ~40 строк схожей логики в каждом провайдере  
- **Health checks**: ~25 строк повторяющихся паттернов
- **Configuration handling**: ~30 строк схожих структур
- **Logging patterns**: ~20 строк идентичных логов

**Общее количество дублированного кода**: ~175 строк на провайдер × 3 провайдера = **~525 строк дублированного кода**

---

**Дата анализа**: 3 августа 2025  
**Анализ выполнен**: Codacy MCP + ручной code review STT провайдеров  
**Приоритет**: 🔥 Высокий - превышение целевых метрик дублирования
