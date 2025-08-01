# Phase 4.1.4 - Decision Logic Extraction Analysis Report

**Дата создания**: 30 июля 2025 г.
**Фаза**: 4.1.4 - Decision logic extraction из текущей voice системы
**Статус**: ✅ ЗАВЕРШЕНО

## 📋 Задача
Проанализировать и задокументировать текущую логику принятия решений в voice системе для последующего переноса в LangGraph agent tools.

## 🔍 Current Decision Logic Analysis

### 1. Voice Intent Detection Logic

#### Местоположение: `app/services/voice/intent_utils.py`

#### VoiceIntentDetector Class:
```python
class VoiceIntentDetector:
    def detect_tts_intent(self, text: str, intent_keywords: List[str]) -> bool:
        """
        Определить, нужно ли озвучивать ответ на основе ключевых слов
        
        LOGIC:
        1. text.lower() - приведение к нижнему регистру
        2. Для каждого keyword в intent_keywords:
           - keyword.lower() 
           - pattern = r'\b' + re.escape(keyword_lower) + r'\b'
           - re.search(pattern, text_lower) - поиск целых слов
        3. Return True если найдено любое ключевое слово
        """
```

#### Проблемы текущей реализации:
- ❌ **Primitive Pattern Matching**: Только regex поиск по ключевым словам
- ❌ **No Context Awareness**: Не учитывает контекст сообщения, историю чата
- ❌ **Static Rules**: Hardcoded logic без машинного обучения
- ❌ **No User Preferences**: Не учитывает пользовательские настройки и поведение
- ❌ **Wrong Architecture Layer**: Decision logic в utility layer, а не в agent

### 2. Voice Settings Processing Logic

#### Местоположение: `app/api/schemas/voice_schemas.py`

#### VoiceSettings Decision Methods:
```python
class VoiceSettings(BaseModel):
    auto_stt: bool = True                    # ✅ Simple boolean decision
    auto_tts_on_keywords: bool = True        # ❌ Static rule-based decision
    intent_keywords: List[str] = [           # ❌ Hardcoded keyword list
        "голос", "скажи", "произнеси", "озвучь"
    ]
    intent_detection_mode: IntentDetectionMode = KEYWORDS  # ❌ Static mode
    
    def should_process_voice_intent(self, text: str) -> bool:
        """
        DECISION LOGIC:
        - ALWAYS mode: return True  
        - DISABLED mode: return False
        - KEYWORDS mode: check if any intent_keywords in text
        
        PROBLEMS:
        ❌ No agent context
        ❌ No user history consideration  
        ❌ No dynamic keyword expansion
        ❌ No confidence scoring
        """
```

#### IntentDetectionMode Analysis:
```python
class IntentDetectionMode(str, Enum):
    KEYWORDS = "keywords"  # ❌ Primitive keyword matching
    ALWAYS = "always"      # ❌ No intelligence, always TTS
    DISABLED = "disabled"  # ❌ No flexibility
    
# MISSING MODES:
# SMART = "smart"        # ✅ NEEDED: AI-powered intent detection  
# CONTEXTUAL = "contextual" # ✅ NEEDED: Based on conversation history
# USER_ADAPTIVE = "adaptive" # ✅ NEEDED: Learns from user behavior
```

### 3. Agent Response Processing Logic

#### Местоположение: `app/services/voice/intent_utils.py`

#### AgentResponseProcessor Class:
```python
class AgentResponseProcessor:
    async def process_agent_response(self, agent_response: str, user_message: str, 
                                   agent_config: Dict, user_id: str, 
                                   platform: str) -> Dict[str, Any]:
        """
        CURRENT DECISION FLOW:
        1. Extract voice_settings from agent_config
        2. Check should_auto_tts_response() -> keyword detection
        3. Get primary TTS provider (lowest priority number)
        4. Get TTS config for provider
        5. Generate TTS audio if all conditions met
        
        PROBLEMS:
        ❌ No agent memory access
        ❌ No conversation context consideration
        ❌ No user behavior learning
        ❌ No dynamic provider selection logic
        ❌ Hardcoded decision tree
        """
```

#### TTS Provider Selection Logic:
```python
def get_primary_tts_provider(self, voice_settings: Dict[str, Any]) -> Optional[str]:
    """
    CURRENT LOGIC:
    1. Filter providers where tts_config.enabled = True
    2. Sort by priority (1 = highest priority)
    3. Return first provider
    
    PROBLEMS:
    ❌ No failure awareness (circuit breaker state)
    ❌ No performance-based selection
    ❌ No user preference consideration
    ❌ No cost optimization
    ❌ No quality vs speed tradeoffs
    """
```

### 4. Agent Runner TTS Decision Logic

#### Местоположение: `app/agent_runner/agent_runner.py`

