# Voice_v2 Architecture Improvement Implementation Plan

## Дата: 2024-12-30
## Основа: Voice_v2_Architecture_Deep_Analysis_Report.md
## Цель: Практическая реализация архитектурных улучшений

---

## PHASE 1: FOUNDATION SETUP (Days 1-2)

### 1.1 Create Core Interfaces and Abstractions

**Step 1.1.1: Analysis Interfaces**
```python
# app/services/voice_v2/analysis/interfaces.py
from abc import ABC, abstractmethod
from typing import Dict, Any
from dataclasses import dataclass

@dataclass
class AnalysisContext:
    user_input: str
    messages: List[Dict[str, Any]]
    user_data: Dict[str, Any]
    platform: str
    session_metadata: Dict[str, Any]

@dataclass  
class AnalysisResult:
    score: float
    confidence: float
    reasoning: str
    metadata: Dict[str, Any]

class VoiceIntentAnalyzer(ABC):
    @abstractmethod
    async def analyze(self, context: AnalysisContext) -> AnalysisResult:
        pass

class DecisionEngine(ABC):
    @abstractmethod
    async def make_decision(self, 
                          content_result: AnalysisResult,
                          context_result: AnalysisResult, 
                          pattern_result: AnalysisResult) -> IntentAnalysisResult:
        pass
```

**Step 1.1.2: Provider Interfaces**
```python
# app/services/voice_v2/providers/interfaces.py
from abc import ABC, abstractmethod

class ProviderSelectionStrategy(ABC):
    @abstractmethod
    async def select_provider(self, 
                            providers: List[ProviderConfig],
                            context: SelectionContext) -> ProviderSelection:
        pass

class HealthChecker(ABC):
    @abstractmethod
    async def check_provider_health(self, provider: str) -> HealthStatus:
        pass
```

### 1.2 Setup Dependency Injection Container

**Step 1.2.1: Install that-depends**
```bash
uv add that-depends
```

**Step 1.2.2: Create DI Container**
```python
# app/services/voice_v2/container.py
from that_depends import container, providers
from app.services.voice_v2.analysis import *
from app.services.voice_v2.providers import *

class VoiceContainer(container.DeclarativeContainer):
    # Configuration
    config = providers.Configuration()
    
    # Infrastructure
    redis_client = providers.Singleton(
        providers.Factory(redis.Redis),
        url=config.redis_url
    )
    
    # Analysis components
    content_analyzer = providers.Factory(ContentSuitabilityAnalyzer)
    context_analyzer = providers.Factory(ConversationContextAnalyzer)
    pattern_analyzer = providers.Factory(UserPatternAnalyzer)
    
    # Decision engine
    decision_engine = providers.Factory(
        IntentDecisionEngine,
        weight_config=config.decision_weights
    )
    
    # Main orchestrator
    intent_analysis_service = providers.Factory(
        VoiceIntentAnalysisService,
        content_analyzer=content_analyzer,
        context_analyzer=context_analyzer,
        pattern_analyzer=pattern_analyzer,
        decision_engine=decision_engine
    )
```

### 1.3 Create Factory Patterns

**Step 1.3.1: Provider Selection Factory**
```python
# app/services/voice_v2/providers/factory.py
from typing import Dict, Type
from app.services.voice_v2.providers.strategies import *

class ProviderSelectionFactory:
    _strategies: Dict[ProviderSelectionType, Type[ProviderSelectionStrategy]] = {
        ProviderSelectionType.HEALTH_BASED: HealthBasedStrategy,
        ProviderSelectionType.PERFORMANCE_BASED: PerformanceBasedStrategy,
        ProviderSelectionType.COST_OPTIMIZED: CostOptimizedStrategy,
    }
    
    @classmethod
    def create_strategy(cls, strategy_type: ProviderSelectionType) -> ProviderSelectionStrategy:
        strategy_class = cls._strategies.get(strategy_type)
        if not strategy_class:
            raise ValueError(f"Unknown strategy type: {strategy_type}")
        return strategy_class()
```

---

## PHASE 2: COMPONENT MIGRATION (Days 3-6)

