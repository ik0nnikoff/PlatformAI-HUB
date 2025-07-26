# 🏗️ **VOICE V2 DIRECTORY STRUCTURE PLAN**

## 📁 **ГРАМОТНАЯ ИЕРАРХИЯ КАТАЛОГОВ (≤50 ФАЙЛОВ)**

### **Общая структура**
```
app/services/voice_v2/                           # Main voice_v2 package
├── __init__.py                                  # [1] Main API exports
├── core/                                        # Core components (8 files)
│   ├── __init__.py                              # [2] Core exports
│   ├── exceptions.py                            # [3] Voice-specific exceptions (≤150 lines)
│   ├── base.py                                  # [4] Abstract base classes (≤400 lines) 
│   ├── interfaces.py                            # [5] Type definitions, protocols (≤200 lines)
│   ├── orchestrator.py                          # [6] Main orchestrator (≤500 lines)
│   ├── config.py                                # [7] Configuration management (≤350 lines)
│   ├── schemas.py                               # [8] Pydantic schemas (≤250 lines)
│   └── constants.py                             # [9] Constants and enums (≤100 lines)
│
├── providers/                                   # Provider implementations (15 files)
│   ├── __init__.py                              # [10] Providers exports
│   ├── factory.py                               # [11] Provider factory (≤300 lines)
│   ├── connection_manager.py                    # [12] Connection pooling (≤250 lines)
│   ├── stt/                                     # STT providers (6 files)
│   │   ├── __init__.py                          # [13] STT exports
│   │   ├── base_stt.py                          # [14] STT base class (≤200 lines)
│   │   ├── openai_stt.py                        # [15] OpenAI STT (≤350 lines)
│   │   ├── google_stt.py                        # [16] Google STT (≤350 lines)
│   │   └── yandex_stt.py                        # [17] Yandex STT (≤400 lines)
│   └── tts/                                     # TTS providers (6 files)
│       ├── __init__.py                          # [18] TTS exports
│       ├── base_tts.py                          # [19] TTS base class (≤200 lines)
│       ├── openai_tts.py                        # [20] OpenAI TTS (≤400 lines)
│       ├── google_tts.py                        # [21] Google TTS (≤350 lines)
│       └── yandex_tts.py                        # [22] Yandex TTS (≤400 lines)
│
├── infrastructure/                              # Supporting services (8 files)
│   ├── __init__.py                              # [23] Infrastructure exports
│   ├── minio_manager.py                         # [24] MinIO operations (≤400 lines)
│   ├── rate_limiter.py                          # [25] Redis rate limiting (≤250 lines)
│   ├── metrics.py                               # [26] Metrics collection (≤300 lines)
│   ├── cache.py                                 # [27] Caching layer (≤250 lines)
│   ├── circuit_breaker.py                       # [28] Circuit breaker (≤200 lines)
│   ├── health_checker.py                        # [29] Health monitoring (≤200 lines)
│   └── logger.py                                # [30] Structured logging (≤150 lines)
│
├── utils/                                       # Utilities and helpers (6 files)
│   ├── __init__.py                              # [31] Utils exports
│   ├── audio.py                                 # [32] Audio processing (≤250 lines)
│   ├── helpers.py                               # [33] Common utilities (≤200 lines)
│   ├── validators.py                            # [34] Validation functions (≤150 lines)
│   ├── converters.py                            # [35] Data converters (≤150 lines)
│   └── performance.py                           # [36] Performance utilities (≤100 lines)
│
├── integration/                                 # LangGraph integration (4 files)
│   ├── __init__.py                              # [37] Integration exports
│   ├── voice_execution_tool.py                  # [38] LangGraph TTS tool (≤200 lines)
│   ├── agent_interface.py                       # [39] Agent communication (≤150 lines)
│   └── workflow_helpers.py                      # [40] Workflow utilities (≤150 lines)
│
├── migration/                                   # Migration support (3 files)
│   ├── __init__.py                              # [41] Migration exports
│   ├── config_import.py                         # [42] Config import from app/services/voice (≤200 lines)
│   └── config_migrator.py                       # [43] Config migration (≤150 lines)
│
├── monitoring/                                  # Advanced monitoring (4 files)
│   ├── __init__.py                              # [44] Monitoring exports
│   ├── performance_tracker.py                   # [45] Performance monitoring (≤200 lines)
│   ├── error_tracker.py                         # [46] Error tracking (≤150 lines)
│   └── dashboard.py                             # [47] Monitoring dashboard (≤200 lines)
│
└── testing/                                     # Testing utilities (3 files)
    ├── __init__.py                              # [48] Testing exports
    ├── fixtures.py                              # [49] Test fixtures (≤200 lines)
    └── mocks.py                                 # [50] Mock objects (≤200 lines)
```

