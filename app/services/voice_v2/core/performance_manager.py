"""
Performance Manager - Phase 5.3.5 Implementation

Интегрирует систему оптимизации производительности в voice_v2 orchestrator:
- Conditional activation через .env VOICE_V2_PERFORMANCE_ENABLED
- Centralized management всех performance optimizers
- Automatic initialization и lifecycle management
- Production monitoring activation
- Testing support и validation

Architecture Compliance:
- SOLID principles compliance (SRP, OCP, DIP)
- Centralized performance management pattern
- Configuration-driven activation
- Lifecycle management best practices
"""

import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.core.config import settings
from app.services.voice_v2.performance.stt_optimizer import STTPerformanceOptimizer, STTOptimizationConfig
from app.services.voice_v2.performance.tts_optimizer import TTSPerformanceOptimizer, TTSOptimizationConfig
from app.services.voice_v2.performance.langgraph_optimizer import VoiceDecisionOptimizer
from app.services.voice_v2.performance.integration_monitor import IntegrationPerformanceMonitor, LoadTestConfig
from app.services.voice_v2.performance.validation_suite import PerformanceValidationSuite
from app.services.voice_v2.core.interfaces import ProviderType

logger = logging.getLogger(__name__)


class PerformanceLevel(Enum):
    """Performance optimization levels"""
    DISABLED = "disabled"          # No performance optimization
    BASIC = "basic"               # Basic optimization only
    STANDARD = "standard"         # Standard optimization (default)
    AGGRESSIVE = "aggressive"     # Maximum optimization
    MONITORING_ONLY = "monitoring" # Only monitoring, no optimization


@dataclass
class PerformanceConfig:
    """Performance system configuration from environment"""
    enabled: bool = False
    level: PerformanceLevel = PerformanceLevel.STANDARD
    monitoring_enabled: bool = True
    monitoring_interval: int = 30  # seconds
    
    # STT optimization settings
    stt_target_latency: float = 3.5
    stt_parallel_enabled: bool = True
    stt_cache_enabled: bool = True
    
    # TTS optimization settings
    tts_target_latency: float = 3.0
    tts_streaming_enabled: bool = True
    tts_compression_enabled: bool = True
    
    # Decision optimization settings
    decision_target_latency: float = 0.5
    decision_cache_enabled: bool = True
    
    # Load testing settings
    load_test_enabled: bool = False
    load_test_users: int = 10
    load_test_duration: int = 300  # seconds
    
    @classmethod
    def from_env(cls) -> 'PerformanceConfig':
        """Create configuration from centralized settings"""
        return cls(
            enabled=settings.VOICE_V2_PERFORMANCE_ENABLED,
            level=PerformanceLevel.STANDARD,  # Default level, можно добавить в settings при необходимости
            monitoring_enabled=settings.VOICE_V2_MONITORING_ENABLED,
            monitoring_interval=30,  # Default monitoring interval
            
            # STT settings
            stt_target_latency=3.5,  # Default target latency
            stt_parallel_enabled=settings.VOICE_V2_STT_OPTIMIZATION_ENABLED,
            stt_cache_enabled=settings.VOICE_V2_STT_OPTIMIZATION_ENABLED,
            
            # TTS settings
            tts_target_latency=3.0,  # Default target latency
            tts_streaming_enabled=settings.VOICE_V2_TTS_STREAMING_ENABLED,
            tts_compression_enabled=settings.VOICE_V2_TTS_COMPRESSION_ENABLED,
            
            # Decision settings
            decision_target_latency=0.5,  # Default target latency
            decision_cache_enabled=settings.VOICE_V2_LANGGRAPH_OPTIMIZATION_ENABLED,
            
            # Load testing
            load_test_enabled=settings.VOICE_V2_LOAD_TESTING_ENABLED,
            load_test_users=10,  # Default users
            load_test_duration=300  # Default duration
        )


@dataclass
class PerformanceStatus:
    """Current performance system status"""
    enabled: bool
    level: PerformanceLevel
    components_initialized: Dict[str, bool]
    monitoring_active: bool
    last_optimization: Optional[datetime]
    performance_metrics: Dict[str, Any]


