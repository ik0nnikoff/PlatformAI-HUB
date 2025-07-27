# Phase 1.2.4 - LangGraph Integration Planning для voice_v2

## 📊 Общий обзор

**Фаза**: 1.2.4  
**Дата выполнения**: 2024-12-31  
**Статус**: ✅ ЗАВЕРШЕНА  

## 🎯 Цели этапа

1. Планирование архитектуры voice_v2 ↔ LangGraph интеграции
2. Проектирование voice intent detection through LangGraph
3. Определение voice tools для LangGraph workflow
4. Создание clean API между voice_v2 и LangGraph

## 🏗️ Архитектурная концепция

### Принцип разделения ответственности

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│   LangGraph     │    │   voice_v2       │    │   External APIs     │
│   Agent         │    │   Orchestrator   │    │   (OpenAI/Google)   │
├─────────────────┤    ├──────────────────┤    ├─────────────────────┤
│ Voice Decisions │────│ Voice Execution  │────│ STT/TTS Processing  │
│ Intent Analysis │    │ Provider Chain   │    │ Audio Conversion    │
│ Context Memory  │    │ Performance Opt  │    │ File Storage        │
│ Workflow Logic  │    │ Error Handling   │    │ Rate Limiting       │
└─────────────────┘    └──────────────────┘    └─────────────────────┘
```

**Разделение ответственности**:
- **LangGraph Agent**: Принимает все решения о voice responses
- **Voice_v2 Orchestrator**: Только execution layer без decision making
- **Clean API**: Четкое разделение через tool interfaces

## 🧠 Voice Intent Detection Architecture

### 1. LangGraph-Based Intent Detection

```python
# app/services/voice_v2/integration/intent_detection.py
class VoiceIntentNode:
    """LangGraph node для анализа voice intent"""
    
    @staticmethod
    async def analyze_voice_intent(state: VoiceAgentState) -> Dict[str, Any]:
        """Анализ намерений пользователя для voice ответа"""
        
        # Получаем контекст сообщения
        last_message = state["messages"][-1]
        user_data = state.get("user_data", {})
        voice_settings = state.get("voice_settings", {})
        
        # LLM анализ намерений
        intent_prompt = f"""
        Проанализируй сообщение пользователя и определи:
        1. Нужен ли голосовой ответ? (да/нет и почему)
        2. Тип voice response: conversational/informational/emotional
        3. Preferred voice style: natural/professional/friendly
        4. Estimated response length: short/medium/long
        
        Сообщение: {last_message.content}
        Контекст: {user_data}
        """
        
        # LLM call через agent context
        intent_analysis = await state["llm"].ainvoke([
            SystemMessage(content=intent_prompt),
            last_message
        ])
        
        # Парсинг намерений
        voice_intent = VoiceIntentParser.parse_intent(intent_analysis.content)
        
        return {
            "voice_intent": voice_intent,
            "should_respond_voice": voice_intent.get("needs_voice_response", False),
            "voice_style": voice_intent.get("voice_style", "natural"),
            "response_length": voice_intent.get("response_length", "medium")
        }
```

### 2. Conditional Edge для Voice Decision

```python
# app/services/voice_v2/integration/voice_workflow.py
def should_use_voice_response(state: VoiceAgentState) -> str:
    """Conditional edge function для voice decision"""
    
    voice_intent = state.get("voice_intent", {})
    user_preferences = state.get("user_data", {}).get("voice_preferences", {})
    
    # Факторы для voice decision
    factors = {
        "user_requested_voice": voice_intent.get("explicit_voice_request", False),
        "emotional_content": voice_intent.get("emotional_score", 0) > 0.7,
        "user_enabled_voice": user_preferences.get("voice_enabled", True),
        "appropriate_context": voice_intent.get("context_appropriate", True),
        "short_response": voice_intent.get("response_length", "medium") == "short"
    }
    
    # Decision logic
    if factors["user_requested_voice"] or (
        factors["emotional_content"] and 
        factors["user_enabled_voice"] and 
        factors["appropriate_context"]
    ):
        return "voice_synthesis_node"
    else:
        return "text_response_node"
