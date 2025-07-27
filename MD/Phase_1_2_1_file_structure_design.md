# 🏗️ **VOICE_V2 FILE STRUCTURE DESIGN**

**Дата:** 27 июля 2025  
**Фаза:** 1.2.1 - Определение file structure (≤50 файлов)  
**Статус:** ✅ **ЗАВЕРШЕНО**

---

## 🎯 **EXECUTIVE SUMMARY**

Финализирована файловая структура для `voice_v2` системы на основе анализа `app/services/voice` и архитектурных паттернов. Структура оптимизирована для SOLID принципов, maintainability и scalability.

### **Ключевые характеристики:**
- ✅ **50 файлов** - строго в пределах лимита
- ✅ **≤500 строк** на файл - соблюдение code quality стандартов
- ✅ **Логическое группирование** - четкое разделение ответственности
- ✅ **Scalable architecture** - легкое расширение функционала

---

## 📁 **ФИНАЛИЗИРОВАННАЯ СТРУКТУРА (50 ФАЙЛОВ)**

```
app/services/voice_v2/                           # Main voice_v2 package
├── __init__.py                                  # [1] Main API exports (≤100 lines)
│
├── core/                                        # Core components (9 files)
│   ├── __init__.py                              # [2] Core exports (≤50 lines)
│   ├── exceptions.py                            # [3] Voice-specific exceptions (≤150 lines)
│   ├── base.py                                  # [4] Abstract base classes (≤400 lines) 
│   ├── interfaces.py                            # [5] Type definitions, protocols (≤200 lines)
│   ├── orchestrator.py                          # [6] Main orchestrator (≤500 lines)
│   ├── config.py                                # [7] Configuration management (≤350 lines)
│   ├── schemas.py                               # [8] Pydantic schemas (≤250 lines)
│   ├── constants.py                             # [9] Constants and enums (≤100 lines)
│   └── factory.py                               # [10] Central factory (≤300 lines)
│
├── providers/                                   # Provider implementations (14 files)
│   ├── __init__.py                              # [11] Providers exports (≤100 lines)
│   ├── connection_manager.py                    # [12] HTTP client pooling (≤250 lines)
│   ├── stt/                                     # STT providers (6 files)
│   │   ├── __init__.py                          # [13] STT exports (≤50 lines)
│   │   ├── base_stt.py                          # [14] STT base class (≤200 lines)
│   │   ├── openai_stt.py                        # [15] OpenAI STT (≤350 lines)
│   │   ├── google_stt.py                        # [16] Google STT (≤350 lines)
│   │   └── yandex_stt.py                        # [17] Yandex STT (≤400 lines)
│   └── tts/                                     # TTS providers (6 files)
│       ├── __init__.py                          # [18] TTS exports (≤50 lines)
│       ├── base_tts.py                          # [19] TTS base class (≤200 lines)
│       ├── openai_tts.py                        # [20] OpenAI TTS (≤400 lines)
│       ├── google_tts.py                        # [21] Google TTS (≤350 lines)
│       └── yandex_tts.py                        # [22] Yandex TTS (≤400 lines)
│
├── infrastructure/                              # Supporting services (8 files)
│   ├── __init__.py                              # [23] Infrastructure exports (≤50 lines)
│   ├── minio_manager.py                         # [24] MinIO operations (≤400 lines)
│   ├── rate_limiter.py                          # [25] Redis rate limiting (≤250 lines)
│   ├── metrics.py                               # [26] Metrics collection (≤300 lines)
│   ├── cache.py                                 # [27] Caching layer (≤250 lines)
│   ├── circuit_breaker.py                       # [28] Circuit breaker (≤200 lines)
│   ├── health_checker.py                        # [29] Health monitoring (≤200 lines)
│   └── logger.py                                # [30] Structured logging (≤150 lines)
│
├── utils/                                       # Utilities and helpers (7 files)
│   ├── __init__.py                              # [31] Utils exports (≤50 lines)
│   ├── audio.py                                 # [32] Audio processing (≤250 lines)
│   ├── helpers.py                               # [33] Common utilities (≤200 lines)
│   ├── validators.py                            # [34] Validation functions (≤150 lines)
│   ├── converters.py                            # [35] Data converters (≤150 lines)
│   ├── performance.py                           # [36] Performance utilities (≤100 lines)
│   └── async_helpers.py                         # [37] Async utilities (≤150 lines)
│
├── integration/                                 # LangGraph integration (4 files)
│   ├── __init__.py                              # [38] Integration exports (≤50 lines)
│   ├── voice_execution_tool.py                  # [39] LangGraph TTS tool (≤200 lines)
│   ├── agent_interface.py                       # [40] Agent communication (≤150 lines)
│   └── workflow_helpers.py                      # [41] Workflow utilities (≤150 lines)
│
├── migration/                                   # Migration support (4 files)
│   ├── __init__.py                              # [42] Migration exports (≤50 lines)
│   ├── config_migrator.py                       # [43] Config migration (≤200 lines)
│   ├── data_migrator.py                         # [44] Data migration (≤150 lines)
│   └── compatibility.py                         # [45] Backward compatibility (≤150 lines)
│
├── monitoring/                                  # Advanced monitoring (3 files)
│   ├── __init__.py                              # [46] Monitoring exports (≤50 lines)
│   ├── performance_tracker.py                   # [47] Performance monitoring (≤200 lines)
│   └── dashboard.py                             # [48] Metrics dashboard (≤200 lines)
│
└── testing/                                     # Testing utilities (2 files)
    ├── __init__.py                              # [49] Testing exports (≤50 lines)
    └── mocks.py                                 # [50] Mock objects (≤200 lines)
```