class VoicePerformanceManager:
    """
    Voice Performance Manager
    
    Centralized management of voice_v2 performance optimization system:
    - Configuration-driven activation
    - Component lifecycle management  
    - Monitoring coordination
    - Integration with voice_v2 orchestrator
    """
    
    def __init__(self, config: Optional[PerformanceConfig] = None):
        self.config = config or PerformanceConfig.from_env()
        
        # Performance components
        self.stt_optimizer: Optional[STTPerformanceOptimizer] = None
        self.tts_optimizer: Optional[TTSPerformanceOptimizer] = None
        self.decision_optimizer: Optional[VoiceDecisionOptimizer] = None
        self.integration_monitor: Optional[IntegrationPerformanceMonitor] = None
        self.validation_suite: Optional[PerformanceValidationSuite] = None
        
        # State management
        self._initialized = False
        self._monitoring_active = False
        self._components_status: Dict[str, bool] = {}
        
        logger.info(f"VoicePerformanceManager created - Enabled: {self.config.enabled}, "
                   f"Level: {self.config.level.value}")
    
    @property
    def is_enabled(self) -> bool:
        """Check if performance system is enabled"""
        return self.config.enabled and self.config.level != PerformanceLevel.DISABLED
    
    @property
    def is_monitoring_only(self) -> bool:
        """Check if only monitoring is enabled"""
        return self.config.level == PerformanceLevel.MONITORING_ONLY
    
    async def initialize(self, available_providers: List[ProviderType]) -> None:
        """
        Initialize performance system based on configuration.
        
        Args:
            available_providers: List of available voice providers
        """
        if not self.is_enabled:
            logger.info("Performance system disabled via configuration")
            return
        
        logger.info(f"Initializing performance system - Level: {self.config.level.value}")
        
        try:
            # Initialize optimizers based on level
            if not self.is_monitoring_only:
                await self._initialize_optimizers(available_providers)
            
            # Initialize monitoring if enabled
            if self.config.monitoring_enabled:
                await self._initialize_monitoring()
            
            # Initialize validation suite
            await self._initialize_validation_suite()
            
            self._initialized = True
            logger.info("Performance system initialization completed")
            
        except Exception as e:
            logger.error(f"Performance system initialization failed: {e}")
            raise
    
    async def _initialize_optimizers(self, available_providers: List[ProviderType]) -> None:
        """Initialize performance optimizers"""
        logger.info("Initializing performance optimizers")
        
        # Initialize STT optimizer
        if self.config.stt_cache_enabled or self.config.stt_parallel_enabled:
            stt_config = self._create_stt_config()
            self.stt_optimizer = STTPerformanceOptimizer(stt_config)
            await self.stt_optimizer.initialize_connection_pools(available_providers)
            self._components_status['stt_optimizer'] = True
            logger.info("STT optimizer initialized")
        
        # Initialize TTS optimizer
        if self.config.tts_streaming_enabled or self.config.tts_compression_enabled:
            tts_config = self._create_tts_config()
            self.tts_optimizer = TTSPerformanceOptimizer(tts_config)
            await self.tts_optimizer.initialize_connection_pools(available_providers)
            self._components_status['tts_optimizer'] = True
            logger.info("TTS optimizer initialized")
        
        # Initialize decision optimizer
        if self.config.decision_cache_enabled:
            self.decision_optimizer = VoiceDecisionOptimizer(
                target_decision_time=self.config.decision_target_latency
            )
            self._components_status['decision_optimizer'] = True
            logger.info("Decision optimizer initialized")
    
    async def _initialize_monitoring(self) -> None:
        """Initialize performance monitoring using centralized settings"""
        logger.info("Initializing performance monitoring")
        
        load_config = LoadTestConfig(
            concurrent_users=self.config.load_test_users,
            test_duration_seconds=self.config.load_test_duration,
            target_stt_latency=self.config.stt_target_latency,
            target_tts_latency=self.config.tts_target_latency
        )
        
        self.integration_monitor = IntegrationPerformanceMonitor(load_config)
        
        # Set optimizers if they exist
        if self.stt_optimizer or self.tts_optimizer or self.decision_optimizer:
            self.integration_monitor.set_optimizers(
                self.stt_optimizer,
                self.tts_optimizer,
                self.decision_optimizer
            )
        
        # Start monitoring
        await self.integration_monitor.start_monitoring(
            interval_seconds=float(self.config.monitoring_interval)
        )
        self._monitoring_active = True
        self._components_status['integration_monitor'] = True
        
        logger.info(f"Performance monitoring started - Interval: {self.config.monitoring_interval}s")
    
    async def _initialize_validation_suite(self) -> None:
        """Initialize validation suite"""
        logger.info("Initializing validation suite")
        
        self.validation_suite = PerformanceValidationSuite()
        
        # Configure with available components
        if self.integration_monitor:
            self.validation_suite.configure_components(
                self.integration_monitor,
                self.stt_optimizer,
                self.tts_optimizer,
                self.decision_optimizer
            )
        
        self._components_status['validation_suite'] = True
        logger.info("Validation suite initialized")
    
    def _create_stt_config(self) -> STTOptimizationConfig:
        """Create STT optimization configuration from centralized settings"""
        return STTOptimizationConfig(
            target_latency=self.config.stt_target_latency,
            enable_parallel_attempts=self.config.stt_parallel_enabled,
            dynamic_ordering=True,  # Always enabled for optimization
            cache_ttl_seconds=settings.VOICE_V2_STT_CACHE_TTL if self.config.stt_cache_enabled else 0,
            
            # Use settings-based configuration
            max_connections=settings.VOICE_V2_STT_MAX_CONNECTIONS,
            max_connections_per_host=settings.VOICE_V2_STT_PARALLEL_REQUESTS,
        )
    
    def _create_tts_config(self) -> TTSOptimizationConfig:
        """Create TTS optimization configuration from centralized settings"""
        return TTSOptimizationConfig(
            target_latency=self.config.tts_target_latency,
            enable_streaming=self.config.tts_streaming_enabled,
            enable_compression=self.config.tts_compression_enabled,
            enable_response_caching=True,  # Always enabled for optimization
            
            # Use settings-based configuration
            max_connections=settings.VOICE_V2_STT_MAX_CONNECTIONS,  # Reuse STT connection settings
            max_connections_per_host=settings.VOICE_V2_STT_PARALLEL_REQUESTS,
            cache_max_size_mb=int(settings.VOICE_V2_TTS_CACHE_SIZE / 10)  # Convert to MB approximation
        )
    
    def _get_connection_pool_size(self) -> int:
        """Get connection pool size based on performance level"""
        if self.config.level == PerformanceLevel.AGGRESSIVE:
            return 150
        elif self.config.level == PerformanceLevel.STANDARD:
            return 100
        else:  # BASIC
            return 50
    
    def _get_host_connection_limit(self) -> int:
        """Get per-host connection limit based on performance level"""
        if self.config.level == PerformanceLevel.AGGRESSIVE:
            return 50
        elif self.config.level == PerformanceLevel.STANDARD:
            return 30
        else:  # BASIC
            return 20
    
    def _get_cache_size(self) -> int:
        """Get cache size based on performance level"""
        if self.config.level == PerformanceLevel.AGGRESSIVE:
            return 2000
        elif self.config.level == PerformanceLevel.STANDARD:
            return 1000
        else:  # BASIC
            return 500
    
    def _get_cache_size_mb(self) -> int:
        """Get cache size in MB based on performance level"""
        if self.config.level == PerformanceLevel.AGGRESSIVE:
            return 200
        elif self.config.level == PerformanceLevel.STANDARD:
            return 100
        else:  # BASIC
            return 50
    
    async def get_stt_optimizer(self) -> Optional[STTPerformanceOptimizer]:
        """Get STT optimizer if available"""
        return self.stt_optimizer
    
    async def get_tts_optimizer(self) -> Optional[TTSPerformanceOptimizer]:
        """Get TTS optimizer if available"""
        return self.tts_optimizer
    
    async def get_decision_optimizer(self) -> Optional[VoiceDecisionOptimizer]:
        """Get decision optimizer if available"""
        return self.decision_optimizer
    
    async def run_performance_validation(self, duration_minutes: int = 5) -> Dict[str, Any]:
        """
        Run performance validation if enabled.
        
        Args:
            duration_minutes: Duration for validation testing
            
        Returns:
            Validation report or empty dict if disabled
        """
        if not self.is_enabled or not self.validation_suite:
            logger.warning("Performance validation not available - system disabled or not initialized")
            return {}
        
        if not self.config.load_test_enabled:
            logger.info("Load testing disabled via configuration")
            return {"status": "load_test_disabled"}
        
        logger.info(f"Running performance validation ({duration_minutes} minutes)")
        
        try:
            report = await self.validation_suite.run_full_validation(duration_minutes)
            
            # Export report
            report_data = {
                "validation_status": report.overall_status.value,
                "production_readiness": report.production_readiness.value,
                "compliance_percentage": report.compliance_percentage,
                "passed_tests": report.passed_tests,
                "failed_tests": report.failed_tests,
                "warning_tests": report.warning_tests,
                "recommendations": report.recommendations,
                "critical_issues": report.critical_issues
            }
            
            logger.info(f"Performance validation completed - Status: {report.overall_status.value}, "
                       f"Compliance: {report.compliance_percentage:.1f}%")
            
            return report_data
            
        except Exception as e:
            logger.error(f"Performance validation failed: {e}")
            return {"status": "validation_error", "error": str(e)}
    
    async def get_performance_metrics(self) -> Dict[str, Any]:
        """Get current performance metrics"""
        if not self.is_enabled:
            return {"status": "disabled"}
        
        metrics = {
            "system_status": {
                "enabled": self.is_enabled,
                "level": self.config.level.value,
                "monitoring_active": self._monitoring_active,
                "components_initialized": self._components_status
            }
        }
        
        # Add component metrics
        if self.stt_optimizer:
            metrics["stt_optimizer"] = self.stt_optimizer.get_performance_summary()
        
        if self.tts_optimizer:
            metrics["tts_optimizer"] = self.tts_optimizer.get_performance_summary()
        
        if self.decision_optimizer:
            metrics["decision_optimizer"] = self.decision_optimizer.get_performance_summary()
        
        if self.integration_monitor:
            metrics["integration_monitor"] = self.integration_monitor.get_monitoring_dashboard_data()
        
        return metrics
    
    async def get_status(self) -> PerformanceStatus:
        """Get current performance system status"""
        performance_metrics = await self.get_performance_metrics()
        
        return PerformanceStatus(
            enabled=self.is_enabled,
            level=self.config.level,
            components_initialized=self._components_status,
            monitoring_active=self._monitoring_active,
            last_optimization=datetime.now() if self._initialized else None,
            performance_metrics=performance_metrics
        )
    
    async def shutdown(self) -> None:
        """Shutdown performance system gracefully"""
        logger.info("Shutting down performance system")
        
        try:
            # Stop monitoring
            if self.integration_monitor and self._monitoring_active:
                await self.integration_monitor.stop_monitoring()
                self._monitoring_active = False
            
            # Cleanup optimizers
            if self.stt_optimizer:
                await self.stt_optimizer.cleanup_connection_pools()
            
            if self.tts_optimizer:
                await self.tts_optimizer.cleanup_connection_pools()
            
            # Reset state
            self._initialized = False
            self._components_status.clear()
            
            logger.info("Performance system shutdown completed")
            
        except Exception as e:
            logger.error(f"Error during performance system shutdown: {e}")
    
    def __repr__(self) -> str:
        return (f"VoicePerformanceManager(enabled={self.is_enabled}, "
                f"level={self.config.level.value}, "
                f"monitoring={self._monitoring_active})")


# Factory function for easy integration
async def create_performance_manager(available_providers: Optional[List[ProviderType]] = None) -> VoicePerformanceManager:
    """
    Factory function to create and initialize performance manager.
    
    Args:
        available_providers: List of available voice providers
        
    Returns:
        Initialized VoicePerformanceManager
    """
    config = PerformanceConfig.from_env()
    manager = VoicePerformanceManager(config)
    
    if available_providers:
        await manager.initialize(available_providers)
    
    return manager