```

### 3. Voice Tools Integration

```python
# app/services/voice_v2/integration/voice_tools.py
class VoiceLangGraphTools:
    """High-performance voice tools для LangGraph"""
    
    @tool
    async def check_voice_capability(
        user_id: Annotated[str, "User ID для проверки настроек"],
        context: Annotated[Dict, "Message context"],
        state: Annotated[Dict, InjectedState] = None
    ) -> Dict[str, Any]:
        """Проверка возможности voice response для пользователя"""
        
        orchestrator = await VoiceOrchestrator.get_instance()
        
        # Получаем user voice settings
        user_settings = await orchestrator.user_settings_manager.get_voice_settings(user_id)
        
        # Проверяем доступность провайдеров
        available_providers = await orchestrator.provider_manager.check_availability()
        
        return {
            "voice_enabled": user_settings.get("enabled", True),
            "preferred_language": user_settings.get("language", "ru"),
            "voice_style": user_settings.get("style", "natural"),
            "available_providers": available_providers,
            "can_synthesize": len(available_providers) > 0
        }
    
    @tool
    async def synthesize_voice_response(
        text: Annotated[str, "Текст для синтеза речи"],
        voice_config: Annotated[Dict, "Конфигурация голоса"],
        state: Annotated[Dict, InjectedState] = None
    ) -> Dict[str, Any]:
        """Синтез voice response через voice_v2 orchestrator"""
        
        orchestrator = await VoiceOrchestrator.get_instance()
        
        # Performance metrics start
        start_time = time.time()
        
        try:
            # Синтез через orchestrator
            audio_result = await orchestrator.synthesize_speech(
                text=text,
                language=voice_config.get("language", "ru"),
                voice_style=voice_config.get("style", "natural"),
                speed=voice_config.get("speed", 1.0)
            )
            
            synthesis_time = (time.time() - start_time) * 1000
            
            # Сохраняем в MinIO
            audio_url = await orchestrator.file_manager.upload_audio(
                audio_data=audio_result.audio_data,
                format=audio_result.format,
                duration=audio_result.duration
            )
            
            # Metrics recording
            await orchestrator.metrics_collector.record_synthesis(
                provider=audio_result.provider,
                duration=synthesis_time,
                text_length=len(text),
                success=True
            )
            
            return {
                "success": True,
                "audio_url": audio_url,
                "format": audio_result.format,
                "duration": audio_result.duration,
                "provider": audio_result.provider,
                "synthesis_time_ms": synthesis_time
            }
            
        except Exception as e:
            await orchestrator.metrics_collector.record_synthesis_error(
                error_type=type(e).__name__,
                duration=(time.time() - start_time) * 1000
            )
            raise
    
    @tool
    async def transcribe_voice_message(
        audio_data: Annotated[bytes, "Audio data для transcription"],
        language: Annotated[str, "Language code"] = "auto",
        state: Annotated[Dict, InjectedState] = None
    ) -> Dict[str, Any]:
        """Transcription voice message через voice_v2 orchestrator"""
        
        orchestrator = await VoiceOrchestrator.get_instance()
        
        # Используем cached transcription если available
        audio_hash = hashlib.md5(audio_data).hexdigest()
        cache_key = f"voice_v2:stt:{audio_hash}:{language}"
        
        cached_result = await orchestrator.cache_manager.get(cache_key)
        if cached_result:
            return cached_result
        
        # Transcription через orchestrator
        start_time = time.time()
        
        try:
            transcription_result = await orchestrator.transcribe_audio(
                audio_data=audio_data,
                language=language,
                performance_mode=True
            )
            
            result = {
                "success": True,
                "text": transcription_result.text,
                "language": transcription_result.detected_language,
                "confidence": transcription_result.confidence,
                "provider": transcription_result.provider,
                "duration_ms": (time.time() - start_time) * 1000
            }
            
            # Cache результат
            await orchestrator.cache_manager.set(
                cache_key, result, ttl=86400  # 24 hours
            )
            
            return result
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }
```

## 🔄 Voice Workflow Design

### LangGraph Voice-Enabled Workflow

```python
# app/services/voice_v2/integration/voice_workflow.py
class VoiceEnabledWorkflow:
    """LangGraph workflow с voice capabilities"""
    
    @classmethod
    def create_voice_workflow(cls) -> StateGraph:
        """Создание LangGraph workflow с voice support"""
        
        # State definition
        class VoiceAgentState(TypedDict):
            messages: Annotated[List[BaseMessage], add_messages]
            user_data: Dict[str, Any]
            voice_settings: Dict[str, Any]
            voice_intent: Optional[Dict[str, Any]]
            audio_data: Optional[bytes]
            should_respond_voice: bool
            voice_response_url: Optional[str]
        
        workflow = StateGraph(VoiceAgentState)
        
        # Nodes
        workflow.add_node("intent_analysis", VoiceIntentNode.analyze_voice_intent)
        workflow.add_node("chatbot", chatbot_node)
        workflow.add_node("voice_synthesis", voice_synthesis_node)
        workflow.add_node("tools", ToolNode(tools=get_voice_tools()))
        
        # Edges
        workflow.set_entry_point("intent_analysis")
        workflow.add_edge("intent_analysis", "chatbot")
        
        # Conditional edges
        workflow.add_conditional_edges(
            "chatbot",
            tools_condition,
            {
                "tools": "tools",
                "continue": "voice_decision"
            }
        )
        
        workflow.add_conditional_edges(
            "voice_decision",
            should_use_voice_response,
            {
                "voice_synthesis_node": "voice_synthesis",
                "text_response_node": END
            }
        )
        
        workflow.add_edge("tools", "chatbot")
        workflow.add_edge("voice_synthesis", END)
        
        return workflow
    
    @staticmethod
    async def voice_synthesis_node(state: VoiceAgentState) -> Dict[str, Any]:
        """Node для синтеза voice response"""
        
        # Получаем последний response от chatbot
        last_message = state["messages"][-1]
        voice_settings = state["voice_settings"]
        
        # Вызываем voice synthesis tool
        voice_tools = VoiceLangGraphTools()
        synthesis_result = await voice_tools.synthesize_voice_response(
            text=last_message.content,
            voice_config=voice_settings,
            state=state
        )
        
        if synthesis_result["success"]:
            return {
                "voice_response_url": synthesis_result["audio_url"],
                "voice_synthesis_metrics": {
                    "duration_ms": synthesis_result["synthesis_time_ms"],
                    "provider": synthesis_result["provider"],
                    "format": synthesis_result["format"]
                }
            }
        else:
            # Fallback to text response при ошибке synthesis
            return {"voice_response_url": None}