### 2.1 Migrate Content Analysis (Day 3)

**Step 2.1.1: Create ContentSuitabilityAnalyzer**
```python
# app/services/voice_v2/analysis/content/content_analyzer.py
class ContentSuitabilityAnalyzer(VoiceIntentAnalyzer):
    """Focused content suitability analysis for TTS"""
    
    def __init__(self, config: ContentAnalysisConfig = None):
        self._config = config or ContentAnalysisConfig()
        self._logger = logging.getLogger(__name__)
    
    async def analyze(self, context: AnalysisContext) -> AnalysisResult:
        """Analyze content suitability for TTS"""
        try:
            score = await self._calculate_content_score(context.user_input)
            confidence = self._calculate_confidence(context)
            reasoning = self._generate_reasoning(score, context)
            
            return AnalysisResult(
                score=score,
                confidence=confidence,
                reasoning=reasoning,
                metadata=self._extract_metadata(context)
            )
        except Exception as e:
            self._logger.error(f"Content analysis failed: {e}", exc_info=True)
            return self._fallback_result()
    
    async def _calculate_content_score(self, text: str) -> float:
        """Calculate TTS suitability score for content"""
        # Move existing content analysis logic here
        # Reduce from 150+ lines to 30-40 lines focused logic
        pass
```

**Step 2.1.2: Create Content Models**
```python
# app/services/voice_v2/analysis/content/models.py
@dataclass
class ContentAnalysisConfig:
    min_word_count: int = 10
    max_word_count: int = 500
    weight_conversational: float = 0.3
    weight_educational: float = 0.25
    penalty_technical: float = 0.2

@dataclass
class ContentMetrics:
    word_count: int
    sentence_count: int
    technical_score: float
    conversational_score: float
    educational_score: float
```

### 2.2 Migrate Context Analysis (Day 4)

**Step 2.2.1: Create ConversationContextAnalyzer**  
```python
# app/services/voice_v2/analysis/context/context_analyzer.py
class ConversationContextAnalyzer(VoiceIntentAnalyzer):
    """Analyze conversation context for voice intent"""
    
    async def analyze(self, context: AnalysisContext) -> AnalysisResult:
        # Move conversation context logic from original tool
        # Focus only on context analysis (50-70 lines)
        pass
```

### 2.3 Migrate Pattern Analysis (Day 5)

**Step 2.3.1: Create UserPatternAnalyzer**
```python
# app/services/voice_v2/analysis/patterns/pattern_analyzer.py  
class UserPatternAnalyzer(VoiceIntentAnalyzer):
    """Analyze user voice interaction patterns"""
    
    async def analyze(self, context: AnalysisContext) -> AnalysisResult:
        # Move user pattern logic from original tool
        # Focus only on pattern analysis (50-70 lines)
        pass
```

### 2.4 Create Analysis Orchestrator (Day 6)

**Step 2.4.1: Main Service**
```python
# app/services/voice_v2/analysis/orchestrator/analysis_orchestrator.py
class VoiceIntentAnalysisService:
    """Main orchestrator for voice intent analysis"""
    
    def __init__(self,
                 content_analyzer: ContentSuitabilityAnalyzer,
                 context_analyzer: ConversationContextAnalyzer,
                 pattern_analyzer: UserPatternAnalyzer,
                 decision_engine: IntentDecisionEngine):
        self._content_analyzer = content_analyzer
        self._context_analyzer = context_analyzer
        self._pattern_analyzer = pattern_analyzer
        self._decision_engine = decision_engine
        self._logger = logging.getLogger(__name__)
    
    async def analyze_intent(self, context: AnalysisContext) -> IntentAnalysisResult:
        """Orchestrate comprehensive voice intent analysis"""
        try:
            # Parallel execution for performance
            content_task = asyncio.create_task(self._content_analyzer.analyze(context))
            context_task = asyncio.create_task(self._context_analyzer.analyze(context))
            pattern_task = asyncio.create_task(self._pattern_analyzer.analyze(context))
            
            # Wait for all analyses
            content_result, context_result, pattern_result = await asyncio.gather(
                content_task, context_task, pattern_task,
                return_exceptions=True
            )
            
            # Handle exceptions
            if isinstance(content_result, Exception):
                self._logger.error(f"Content analysis failed: {content_result}")
                content_result = self._fallback_content_result()
                
            # Similar for other results...
            
            # Make final decision
            return await self._decision_engine.make_decision(
                content_result, context_result, pattern_result
            )
            
        except Exception as e:
            self._logger.error(f"Intent analysis orchestration failed: {e}", exc_info=True)
            return self._fallback_intent_result()
```

