"""
LangGraph Voice Decision Optimizer - Phase 5.3.3 Implementation

Реализует оптимизацию voice decision making в LangGraph для минимизации latency:
- Decision caching для similar contexts (≤500ms target overhead)
- Prompt optimization для voice decision speed
- Conditional routing efficiency в LangGraph workflow
- AgentState voice context management optimization
- Real-time decision monitoring и performance tracking

Architecture Compliance:
- LangGraph workflow optimization patterns
- SOLID principles compliance
- Phase 1.2.3 performance optimization patterns
- Voice_v2_LangGraph_Decision_Analysis.md principles
"""

import asyncio
import logging
import time
import json
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
import statistics
import hashlib

logger = logging.getLogger(__name__)


class VoiceDecisionType(Enum):
    """Types of voice decisions in LangGraph"""
    SHOULD_PROCESS_VOICE = "should_process_voice"
    VOICE_RESPONSE_NEEDED = "voice_response_needed"
    TTS_AUTO_TRIGGER = "tts_auto_trigger"
    VOICE_PRIORITY = "voice_priority"
    VOICE_CONTEXT_ANALYSIS = "voice_context_analysis"


class DecisionComplexity(Enum):
    """Decision complexity levels"""
    SIMPLE = "simple"       # ≤100ms (pattern matching)
    MEDIUM = "medium"       # ≤300ms (context analysis)
    COMPLEX = "complex"     # ≤500ms (deep reasoning)


class CacheStrategy(Enum):
    """Voice decision caching strategies"""
    EXACT_MATCH = "exact_match"           # Exact context match
    SEMANTIC_SIMILARITY = "semantic"     # Similar contexts
    PATTERN_BASED = "pattern"            # Pattern matching
    USER_BEHAVIOR = "user_behavior"      # User-specific patterns


@dataclass
class VoiceDecisionMetrics:
    """Voice decision performance metrics"""
    decision_type: VoiceDecisionType
    total_decisions: int = 0
    cached_decisions: int = 0
    computed_decisions: int = 0

    # Timing metrics
    average_decision_time: float = 0.0
    p95_decision_time: float = 0.0
    p99_decision_time: float = 0.0
    recent_decision_times: List[float] = field(default_factory=list)

    # Cache effectiveness
    cache_hit_rate: float = 0.0
    cache_accuracy: float = 0.0  # Percentage of correct cached decisions

    # Decision quality
    decision_accuracy: float = 0.0
    decision_confidence: float = 0.0

    @property
    def is_meeting_target(self) -> bool:
        """Check if decision time meets target (<500ms)"""
        return self.average_decision_time <= 0.5

    @property
    def cache_effectiveness_score(self) -> float:
        """Calculate cache effectiveness (hit rate * accuracy)"""
        return self.cache_hit_rate * self.cache_accuracy / 100.0


@dataclass
class VoiceContextInfo:
    """Voice context information for decision making"""
    user_id: str
    agent_id: str
    conversation_id: str
    message_type: str  # 'voice', 'text', 'mixed'

    # Context metadata
    recent_interactions: List[str] = field(default_factory=list)
    user_preferences: Dict[str, Any] = field(default_factory=dict)
    conversation_state: Dict[str, Any] = field(default_factory=dict)

    # Voice-specific context
    voice_enabled: bool = True
    last_voice_interaction: Optional[datetime] = None
    voice_preference_score: float = 0.5  # 0.0 = text only, 1.0 = voice preferred

    def get_context_hash(self) -> str:
        """Generate context hash for caching"""
        context_data = {
            'user_id': self.user_id,
            'agent_id': self.agent_id,
            'message_type': self.message_type,
            'voice_enabled': self.voice_enabled,
            'preferences': self.user_preferences,
            'recent_types': [msg[:10] for msg in self.recent_interactions[-5:]]  # Last 5 truncated
        }

        context_str = json.dumps(context_data, sort_keys=True)
        return hashlib.sha256(context_str.encode()).hexdigest()[:16]


@dataclass
class VoiceDecision:
    """Voice decision result"""
    decision_type: VoiceDecisionType
    decision: bool
    confidence: float
    reasoning: str
    decision_time: float
    cached: bool = False
    cache_key: Optional[str] = None