```

## 🎛️ Configuration Management

### Voice Settings в Agent Config

```python
# app/services/voice_v2/integration/config_manager.py
class VoiceAgentConfigManager:
    """Управление voice настройками в agent config"""
    
    @staticmethod
    def get_voice_config(agent_config: Dict) -> Dict[str, Any]:
        """Извлечение voice настроек из agent config"""
        
        voice_settings = agent_config.get("config", {}).get("simple", {}).get("settings", {}).get("voice_settings", {})
        
        return {
            "enabled": voice_settings.get("enabled", False),
            "providers": voice_settings.get("providers", []),
            "default_language": voice_settings.get("default_language", "ru"),
            "synthesis_settings": {
                "speed": voice_settings.get("speed", 1.0),
                "voice_style": voice_settings.get("voice_style", "natural"),
                "quality": voice_settings.get("quality", "standard")
            },
            "transcription_settings": {
                "language_detection": voice_settings.get("auto_language", True),
                "confidence_threshold": voice_settings.get("confidence_threshold", 0.8)
            }
        }
    
    @staticmethod
    def validate_voice_config(voice_config: Dict) -> bool:
        """Валидация voice конфигурации"""
        
        required_fields = ["enabled", "providers"]
        for field in required_fields:
            if field not in voice_config:
                return False
        
        # Проверяем providers format
        providers = voice_config["providers"]
        if not isinstance(providers, list) or len(providers) == 0:
            return False
        
        for provider in providers:
            if not all(key in provider for key in ["provider", "priority", "enabled"]):
                return False
        
        return True