---

## PHASE 3: INTEGRATION AND TESTING (Days 7-9)

### 3.1 Update LangGraph Tool Integration (Day 7)

**Step 3.1.1: Modern Tool Interface**
```python
# app/services/voice_v2/integration/voice_intent_analysis_tool_v2.py
from that_depends import Provide
from app.services.voice_v2.container import VoiceContainer

@tool
def voice_intent_analysis_tool_v2(
    state: Annotated[Dict, InjectedState] = None,
    analysis_service: VoiceIntentAnalysisService = Provide[VoiceContainer.intent_analysis_service]
) -> str:
    """
    Modern voice intent analysis tool with DI and clean architecture
    """
    try:
        # Extract context from state
        context = AnalysisContext(
            user_input=state.get("user_input", ""),
            messages=state.get("messages", []),
            user_data=state.get("user_data", {}),
            platform=state.get("channel", "unknown"),
            session_metadata=state.get("metadata", {})
        )
        
        # Run analysis through DI service
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(analysis_service.analyze_intent(context))
        
        return json.dumps(result.to_dict(), ensure_ascii=False)
        
    except Exception as e:
        logger.error(f"Voice intent analysis failed: {e}", exc_info=True)
        return json.dumps({"success": False, "error": str(e)})
```

### 3.2 Create Comprehensive Tests (Days 8-9)

**Step 3.2.1: Unit Tests Structure**
```
tests/voice_v2/analysis/
├── content/
│   ├── test_content_analyzer.py
│   ├── test_content_models.py
│   └── test_content_config.py
├── context/
│   ├── test_context_analyzer.py
│   └── test_context_models.py
├── patterns/
│   ├── test_pattern_analyzer.py
│   └── test_pattern_models.py
├── orchestrator/
│   ├── test_analysis_orchestrator.py
│   ├── test_decision_engine.py
│   └── test_integration.py
└── conftest.py
```

**Step 3.2.2: Sample Test Implementation**
```python
# tests/voice_v2/analysis/content/test_content_analyzer.py
import pytest
from app.services.voice_v2.analysis.content import ContentSuitabilityAnalyzer

class TestContentSuitabilityAnalyzer:
    @pytest.fixture
    def analyzer(self):
        return ContentSuitabilityAnalyzer()
    
    @pytest.fixture  
    def sample_context(self):
        return AnalysisContext(
            user_input="Расскажи мне про машинное обучение",
            messages=[],
            user_data={},
            platform="telegram",
            session_metadata={}
        )
    
    async def test_analyze_educational_content(self, analyzer, sample_context):
        result = await analyzer.analyze(sample_context)
        
        assert result.score > 0.6  # Educational content should score high
        assert result.confidence > 0.7
        assert "educational" in result.reasoning.lower()
        assert result.metadata["content_type"] == "educational"
    
    async def test_analyze_short_content(self, analyzer):
        context = AnalysisContext(
            user_input="Да",
            messages=[],
            user_data={},
            platform="telegram", 
            session_metadata={}
        )
        
        result = await analyzer.analyze(context)
        assert result.score < 0.3  # Short content should score low
```

---

## PHASE 4: OPTIMIZATION AND CLEANUP (Days 10-12)

### 4.1 Performance Optimization (Day 10)

**Step 4.1.1: Add Caching Layer**
```python
# app/services/voice_v2/infrastructure/cache.py
class AnalysisCache:
    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client
        self._ttl = 3600  # 1 hour
    
    async def get_cached_result(self, context_hash: str) -> Optional[AnalysisResult]:
        cached = await self._redis.get(f"analysis:{context_hash}")
        if cached:
            return AnalysisResult.from_dict(json.loads(cached))
        return None
    
    async def cache_result(self, context_hash: str, result: AnalysisResult):
        await self._redis.setex(
            f"analysis:{context_hash}",
            self._ttl,
            json.dumps(result.to_dict())
        )
```