#### Current TTS Processing:
```python
async def _process_response_with_tts(self, response_content: str, user_message: str, 
                                   chat_id: str, channel: str) -> Optional[str]:
    """
    NOTE: Intent detection НЕ ВЫПОЛНЯЕТСЯ здесь - это задача LangGraph агента
    Метод только выполняет TTS synthesis без принятия решений
    
    CURRENT ARCHITECTURE PROBLEM:
    ❌ Pure execution layer making NO decisions
    ❌ Should be agent's responsibility to decide TTS
    ❌ No access to agent state, memory, context
    ❌ Static agent_config without dynamic updates
    """
```

### 5. Voice_v2 Schema Decision Fields

#### Местоположение: `app/services/voice_v2/core/schemas.py`

#### VoiceSettings Schema:
```python
class VoiceSettings(BaseModel):
    enabled: bool = False
    auto_stt: bool = True                    # ✅ Simple execution decision
    auto_tts_on_keywords: bool = False       # ❌ Keyword-based decision
    intent_keywords: List[str] = []          # ❌ Static keyword list
    providers: List[Dict[str, Any]] = []     # ❌ Static provider config
    
    # MISSING DECISION FIELDS:
    # tts_mode: TTSMode                      # ✅ NEEDED: auto, manual, smart
    # confidence_threshold: float            # ✅ NEEDED: minimum confidence for TTS
    # user_preference_weight: float          # ✅ NEEDED: how much to consider user habits
    # context_window_size: int               # ✅ NEEDED: how many messages to consider
```

## 🎯 Decision Logic Patterns Analysis

### 1. Voice Intent Detection Patterns:

#### Current Pattern (Primitive):
```python
# ❌ CURRENT: Static keyword matching
def detect_intent(text: str, keywords: List[str]) -> bool:
    text_lower = text.lower()
    return any(keyword.lower() in text_lower for keyword in keywords)

# ✅ NEEDED: LangGraph agent-based detection
@tool("detect_voice_intent")
def detect_voice_intent(state: AgentState) -> Dict[str, Any]:
    """
    Intelligent voice intent detection using agent context
    
    DECISION FACTORS:
    - Message semantic analysis
    - Conversation history 
    - User behavior patterns
    - Current agent task context
    - Time of day, platform, user state
    """
```

#### Required LangGraph Pattern:
```python
# Agent state-aware intent detection
class VoiceIntentTool:
    def should_process_voice_response(self, state: AgentState) -> VoiceIntentResult:
        """
        INTELLIGENT DECISION MAKING:
        1. Semantic Analysis: понимание смысла сообщения
        2. Context Analysis: последние N сообщений в чате
        3. User Pattern Analysis: предпочтения пользователя
        4. Task Context: текущая задача агента
        5. Platform Context: Telegram vs WhatsApp capabilities
        6. Time/Environment: время дня, нагрузка системы
        
        RETURN: VoiceIntentResult(
            should_tts=bool,
            confidence=float,
            reasoning=str,
            provider_preference=Optional[str]
        )
        """
```

### 2. Provider Selection Patterns:

#### Current Pattern (Static):
```python
# ❌ CURRENT: Priority-only selection
providers = sorted(providers, key=lambda x: x.priority)
return providers[0].provider

# ✅ NEEDED: Dynamic intelligent selection
@tool("select_voice_provider")
def select_voice_provider(state: AgentState, operation: str) -> ProviderSelection:
    """
    DECISION FACTORS:
    - Provider health status (circuit breaker)
    - Response quality history  
    - Processing speed requirements
    - Cost optimization
    - User preference patterns
    - Current system load
    """
```

### 3. TTS Execution Decision Patterns:

#### Current Pattern (Outside Agent):
```python
# ❌ CURRENT: AgentRunner decides TTS (wrong layer)
audio_url = await self._process_response_with_tts(...)

# ✅ NEEDED: Agent decides, tool executes
class TTSExecutionTool:
    def execute_tts(self, state: AgentState, text: str, 
                   provider: str, config: Dict) -> TTSResult:
        """
        PURE EXECUTION TOOL:
        - No decision making
        - Agent already decided: text, provider, config
        - Just execute TTS synthesis
        - Return success/failure + audio URL
        """
```

## 📊 Current Decision Points Inventory

### ✅ Simple Decisions (Keep):
1. **auto_stt**: Boolean - process voice messages automatically
2. **enabled**: Boolean - voice features on/off  
3. **cache_enabled**: Boolean - use caching
4. **max_file_size_mb**: Integer - file size limits

### ❌ Complex Decisions (Move to LangGraph):
1. **Voice Intent Detection**: 
   - Current: keyword matching
   - Needed: semantic analysis + context
   