```

## 🔌 Clean API Design

### Voice_v2 ↔ LangGraph Interface

```python
# app/services/voice_v2/integration/api_interface.py
class VoiceLangGraphInterface:
    """Clean API между voice_v2 и LangGraph"""
    
    def __init__(self, orchestrator: VoiceOrchestrator):
        self.orchestrator = orchestrator
    
    async def process_voice_input(
        self, 
        audio_data: bytes, 
        user_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process voice input для LangGraph"""
        
        try:
            # Transcription
            transcription = await self.orchestrator.transcribe_audio(
                audio_data=audio_data,
                language=user_context.get("language", "auto")
            )
            
            return {
                "success": True,
                "transcription": transcription.text,
                "confidence": transcription.confidence,
                "detected_language": transcription.detected_language,
                "processing_time_ms": transcription.duration_ms
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }
    
    async def generate_voice_output(
        self, 
        text: str, 
        voice_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate voice output для LangGraph"""
        
        try:
            # Synthesis
            audio_result = await self.orchestrator.synthesize_speech(
                text=text,
                language=voice_config.get("language", "ru"),
                voice_style=voice_config.get("style", "natural"),
                speed=voice_config.get("speed", 1.0)
            )
            
            # Upload to MinIO
            audio_url = await self.orchestrator.file_manager.upload_audio(
                audio_data=audio_result.audio_data,
                format=audio_result.format
            )
            
            return {
                "success": True,
                "audio_url": audio_url,
                "format": audio_result.format,
                "duration_seconds": audio_result.duration,
                "provider_used": audio_result.provider
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }
    
    async def check_voice_capabilities(self, user_id: str) -> Dict[str, Any]:
        """Проверка voice capabilities для пользователя"""
        
        # User settings
        user_settings = await self.orchestrator.user_settings_manager.get_voice_settings(user_id)
        
        # Provider availability
        available_providers = await self.orchestrator.provider_manager.check_availability()
        
        return {
            "voice_enabled": user_settings.get("enabled", True),
            "available_languages": user_settings.get("languages", ["ru", "en"]),
            "available_providers": {
                "stt": [p for p in available_providers if p.supports_stt],
                "tts": [p for p in available_providers if p.supports_tts]
            },
            "quality_settings": user_settings.get("quality", "standard")
        }
```

## 📋 Implementation Roadmap

### Phase 2.1 Integration Components

1. **VoiceIntentNode** - LangGraph node для intent анализа
2. **VoiceLangGraphTools** - Optimized tools для voice operations
3. **VoiceEnabledWorkflow** - Complete workflow с voice support
4. **VoiceLangGraphInterface** - Clean API layer

### Phase 2.2 Advanced Features

1. **Voice Memory Management** - Persistent voice preferences
2. **Context-Aware Voice** - Адаптация к контексту разговора
3. **Multi-Language Support** - Seamless language switching
4. **Performance Monitoring** - Real-time voice metrics

### Testing Strategy

1. **Unit Tests** - Каждый voice tool и node
2. **Integration Tests** - Full LangGraph workflow tests
3. **Performance Tests** - Voice latency benchmarks
4. **User Experience Tests** - End-to-end voice scenarios

## 📊 Success Metrics

### Performance Targets

| Метрика | Target | Измерение |
|---------|--------|-----------|
| Voice Intent Analysis | ≤100ms | LangGraph node execution |
| Tool Execution Overhead | ≤10ms | Per voice tool call |
| Workflow Latency | ≤50ms | Voice decision to synthesis |
| Memory Usage | ≤50MB | Peak per voice session |

### Quality Metrics

| Метрика | Target | Описание |
|---------|--------|----------|
| Intent Accuracy | ≥90% | Correct voice/text decisions |
| User Satisfaction | ≥4.5/5 | Voice response quality |
| Error Rate | ≤2% | Failed voice operations |
| Test Coverage | 100% | All voice integration code |

## 🎯 Заключение

**Архитектурные принципы voice_v2 ↔ LangGraph интеграции**:

1. **Clear Separation**: LangGraph = decisions, voice_v2 = execution
2. **Performance First**: Minimal latency, smart caching, async everywhere
3. **Tool-Based Integration**: Clean API через LangGraph tools
4. **Context Awareness**: Voice decisions на основе conversation context
5. **Fallback Resilience**: Graceful degradation при voice failures

**Следующий этап**: Phase 1.3.1 - Архитектурный review и валидация

---

**Статус**: ✅ LangGraph integration planning завершено  
**Готовность к реализации**: 100%
