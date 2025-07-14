# 🎙️ Автоматизированная система тестирования голосового workflow

## Обзор системы

Полная автоматизированная система тестирования для проверки функциональности голосового workflow в PlatformAI-HUB. Система включает в себя 4 уровня тестирования:

1. **Workflow Automation Tests** - Автоматизированные тесты workflow
2. **Integration Tests** - Интеграционные тесты всех сервисов  
3. **Performance Tests** - Тесты производительности и нагрузки
4. **Master Test Suite** - Общая система управления тестами

## 📁 Структура файлов

```
├── test_voice_workflow_automation.py   # Основные workflow тесты
├── test_voice_integration.py           # Интеграционные тесты
├── test_voice_performance.py           # Тесты производительности
├── test_voice_master.py                # Мастер-контроллер тестов
└── run_voice_tests.sh                  # Скрипт быстрого запуска
```

## 🚀 Быстрый запуск

### Автоматический запуск
```bash
./run_voice_tests.sh
```

Скрипт предложит выбрать режим тестирования:
- **Quick Test** (30 сек) - только workflow тесты
- **Standard Test** (2 мин) - workflow + интеграция  
- **Full Test Suite** (5 мин) - все тесты
- **Performance Only** (3 мин) - только производительность

### Ручной запуск
```bash
# Все тесты
python3 test_voice_master.py --tests all

# Отдельные типы
python3 test_voice_master.py --tests workflow
python3 test_voice_master.py --tests integration performance

# Индивидуальные тесты
python3 test_voice_workflow_automation.py
python3 test_voice_integration.py
python3 test_voice_performance.py
```

## 📋 Типы тестов

### 1. Workflow Automation Tests (`test_voice_workflow_automation.py`)

**Цель**: Проверка корректности работы голосового workflow

**Включает**:
- ✅ Тестирование детекции голосовых намерений
- ✅ Mock-тестирование взаимодействия с агентом
- ✅ Проверка real voice orchestrator
- ✅ Загрузка конфигурации агента

**Тестовые сообщения**:
```python
test_messages = [
    "Отвечай голосом. Сколько времени сейчас?",  # voice intent
    "Скажи мне текущее время",                   # voice intent  
    "Произнеси ответ вслух",                     # voice intent
    "Просто текстовый ответ",                    # no intent
    "Какая погода сегодня?"                      # no intent
]
```

**Результат**: JSON отчет с детальной статистикой

### 2. Integration Tests (`test_voice_integration.py`)

**Цель**: Проверка интеграции всех сервисов

**Включает**:
- 🏥 Health endpoints всех сервисов
- ⚙️ API конфигурации агента
- 🎵 TTS синтез через API
- 💬 Полный голосовой чат с агентом
- 🔴 Redis интеграция

**Тестовые сценарии**:
```python
test_cases = [
    {
        "message": "Отвечай голосом. Расскажи о страйкболе кратко.",
        "expect_voice": True
    },
    {
        "message": "Что такое тактическая игра?", 
        "expect_voice": False
    },
    {
        "message": "Скажи мне о правилах безопасности.",
        "expect_voice": True
    }
]
```

### 3. Performance Tests (`test_voice_performance.py`)

**Цель**: Проверка производительности и стабильности под нагрузкой

**Включает**:
- 🔥 Stress Test - конкурентные запросы (10 пользователей × 5 запросов)
- 📈 Load Test - устойчивый трафик (2 RPS на 60 секунд)
- 🧠 Memory Leak Test - 100 итераций для проверки утечек памяти

**Метрики**:
```python
@dataclass
class PerformanceMetrics:
    total_requests: int
    successful_requests: int
    avg_response_time: float
    p95_response_time: float
    throughput: float  # RPS
    error_rate: float
```

### 4. Master Test Suite (`test_voice_master.py`)

**Цель**: Управление всеми типами тестов и генерация сводного отчета

**Функции**:
- 🎯 Последовательный запуск всех тестов
- 📊 Генерация сводной статистики
- 💾 Сохранение результатов в JSON
- 📈 Красивый финальный отчет

## 📊 Результаты тестирования

### Файлы результатов
```
voice_workflow_test_results.json         # Workflow тесты
voice_integration_test_results.json      # Интеграционные тесты  
voice_performance_test_results.json      # Тесты производительности
voice_testing_master_results_*.json      # Сводный отчет
voice_testing_master.log                 # Логи выполнения
```