2. **TTS Response Decision**:
   - Current: keyword-based auto_tts_on_keywords
   - Needed: conversation context + user patterns
   
3. **Provider Selection**:
   - Current: static priority
   - Needed: dynamic health/performance/cost selection
   
4. **Voice Configuration**:
   - Current: static agent_config
   - Needed: dynamic runtime configuration based on context

### 🔄 Decision Migration Map:

#### FROM voice/intent_utils.py TO LangGraph Tools:
```python
# ❌ OLD LOCATION: voice/intent_utils.py
VoiceIntentDetector.detect_tts_intent()
VoiceIntentDetector.should_auto_tts_response()
VoiceIntentDetector.get_primary_tts_provider()

# ✅ NEW LOCATION: LangGraph agent tools
@tool("analyze_voice_intent") 
def analyze_voice_intent(state: AgentState) -> VoiceIntentAnalysis

@tool("decide_tts_response")
def decide_tts_response(state: AgentState) -> TTSDecision

@tool("select_optimal_provider")  
def select_optimal_provider(state: AgentState, operation: str) -> ProviderChoice
```

#### FROM agent_runner.py TO LangGraph Agent:
```python
# ❌ OLD: AgentRunner decides TTS
await self._process_response_with_tts(...)

# ✅ NEW: Agent node decides TTS in workflow
def agent_voice_decision_node(state: AgentState) -> AgentState:
    """Agent decides voice response strategy"""
    if should_include_voice_response(state):
        state["voice_response_mode"] = "tts" 
        state["voice_provider"] = select_provider(state)
    return state
```

## 🎯 Required AgentState Extensions

### Voice Decision State Fields:
```python
class AgentState(TypedDict):
    # ... existing fields ...
    
    # Voice intent and analysis
    voice_intent: Optional[VoiceIntentAnalysis]
    voice_response_mode: Optional[str]  # "text", "tts", "auto"
    voice_analysis: Optional[Dict[str, Any]]  # STT analysis results
    
    # Dynamic voice configuration  
    voice_provider_config: Optional[Dict[str, Any]]
    voice_provider_preference: Optional[str]
    voice_quality_requirements: Optional[Dict[str, Any]]
    
    # User voice patterns and preferences
    user_voice_history: Optional[List[Dict[str, Any]]]
    user_voice_preferences: Optional[Dict[str, Any]]
    
    # Context for voice decisions
    conversation_voice_context: Optional[Dict[str, Any]]
    platform_voice_capabilities: Optional[Dict[str, Any]]
```

## ✅ Decision Logic Migration Strategy

### Phase 1: Extract Current Logic
1. ✅ **Analyzed**: keyword-based intent detection
2. ✅ **Analyzed**: static provider selection  
3. ✅ **Analyzed**: primitive TTS decision rules
4. ✅ **Documented**: current decision points and problems

### Phase 2: Design LangGraph Tools  
1. **voice_intent_analysis_tool**: Semantic intent detection
2. **voice_response_decision_tool**: Intelligent TTS decision
3. **voice_provider_selection_tool**: Dynamic provider choice
4. **voice_execution_tool**: Pure TTS execution

### Phase 3: Migrate Decision Logic
1. Move intent detection from intent_utils.py → LangGraph tools
2. Move TTS decisions from AgentRunner → Agent workflow
3. Implement dynamic voice configuration in AgentState
4. Add conversation context analysis for voice decisions

### Phase 4: Enhanced Decision Intelligence
1. Replace keyword matching with semantic analysis
2. Add user behavior learning and adaptation
3. Implement provider performance-based selection
4. Add cost and quality optimization logic

## 🔄 Выводы

### Критические проблемы текущей архитектуры:
1. **Primitive Decision Logic**: Keyword matching вместо semantic analysis
2. **Wrong Architecture Layer**: Decisions в utility classes, не в agent
3. **No Context Awareness**: Static rules без учета conversation history
4. **No Learning**: Нет адаптации к user preferences и behavior
5. **Static Configuration**: agent_config не обновляется динамически

### Ключевые решения для LangGraph integration:
1. **Centralize Decision Logic**: Все voice decisions в LangGraph agent tools
2. **Context-Aware Decisions**: Access to conversation history, user patterns  
3. **Intelligent Intent Detection**: Semantic analysis вместо keyword matching
4. **Dynamic Provider Selection**: Health, performance, cost-based selection
5. **User-Adaptive**: Learning from user voice interaction patterns

### Следующие шаги (Phase 4.1.5):
1. Performance impact assessment текущих voice decisions
2. Bottleneck identification в decision logic
3. Optimization opportunities for LangGraph integration
4. Design performance-optimized voice tools architecture

---
**Анализ завершен**: ✅ Decision logic patterns extracted, problems identified, LangGraph migration strategy designed.