**Step 4.1.2: Add Circuit Breaker**
```python
# app/services/voice_v2/infrastructure/circuit_breaker.py
class AnalysisCircuitBreaker:
    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self._failure_threshold = failure_threshold
        self._timeout = timeout
        self._failure_count = 0
        self._last_failure_time = None
        self._state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    
    async def call(self, func, *args, **kwargs):
        if self._state == "OPEN":
            if time.time() - self._last_failure_time > self._timeout:
                self._state = "HALF_OPEN"
            else:
                raise CircuitBreakerOpenError("Circuit breaker is open")
        
        try:
            result = await func(*args, **kwargs)
            if self._state == "HALF_OPEN":
                self._state = "CLOSED"
                self._failure_count = 0
            return result
        except Exception as e:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self._failure_count >= self._failure_threshold:
                self._state = "OPEN"
            
            raise e
```

### 4.2 Migration and Cleanup (Days 11-12)

**Step 4.2.1: Gradual Migration Script**
```python
# scripts/migrate_voice_analysis.py
"""
Gradual migration script to switch from old to new voice analysis
"""

async def migrate_voice_analysis():
    """Migrate voice analysis tools"""
    
    # Phase 1: Deploy new components alongside old ones
    logger.info("Phase 1: Deploying new components...")
    
    # Phase 2: Switch traffic gradually (10%, 50%, 100%)
    logger.info("Phase 2: Gradual traffic switch...")
    
    # Phase 3: Remove old components
    logger.info("Phase 3: Cleaning up old components...")
    
    # Validate all metrics
    await validate_migration_metrics()
```

**Step 4.2.2: Cleanup Old Code**
```bash
# Remove old monolithic tool after migration
rm app/services/voice_v2/integration/voice_intent_analysis_tool.py

# Update imports across codebase
find . -name "*.py" -exec sed -i 's/voice_intent_analysis_tool/voice_intent_analysis_tool_v2/g' {} \;
```

---

## VALIDATION CRITERIA

### Architecture Metrics
- [ ] SOLID Compliance: ≥95%
- [ ] DIP Score: ≥95%
- [ ] SRP Score: ≥98%
- [ ] CCN Average: ≤5

### Performance Metrics  
- [ ] Latency: ≤3.0s (target: 2.9s)
- [ ] Memory Usage: +5% max
- [ ] CPU Utilization: -10% min

### Code Quality Metrics
- [ ] Lines per file: ≤120
- [ ] Test Coverage: ≥95%
- [ ] Pylint Score: ≥9.5/10

### Functional Metrics
- [ ] All existing functionality preserved
- [ ] Error handling improved
- [ ] Logging enhanced
- [ ] Monitoring capabilities added

---

## ROLLBACK PLAN

### Emergency Rollback (< 5 minutes)
1. Switch feature flag to old implementation
2. Restart services
3. Verify functionality

### Partial Rollback (< 30 minutes)
1. Revert specific component
2. Update DI container configuration  
3. Run validation tests

### Full Rollback (< 2 hours)
1. Revert all commits
2. Restore original files
3. Full system validation
4. Post-mortem analysis

---

## SUCCESS METRICS TRACKING

### Daily Metrics Dashboard
- Architecture compliance scores
- Performance benchmarks
- Error rates and availability
- Code quality trends

### Weekly Review Points
- Migration progress vs plan
- Metric improvements
- Team feedback and blockers
- Risk assessment updates

---

## NEXT ACTIONS

1. **Immediate (Today)**: Start Phase 1.1 - Create core interfaces
2. **Day 1**: Complete DI container setup
3. **Day 2**: Begin content analyzer migration
4. **Day 3**: Daily stand-up and progress review

**Expected Completion**: 12 days
**Target Metrics Achievement**: 95%+ architecture compliance
**Risk Level**: Medium (well-planned gradual migration)