---

## 🎯 **ПРИНЦИПЫ ОРГАНИЗАЦИИ**

### **1. Логическое группирование**
- **core/**: Основные классы и интерфейсы
- **providers/**: STT/TTS провайдеры с подкаталогами
- **infrastructure/**: Supporting сервисы
- **utils/**: Переиспользуемые утилиты
- **integration/**: LangGraph-specific интеграция
- **migration/**: Compatibility и migration helpers
- **monitoring/**: Advanced мониторинг
- **testing/**: Testing utilities

### **2. Минимизация дублирования**
- **base_stt.py/base_tts.py**: Общие base классы для провайдеров
- **helpers.py**: Общие utilities вместо дублирования в каждом файле
- **constants.py**: Централизованные константы
- **validators.py**: Переиспользуемые validation функции

### **3. Scalability**
- **Расширяемость**: Легкое добавление новых провайдеров
- **Модульность**: Каждый компонент изолирован
- **Dependency injection**: Через factory pattern
- **Configuration-driven**: Гибкая настройка через config

### **4. Performance-first**
- **connection_manager.py**: Connection pooling для всех провайдеров
- **cache.py**: Intelligent caching layer
- **performance.py**: Performance measurement utilities
- **circuit_breaker.py**: Resilience patterns

### **5. Production-ready**
- **monitoring/**: Comprehensive мониторинг
- **migration/**: Smooth transition от app/services/voice
- **testing/**: Testing infrastructure
- **error_tracker.py**: Comprehensive error handling

---

## 📊 **SIZE DISTRIBUTION**

### **По каталогам**:
- **core/**: 9 файлов (18% от total)
- **providers/**: 15 файлов (30% от total)
- **infrastructure/**: 8 файлов (16% от total)  
- **utils/**: 6 файлов (12% от total)
- **integration/**: 4 файла (8% от total)
- **migration/**: 3 файла (6% от total)
- **monitoring/**: 4 файла (8% от total)
- **testing/**: 3 файла (6% от total)

### **По строкам кода** (estimated):
- **Общий target**: ≤15,000 строк
- **Средний размер файла**: 300 строк
- **Крупные файлы**: orchestrator (500), providers (350-400)
- **Средние файлы**: infrastructure (200-300)
- **Малые файлы**: utils, testing (100-200)

---

## 🚀 **MIGRATION PATH FROM APP/SERVICES/VOICE**

### **Direct копирование и адаптация**:
```
app/services/voice/base.py                    → core/base.py (adapt)
app/services/voice/voice_orchestrator.py      → core/orchestrator.py (simplify)
app/services/voice/minio_manager.py           → infrastructure/minio_manager.py
app/services/voice/redis_rate_limiter.py      → infrastructure/rate_limiter.py
app/services/voice/voice_metrics.py           → infrastructure/metrics.py
app/services/voice/intent_utils.py            → integration/workflow_helpers.py (parts)
app/services/voice/stt/openai_stt.py          → providers/stt/openai_stt.py
app/services/voice/stt/google_stt.py          → providers/stt/google_stt.py
app/services/voice/stt/yandex_stt.py          → providers/stt/yandex_stt.py
app/services/voice/tts/openai_tts.py          → providers/tts/openai_tts.py
app/services/voice/tts/google_tts.py          → providers/tts/google_tts.py
app/services/voice/tts/yandex_tts.py          → providers/tts/yandex_tts.py
```

### **4. Новые компоненты для enterprise-grade**:
- **circuit_breaker.py**: Resilience patterns
- **performance_tracker.py**: Advanced metrics
- **config_migrator.py**: Migration utilities
- **connection_manager.py**: Connection pooling
- **cache.py**: Performance caching

---

## ✅ **VALIDATION CRITERIA**

### **Structure compliance**:
- [ ] ≤50 файлов total
- [ ] Logical directory hierarchy
- [ ] No code duplication
- [ ] Clear separation of concerns

### **Performance readiness**:
- [ ] Connection pooling infrastructure
- [ ] Caching layer ready
- [ ] Performance monitoring built-in
- [ ] Circuit breaker patterns

### **Production readiness**:
- [ ] Comprehensive monitoring
- [ ] Migration utilities
- [ ] Testing infrastructure
- [ ] Error tracking systems

**ЦЕЛЬ**: Scalable, maintainable, performance-optimized voice система 🎯
