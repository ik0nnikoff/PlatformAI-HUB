# LangGraph Audio URL Persistence Fix - Окончательное решение

## 🎯 Проблема

При использовании voice_v2 TTS инструментов в LangGraph агентах, `audio_url` сохранялся в состоянии `MemorySaver` и передавался в следующих сообщениях, вызывая воспроизведение старых голосовых ответов.

### Симптомы
- ✅ TTS tool (`generate_voice_response`) корректно генерирует audio_url
- ✅ Audio URL успешно извлекается из `final_state.values`
- ❌ Audio URL остается в LangGraph состоянии между сообщениями
- ❌ Пользователи получают старые голосовые ответы

## 🔍 Анализ причины

### Архитектура LangGraph State Management
LangGraph использует **каналы (channels)** для управления состоянием:

1. **Каналы с редьюсерами** (например, `messages` с `add_messages`)
   - Новые значения обрабатываются функцией редьюсера
   - Добавляются к существующим значениям

2. **Каналы без редьюсеров** (например, `audio_url`)
   - Новые значения **полностью перезаписывают** существующие
   - Должны быть явно очищены после использования

### Источник проблемы
```python
# voice_v2/tools/tts_tool.py - TTS tool добавляет audio_url в состояние
return Command(update={"audio_url": url})
```

`audio_url` записывается в LangGraph состояние через `Command`, но **не очищается автоматически**, поэтому сохраняется в `MemorySaver` между сообщениями.

## ✅ Правильное решение

### Документация LangGraph: `update_state` API

```python
# Согласно LangGraph документации:
graph.update_state(config, values, as_node=None)

# Поведение для каналов без редьюсеров:
# - Channels без reducers полностью перезаписываются
# - Для удаления поля установить значение None
```

### Корректная реализация в `agent_runner.py`

```python
# ✅ ПРАВИЛЬНЫЙ ПОДХОД - обновляем только audio_url поле
self.agent_app.update_state(
    config=self.config, 
    values={"audio_url": None},  # Очищаем только audio_url
    as_node=None  # Не запускаем последующие nodes
)
```

### ❌ Неправильные подходы (что НЕ работает)

```python
# ❌ Попытка обновить всё состояние
updated_values = final_state.values.copy()
updated_values.pop('audio_url', None)
self.agent_app.update_state(self.config, updated_values)

# ❌ Использование .pop() на всём состоянии
# Это может нарушить другие поля состояния
```

## 🛠️ Полное решение

### Файл: `app/agent_runner/agent_runner.py`

```python
# Extract audio_url from final state if TTS tool was used
try:
    final_state = self.agent_app.get_state(self.config)
    
    if final_state and hasattr(final_state, 'values') and final_state.values:
        state_audio_url = final_state.values.get('audio_url')
        if state_audio_url:
            audio_url = state_audio_url
            self.logger.info(f"✅ Extracted audio_url from final state: {audio_url}")
            
            # ✅ CRITICAL FIX: Clear audio_url from state to prevent persistence
            # According to LangGraph docs: channels without reducers are completely overwritten
            try:
                # Clear only the audio_url field by setting it to None
                # This is the correct way to remove a field from LangGraph state
                self.agent_app.update_state(
                    config=self.config, 
                    values={"audio_url": None},
                    as_node=None  # Don't trigger any subsequent nodes
                )
                self.logger.info("🧹 Cleared audio_url from LangGraph state to prevent persistence")
            except Exception as clear_error:
                self.logger.error(f"Failed to clear audio_url from state: {clear_error}", exc_info=True)
        else:
            self.logger.warning("❌ No audio_url found in final state values")
    else:
        self.logger.warning("❌ Final state or values not available")
except Exception as e:
    self.logger.error(f"Could not extract audio_url from final state: {e}", exc_info=True)
```

## 📊 Логи после исправления

```
✅ Extracted audio_url from final state: http://127.0.0.1:9000/voice-files/...
🧹 Cleared audio_url from LangGraph state to prevent persistence
Including audio_url in response payload: http://127.0.0.1:9000/voice-files/...
Voice response sent successfully to chat 144641834, skipping text message
```

## 🔐 Ключевые принципы

### 1. LangGraph Channel Management
- **Каналы без редьюсеров** должны быть явно очищены
- **MemorySaver** сохраняет всё состояние между thread_id сессиями
- **Временные значения** (как audio_url) требуют manual cleanup

### 2. Правильное использование `update_state`
```python
# ✅ Обновить только конкретное поле
graph.update_state(config, {"field_name": None}, as_node=None)

# ❌ НЕ обновлять всё состояние целиком
# Это может нарушить другие channels и их reducers
```

### 3. Архитектурные соображения
- **Voice V2 интеграция** работает через LangGraph tools
- **TTS решения** принимаются агентом, не execution layer
- **State cleanup** происходит после извлечения audio_url

## 🚀 Результат

После исправления:
- ✅ Голосовые ответы генерируются корректно
- ✅ `audio_url` не персистит между сообщениями  
- ✅ Каждый новый запрос обрабатывается с чистым состоянием
- ✅ MemorySaver продолжает работать для messages и других полей
- ✅ Telegram/WhatsApp интеграции работают стабильно

## 📚 Ссылки

- **LangGraph State Management**: [Persistence Documentation](https://github.com/langchain-ai/langgraph/blob/main/docs/docs/concepts/persistence.md)
- **update_state API**: [LangGraph API Reference](https://github.com/langchain-ai/langgraph/blob/main/docs/docs/concepts/persistence.md#_snippet_14)
- **Channel Types**: [Pregel Channels](https://github.com/langchain-ai/langgraph/blob/main/docs/docs/concepts/pregel.md#_snippet_0)