**Итого: 50 файлов** ✅

---

## 🎯 **ARCHITECTURAL PRINCIPLES**

### **1. Single Responsibility Principle**

**Каждый файл имеет четкую ответственность:**

| Файл | Ответственность | Размер |
|------|----------------|--------|
| `orchestrator.py` | Координация провайдеров | ≤500 строк |
| `openai_stt.py` | OpenAI STT интеграция | ≤350 строк |
| `metrics.py` | Сбор и агрегация метрик | ≤300 строк |
| `cache.py` | Redis кэширование | ≤250 строк |
| `circuit_breaker.py` | Resilience patterns | ≤200 строк |

### **2. Open/Closed Principle**

**Легкое расширение без модификации:**
- **Новые провайдеры**: Добавление в `providers/stt/` или `providers/tts/`
- **Новые metrics**: Расширение в `infrastructure/metrics.py`
- **Новые tools**: Добавление в `integration/`

### **3. Dependency Inversion**

**Абстракции в core/, реализации в providers/:**
```
core/interfaces.py → базовые интерфейсы
providers/stt/base_stt.py → STT абстракция
providers/stt/openai_stt.py → конкретная реализация
```

---

## 📊 **COMPONENT ORGANIZATION**

### **Core Layer (9 files) - Foundation**
```python
# core/__init__.py - Main exports
from .orchestrator import VoiceServiceOrchestrator
from .config import VoiceConfig
from .schemas import VoiceRequest, VoiceResponse
from .exceptions import VoiceServiceError

# core/factory.py - Central factory
class VoiceServiceFactory:
    @staticmethod
    def create_orchestrator(config: VoiceConfig) -> VoiceServiceOrchestrator
```

### **Providers Layer (14 files) - STT/TTS Implementation**
```python
# providers/__init__.py - Provider exports
from .stt import OpenAISTTProvider, GoogleSTTProvider, YandexSTTProvider
from .tts import OpenAITTSProvider, GoogleTTSProvider, YandexTTSProvider
from .connection_manager import HTTPConnectionManager

# Unified provider interface
class ProviderInterface:
    async def process(self, input_data) -> result
    async def health_check(self) -> bool
```

### **Infrastructure Layer (8 files) - Supporting Services**
```python
# infrastructure/__init__.py - Infrastructure exports
from .cache import VoiceCache
from .metrics import VoiceMetricsCollector
from .circuit_breaker import CircuitBreaker
from .rate_limiter import RateLimiter

# Modular infrastructure services
class InfrastructureManager:
    def __init__(self, cache, metrics, circuit_breaker, rate_limiter)
```

### **Utils Layer (7 files) - Reusable Utilities**
```python
# utils/__init__.py - Utility exports
from .audio import AudioProcessor
from .validators import validate_audio_file
from .converters import convert_to_wav
from .async_helpers import gather_with_timeout

# Pure utility functions
async def process_audio_file(file_path: str) -> ProcessedAudio
```

### **Integration Layer (4 files) - LangGraph Connection**
```python
# integration/__init__.py - Integration exports
from .voice_execution_tool import voice_execution_tool
from .agent_interface import AgentVoiceInterface

# LangGraph tool implementation
@tool
async def voice_execution_tool(
    text: Annotated[str, "Text to synthesize"],
    state: Annotated[Dict, InjectedState] = None
) -> str
```

---

## 🔧 **FILE SIZE OPTIMIZATION**

### **Code Quality Constraints:**

| Category | Max Lines | Rationale |
|----------|-----------|-----------|
| **Core files** | ≤500 | Complex logic, но manageable |
| **Provider files** | ≤400 | Single provider implementation |
| **Infrastructure** | ≤300 | Supporting services |
| **Utils** | ≤250 | Reusable utilities |
| **Integration** | ≤200 | Simple LangGraph tools |
| **Init files** | ≤100 | Only exports |

### **File Size Distribution:**
```
Large files (≤500): 1 file (orchestrator.py)
Medium files (≤400): 6 files (provider implementations)
Standard files (≤300): 43 files (majority)
```

### **CCN (Cyclomatic Complexity) Targets:**
- **Functions**: CCN ≤ 8
- **Classes**: CCN ≤ 15
- **Files**: CCN ≤ 50

---

## 🚀 **SCALABILITY DESIGN**

### **Horizontal Scaling:**
```
providers/
├── stt/
│   ├── base_stt.py
│   ├── openai_stt.py
│   ├── google_stt.py
│   ├── yandex_stt.py
│   └── [NEW_PROVIDER]_stt.py  ← Easy to add
└── tts/
    ├── base_tts.py
    ├── openai_tts.py
    ├── google_tts.py
    ├── yandex_tts.py
    └── [NEW_PROVIDER]_tts.py  ← Easy to add
```