### Пример сводного отчета
```
🎯 VOICE WORKFLOW TESTING - FINAL REPORT
========================================

📋 Session ID: test_session_1737000000
⏰ Start Time: 2025-01-15T00:47:00
🏁 End Time: 2025-01-15T00:52:30

📊 OVERALL SUMMARY:
   Test Types Run: 3/3
   Failed Test Types: 0
   Overall Success: ✅ YES

📈 DETAILED STATISTICS:

   WORKFLOW:
     Total Tests: 7
     Passed Tests: 7
     Success Rate: 100.0%

   INTEGRATION:
     Total Tests: 5
     Passed Tests: 5
     Success Rate: 100.0%

   PERFORMANCE:
     Stress Test: ✅
     Load Test: ✅
     Memory Test: ✅
```

## 🔧 Конфигурация

### Настройки тестирования
```python
# test_voice_master.py
test_types = ["workflow", "integration", "performance"]
agent_id = "agent_airsoft_0faa9616"
base_url = "http://localhost:8001"

# Производительность
concurrent_users = 10
requests_per_user = 5
load_test_duration = 60  # секунд
target_rps = 2.0
```

### Mock объекты
```python
class MockTelegramBot:
    """Mock для тестирования Telegram интеграции"""
    
    async def send_message(self, chat_id, text, **kwargs):
        # Логирование без реальной отправки
    
    async def send_voice(self, chat_id, voice, caption, **kwargs):
        # Эмуляция отправки голосового сообщения
```

## 🎯 Сценарии использования

### Разработка
```bash
# Быстрая проверка после изменений
./run_voice_tests.sh  # выбрать "1" для Quick Test
```

### CI/CD Pipeline
```bash
# Полная проверка перед деплоем
python3 test_voice_master.py --tests all
if [ $? -eq 0 ]; then
    echo "✅ Ready for deployment"
else
    echo "❌ Fix issues before deploy"
    exit 1
fi
```

### Мониторинг производительности
```bash
# Регулярные проверки производительности
python3 test_voice_master.py --tests performance
```

### Отладка проблем
```bash
# Детальная диагностика
python3 test_voice_integration.py  # Проверка каждого сервиса
python3 test_voice_workflow_automation.py  # Детальный workflow
```

## 🐛 Диагностика ошибок

### Типичные проблемы

**Сервер не запущен**
```
❌ Server is not running at http://localhost:8001
```
**Решение**: `make run`

**Проблемы с зависимостями**
```
❌ Missing dependencies
```
**Решение**: `pip install aiohttp asyncio`

**Проблемы с Redis**
```
❌ Redis integration failed
```
**Решение**: Проверить Redis сервис

**Проблемы с MinIO**
```
❌ MinIO health check failed
```
**Решение**: Проверить MinIO сервис и конфигурацию

### Анализ логов
```bash
# Детальные логи
tail -f voice_testing_master.log

# Ошибки из результатов
jq '.tests[] | select(.status == "failed") | .error' voice_testing_master_results_*.json
```

## 📈 Метрики успеха

### Критерии прохождения тестов

**Workflow Tests**:
- ✅ 100% детекция голосовых намерений
- ✅ Успешная инициализация voice orchestrator
- ✅ Загрузка конфигурации агента

**Integration Tests**:
- ✅ Все health endpoints отвечают
- ✅ TTS синтез работает
- ✅ Скачивание аудио файлов
- ✅ Соответствие ожиданий по голосовым ответам

**Performance Tests**:
- ✅ Успешность > 95%
- ✅ Среднее время ответа < 5 секунд
- ✅ P95 время ответа < 10 секунд
- ✅ Стабильность при нагрузке

## 🔄 Автоматизация и CI/CD

### GitHub Actions пример
```yaml
name: Voice Workflow Tests
on: [push, pull_request]

jobs:
  voice-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Setup Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.9'
      
      - name: Install dependencies
        run: pip install -r requirements.txt
      
      - name: Start services
        run: make run &
        
      - name: Wait for services
        run: sleep 30
      
      - name: Run voice tests
        run: python3 test_voice_master.py --tests all
```

### Мониторинг
```bash
# Crontab для регулярного тестирования
0 */6 * * * cd /path/to/project && python3 test_voice_master.py --tests integration >> voice_monitoring.log 2>&1
```

## 📚 Дополнительные ресурсы

- **Архитектура**: `ARCHITECTURE_NOTES.md`
- **Конфигурация голоса**: `airsoft_agent_with_voice.json`
- **Voice services**: `app/services/voice/`
- **Agent runner**: `app/agent_runner/agent_runner.py`

---

*Автоматизированная система тестирования обеспечивает полную проверку голосового workflow от детекции намерений до доставки голосовых сообщений в Telegram.*