@dataclass
class OptimizedPromptTemplate:
    """Optimized prompt template for voice decisions"""
    decision_type: VoiceDecisionType
    template: str
    expected_tokens: int
    complexity: DecisionComplexity

    def format_prompt(self, context: VoiceContextInfo, **kwargs) -> str:
        """Format prompt with context and variables"""
        return self.template.format(
            user_id=context.user_id,
            message_type=context.message_type,
            voice_enabled=context.voice_enabled,
            voice_preference=context.voice_preference_score,
            **kwargs
        )


class VoiceDecisionOptimizer:
    """
    LangGraph Voice Decision Optimizer

    Optimizes voice decision making in LangGraph workflows:
    - Intelligent caching of similar decisions
    - Optimized prompts for faster processing
    - Context analysis optimization
    - Real-time performance monitoring
    """

    def __init__(self, target_decision_time: float = 0.5):
        self.target_decision_time = target_decision_time
        self.decision_metrics: Dict[VoiceDecisionType, VoiceDecisionMetrics] = {}

        # Decision cache
        self._decision_cache: Dict[str, Tuple[VoiceDecision, datetime]] = {}
        self._cache_ttl_seconds = 300  # 5 minutes

        # Optimization state
        self._optimization_lock = asyncio.Lock()
        self._last_optimization = datetime.now()

        # Initialize optimized prompts
        self._prompt_templates = self._initialize_optimized_prompts()

        # Performance tracking
        self._decision_history: List[Tuple[datetime, VoiceDecisionType, float, bool]] = []

        logger.info("VoiceDecisionOptimizer initialized with target: %ss", target_decision_time)

    def _initialize_optimized_prompts(self) -> Dict[VoiceDecisionType, OptimizedPromptTemplate]:
        """Initialize optimized prompt templates for voice decisions"""
        return {
            VoiceDecisionType.SHOULD_PROCESS_VOICE: OptimizedPromptTemplate(
                decision_type=VoiceDecisionType.SHOULD_PROCESS_VOICE,
                template="""Quick voice decision: User {user_id} sent {message_type}.
Voice enabled: {voice_enabled}. Voice preference: {voice_preference:.1f}.
Should process voice? Answer: Yes/No + brief reason.""",
                expected_tokens=50,
                complexity=DecisionComplexity.SIMPLE
            ),

            VoiceDecisionType.VOICE_RESPONSE_NEEDED: OptimizedPromptTemplate(
                decision_type=VoiceDecisionType.VOICE_RESPONSE_NEEDED,
                template="""Voice response decision: Message type {message_type},
user voice preference {voice_preference:.1f}. Recent pattern: {recent_pattern}.
Voice response needed? Yes/No + confidence.""",
                expected_tokens=40,
                complexity=DecisionComplexity.SIMPLE
            ),

            VoiceDecisionType.TTS_AUTO_TRIGGER: OptimizedPromptTemplate(
                decision_type=VoiceDecisionType.TTS_AUTO_TRIGGER,
                template="""Auto TTS trigger: User prefers voice {voice_preference:.1f},
last voice interaction: {last_voice_time}. Message length: {message_length}.
Auto-trigger TTS? Yes/No.""",
                expected_tokens=30,
                complexity=DecisionComplexity.SIMPLE
            ),

            VoiceDecisionType.VOICE_PRIORITY: OptimizedPromptTemplate(
                decision_type=VoiceDecisionType.VOICE_PRIORITY,
                template="""Voice priority assessment: Context urgency {urgency},
user state {user_state}, voice capability {voice_quality}.
High priority voice response? Yes/No + priority level.""",
                expected_tokens=60,
                complexity=DecisionComplexity.MEDIUM
            ),

            VoiceDecisionType.VOICE_CONTEXT_ANALYSIS: OptimizedPromptTemplate(
                decision_type=VoiceDecisionType.VOICE_CONTEXT_ANALYSIS,
                template="""Voice context analysis: Conversation flow {conversation_flow},
emotional context {emotion}, technical context {technical}.
Voice enhances interaction? Yes/No + enhancement score.""",
                expected_tokens=80,
                complexity=DecisionComplexity.COMPLEX
            )
        }

    async def make_optimized_decision(self,
                                      decision_type: VoiceDecisionType,
                                      context: VoiceContextInfo,
                                      llm_invoke_func: Any,
                                      **kwargs) -> VoiceDecision:
        """
        Make optimized voice decision with caching and performance tracking.
        """
        start_time = time.time()

        # Check cache first
        cache_key = self._generate_cache_key(decision_type, context, **kwargs)
        cached_decision = await self._check_decision_cache(cache_key)

        if cached_decision:
            decision_time = time.time() - start_time
            await self._record_decision_metrics(decision_type, decision_time, cached=True)
            cached_decision.decision_time = decision_time
            return cached_decision

        # Make new decision
        decision = await self._compute_decision(
            decision_type, context, llm_invoke_func, **kwargs
        )

        decision_time = time.time() - start_time
        decision.decision_time = decision_time
        decision.cache_key = cache_key

        # Cache the decision
        await self._cache_decision(cache_key, decision)

        # Record metrics
        await self._record_decision_metrics(decision_type, decision_time, cached=False)

        return decision

    def _generate_cache_key(self, decision_type: VoiceDecisionType,
                            context: VoiceContextInfo, **kwargs) -> str:
        """Generate cache key for decision"""
        key_data = {
            'type': decision_type.value,
            'context_hash': context.get_context_hash(),
            'kwargs_hash': hashlib.sha256(
                json.dumps(kwargs, sort_keys=True).encode()
            ).hexdigest()[:8]
        }

        key_str = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(key_str.encode()).hexdigest()[:16]

    async def _check_decision_cache(self, cache_key: str) -> Optional[VoiceDecision]:
        """Check if decision is cached and still valid"""
        cached_data = self._decision_cache.get(cache_key)
        if not cached_data:
            return None

        decision, timestamp = cached_data

        # Check if cache entry is still valid
        age = (datetime.now() - timestamp).total_seconds()
        if age > self._cache_ttl_seconds:
            del self._decision_cache[cache_key]
            return None

        # Return cached decision
        decision.cached = True
        return decision

    async def _cache_decision(self, cache_key: str, decision: VoiceDecision) -> None:
        """Cache decision for future use"""
        self._decision_cache[cache_key] = (decision, datetime.now())

        # Cleanup old cache entries if needed
        if len(self._decision_cache) > 1000:  # Max cache size
            await self._cleanup_cache()

    async def _cleanup_cache(self) -> None:
        """Remove expired cache entries"""
        now = datetime.now()
        expired_keys = []

        for key, (_, timestamp) in self._decision_cache.items():
            age = (now - timestamp).total_seconds()
            if age > self._cache_ttl_seconds:
                expired_keys.append(key)

        for key in expired_keys:
            del self._decision_cache[key]

        logger.debug("Cleaned up %s expired decision cache entries", len(expired_keys))

    async def _compute_decision(self,
                                decision_type: VoiceDecisionType,
                                context: VoiceContextInfo,
                                llm_invoke_func: Any,
                                **kwargs) -> VoiceDecision:
        """Compute new voice decision using optimized prompts"""
        template = self._prompt_templates.get(decision_type)
        if not template:
            raise ValueError(f"No template found for decision type: {decision_type}")

        # Prepare prompt variables
        prompt_vars = kwargs.copy()
        prompt_vars.update({
            'recent_pattern': self._analyze_recent_pattern(context),
            'message_length': kwargs.get('message_length', 0),
            'last_voice_time': self._format_last_voice_time(context),
            'urgency': kwargs.get('urgency', 'normal'),
            'user_state': kwargs.get('user_state', 'active'),
            'voice_quality': kwargs.get('voice_quality', 'high'),
            'conversation_flow': kwargs.get('conversation_flow', 'normal'),
            'emotion': kwargs.get('emotion', 'neutral'),
            'technical': kwargs.get('technical', 'stable')
        })

        # Invoke LLM with timeout based on complexity
        timeout = self._get_decision_timeout(template.complexity)

        try:
            # TODO: Implement actual LLM call with optimized prompt
            # prompt = template.format_prompt(context, **prompt_vars)
            # response = await asyncio.wait_for(llm_invoke_func(prompt), timeout=timeout)

            # For now, return a mock decision based on simple heuristics
            decision = self._make_heuristic_decision(decision_type, context, **kwargs)

            return decision

        except asyncio.TimeoutError:
            logger.warning("Decision timeout for %s", decision_type.value)
            # Return safe default decision
            return VoiceDecision(
                decision_type=decision_type,
                decision=False,
                confidence=0.5,
                reasoning="Timeout - safe default",
                decision_time=timeout
            )

    def _make_heuristic_decision(self, decision_type: VoiceDecisionType,
                                 context: VoiceContextInfo, **kwargs) -> VoiceDecision:
        """Make heuristic decision for demonstration purposes"""
        if decision_type == VoiceDecisionType.SHOULD_PROCESS_VOICE:
            decision = context.voice_enabled and context.message_type == 'voice'
            confidence = 0.9 if decision else 0.8
            reasoning = f"Voice enabled: {context.voice_enabled}, type: {context.message_type}"

        elif decision_type == VoiceDecisionType.VOICE_RESPONSE_NEEDED:
            decision = context.voice_preference_score > 0.6
            confidence = context.voice_preference_score
            reasoning = f"Voice preference score: {context.voice_preference_score}"

        elif decision_type == VoiceDecisionType.TTS_AUTO_TRIGGER:
            message_length = kwargs.get('message_length', 0)
            decision = (context.voice_preference_score > 0.7 and
                        message_length > 50 and message_length < 200)
            confidence = 0.8
            reasoning = f"Preference: {context.voice_preference_score}, length: {message_length}"

        else:
            decision = False
            confidence = 0.5
            reasoning = "Default decision"

        return VoiceDecision(
            decision_type=decision_type,
            decision=decision,
            confidence=confidence,
            reasoning=reasoning,
            decision_time=0.0  # Will be set by caller
        )

    def _get_decision_timeout(self, complexity: DecisionComplexity) -> float:
        """Get timeout based on decision complexity"""
        timeouts = {
            DecisionComplexity.SIMPLE: 0.1,   # 100ms
            DecisionComplexity.MEDIUM: 0.3,   # 300ms
            DecisionComplexity.COMPLEX: 0.5   # 500ms
        }
        return timeouts.get(complexity, 0.5)

    def _analyze_recent_pattern(self, context: VoiceContextInfo) -> str:
        """Analyze recent interaction pattern"""
        if not context.recent_interactions:
            return "no_pattern"

        voice_count = sum(1 for msg in context.recent_interactions[-5:] if 'voice' in msg.lower())
        if voice_count >= 3:
            return "voice_preferred"
        elif voice_count >= 1:
            return "mixed_interaction"
        else:
            return "text_preferred"

    def _format_last_voice_time(self, context: VoiceContextInfo) -> str:
        """Format last voice interaction time"""
        if not context.last_voice_interaction:
            return "never"

        time_diff = datetime.now() - context.last_voice_interaction
        if time_diff.total_seconds() < 60:
            return "recent"
        elif time_diff.total_seconds() < 300:
            return "few_minutes"
        else:
            return "long_ago"

    async def _record_decision_metrics(self, decision_type: VoiceDecisionType,
                                       decision_time: float, cached: bool) -> None:
        """Record decision performance metrics"""
        if decision_type not in self.decision_metrics:
            self.decision_metrics[decision_type] = VoiceDecisionMetrics(decision_type=decision_type)

        metrics = self.decision_metrics[decision_type]
        metrics.total_decisions += 1

        if cached:
            metrics.cached_decisions += 1
        else:
            metrics.computed_decisions += 1

        # Update timing metrics
        metrics.recent_decision_times.append(decision_time)
        if len(metrics.recent_decision_times) > 100:
            metrics.recent_decision_times = metrics.recent_decision_times[-100:]

        # Calculate statistics
        if metrics.recent_decision_times:
            metrics.average_decision_time = statistics.mean(metrics.recent_decision_times)
            sorted_times = sorted(metrics.recent_decision_times)
            n = len(sorted_times)
            metrics.p95_decision_time = sorted_times[int(0.95 * n)] if n > 0 else decision_time
            metrics.p99_decision_time = sorted_times[int(0.99 * n)] if n > 0 else decision_time

        # Update cache metrics
        if metrics.total_decisions > 0:
            metrics.cache_hit_rate = (metrics.cached_decisions / metrics.total_decisions) * 100

        # Track overall performance
        self._decision_history.append((datetime.now(), decision_type, decision_time, cached))

        # Cleanup old history
        cutoff = datetime.now() - timedelta(hours=24)
        self._decision_history = [
            (ts,
             dt,
             time_val,
             cached_val) for ts,
            dt,
            time_val,
            cached_val in self._decision_history if ts > cutoff]

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get comprehensive voice decision performance summary"""
        summary = {
            "timestamp": datetime.now().isoformat(),
            "target_decision_time": self.target_decision_time,
            "cache_size": len(self._decision_cache),
            "decision_types": {}
        }

        overall_meeting_target = True
        total_decisions = 0
        total_cached = 0

        for decision_type, metrics in self.decision_metrics.items():
            summary["decision_types"][decision_type.value] = {
                "total_decisions": metrics.total_decisions,
                "cached_decisions": metrics.cached_decisions,
                "cache_hit_rate": metrics.cache_hit_rate,
                "average_decision_time": metrics.average_decision_time,
                "p95_decision_time": metrics.p95_decision_time,
                "meeting_target": metrics.is_meeting_target,
                "cache_effectiveness": metrics.cache_effectiveness_score
            }

            if not metrics.is_meeting_target:
                overall_meeting_target = False

            total_decisions += metrics.total_decisions
            total_cached += metrics.cached_decisions

        # Overall statistics
        summary["overall"] = {
            "total_decisions": total_decisions,
            "overall_cache_hit_rate": (
                total_cached /
                total_decisions *
                100) if total_decisions > 0 else 0,
            "meeting_target": overall_meeting_target}

        # Recent performance
        if self._decision_history:
            recent_times = [time_val for _, _, time_val, _ in self._decision_history[-50:]]
            summary["recent_performance"] = {
                "average_time": statistics.mean(recent_times),
                "max_time": max(recent_times),
                "min_time": min(recent_times)
            }

        return summary

    async def optimize_if_needed(self) -> bool:
        """
        Trigger optimization if decision performance degrades.
        Returns True if optimization was performed.
        """
        now = datetime.now()
        if (now - self._last_optimization).total_seconds() < 180:  # 3 minutes cooldown
            return False

        async with self._optimization_lock:
            needs_optimization = False

            # Check decision time performance
            for metrics in self.decision_metrics.values():
                if not metrics.is_meeting_target:
                    logger.warning(
                        "Decision type %s not meeting target: %.3fs",
                        metrics.decision_type.value,
                        metrics.average_decision_time)
                    needs_optimization = True

            # Check cache effectiveness
            total_decisions = sum(m.total_decisions for m in self.decision_metrics.values())
            if total_decisions > 50:  # Only check after sufficient data
                total_cached = sum(m.cached_decisions for m in self.decision_metrics.values())
                cache_hit_rate = (total_cached / total_decisions) * 100

                if cache_hit_rate < 30.0:  # Low cache hit rate
                    logger.info("Low cache hit rate: %.1f%%", cache_hit_rate)
                    needs_optimization = True

            if needs_optimization:
                await self._perform_optimization()
                self._last_optimization = now
                return True

        return False

    async def _perform_optimization(self) -> None:
        """Perform voice decision optimization"""
        logger.info("Performing voice decision optimization...")

        # Optimization actions:
        # - Adjust cache TTL based on accuracy
        # - Update prompt templates for slow decisions
        # - Optimize context analysis
        # - Adjust decision complexity routing

        summary = self.get_performance_summary()
        logger.info("Voice decision optimization triggered: %s", summary)

        # Adjust cache TTL based on performance
        if len(self._decision_cache) > 500:
            self._cache_ttl_seconds = max(120, self._cache_ttl_seconds - 60)  # Reduce TTL
        elif len(self._decision_cache) < 100:
            self._cache_ttl_seconds = min(600, self._cache_ttl_seconds + 60)  # Increase TTL