### **Vertical Scaling:**
```
infrastructure/
├── cache.py
├── metrics.py
├── circuit_breaker.py
├── rate_limiter.py
├── health_checker.py
└── [NEW_SERVICE].py  ← Easy to add
```

### **Feature Scaling:**
```
integration/
├── voice_execution_tool.py
├── agent_interface.py
├── workflow_helpers.py
└── [NEW_INTEGRATION].py  ← Easy to add
```

---

## 📋 **IMPLEMENTATION ROADMAP**

### **Phase 1: Core Foundation (9 files)**
1. `core/exceptions.py` - Exception hierarchy
2. `core/interfaces.py` - Base interfaces  
3. `core/constants.py` - Constants and enums
4. `core/schemas.py` - Pydantic models
5. `core/config.py` - Configuration management
6. `core/base.py` - Abstract base classes
7. `core/factory.py` - Central factory
8. `core/orchestrator.py` - Main orchestrator
9. `core/__init__.py` - Core exports

### **Phase 2: Infrastructure Services (8 files)**
1. `infrastructure/logger.py` - Structured logging
2. `infrastructure/cache.py` - Redis caching
3. `infrastructure/metrics.py` - Metrics collection
4. `infrastructure/rate_limiter.py` - Rate limiting
5. `infrastructure/circuit_breaker.py` - Resilience
6. `infrastructure/health_checker.py` - Health monitoring
7. `infrastructure/minio_manager.py` - File storage
8. `infrastructure/__init__.py` - Infrastructure exports

### **Phase 3: Provider Implementation (14 files)**
1. `providers/connection_manager.py` - HTTP pooling
2. `providers/stt/base_stt.py` - STT base class
3. `providers/tts/base_tts.py` - TTS base class
4. STT Providers: OpenAI, Google, Yandex (3 files)
5. TTS Providers: OpenAI, Google, Yandex (3 files)
6. Provider exports (3 files)

### **Phase 4: Supporting Components (19 files)**
1. Utils layer (7 files)
2. Integration layer (4 files)
3. Migration layer (4 files)
4. Monitoring layer (3 files)
5. Testing layer (2 files)

---

## ✅ **QUALITY VALIDATION**

### **File Count Validation:**
```python
# Automated count verification
import os
from pathlib import Path

def count_voice_v2_files():
    voice_v2_path = Path("app/services/voice_v2")
    files = list(voice_v2_path.rglob("*.py"))
    return len(files)

assert count_voice_v2_files() <= 50, "File count exceeds limit"
```

### **File Size Validation:**
```python
def validate_file_sizes():
    for file_path in voice_v2_files:
        with open(file_path) as f:
            lines = len(f.readlines())
            
        max_lines = get_max_lines_for_file(file_path)
        assert lines <= max_lines, f"{file_path} exceeds {max_lines} lines"
```

### **Code Quality Validation:**
```bash
# Automated quality checks
uv run lizard app/services/voice_v2/ --CCN 8
uv run pylint app/services/voice_v2/ --fail-under=9.5
uv run semgrep --config=auto app/services/voice_v2/
```

---

## 🎯 **COMPARISON WITH CURRENT SYSTEMS**

| Metric | app/services/voice | voice_v2 Target | Improvement |
|--------|-------------------|-----------------|-------------|
| **Files** | 15 файлов | 50 файлов | +3.3x structure |
| **Total Lines** | ~5,000 строк | ≤15,000 строк | +3x functionality |
| **Avg Lines/File** | ~333 строки | ≤300 строк | Better organization |
| **Max File Size** | 1,040 строк | ≤500 строк | Improved maintainability |
| **Architecture** | Simple, functional | SOLID, scalable | Enterprise-ready |

---

## ✅ **CHECKLIST UPDATE**

### **Фаза 1.2.1 - Определение file structure**: ✅ **ЗАВЕРШЕНО**
- [x] Использование MD/voice_v2_directory_structure.md как reference ✅
- [x] Логическое группирование компонентов ✅
- [x] Минимизация дублирования кода ✅  
- [x] Scalable структура для enterprise features ✅

### **Следующие шаги:**
1. **Фаза 1.2.2** - SOLID принципы планирование
2. **Фаза 1.2.3** - Dependency injection design
3. **Фаза 1.2.4** - Interface segregation planning

---

## 🎯 **ЗАКЛЮЧЕНИЕ**

Файловая структура `voice_v2` оптимизирована для:

### **Development Excellence:**
- **SOLID principles** - четкое разделение ответственности
- **Code quality** - файлы ≤500 строк, functions ≤50 строк
- **Maintainability** - логическое группирование компонентов
- **Scalability** - легкое добавление новых провайдеров/функций

### **Production Readiness:**
- **Performance** - эффективная организация imports
- **Monitoring** - comprehensive metrics и logging
- **Reliability** - circuit breakers, health checks
- **Testing** - dedicated testing utilities

**Готовность к следующей фазе:** ✅ **READY FOR PHASE 1.2.2**
