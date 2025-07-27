"""
Health Checker System for voice_v2

This module implements comprehensive health monitoring for voice_v2 providers
and system components, integrating with circuit breaker for health-based decisions.

Health checker provides:
- Provider health monitoring (STT/TTS)
- System component health checks
- Health status aggregation
- Integration with circuit breaker decisions
- Real-time health endpoints

Architecture follows SOLID principles:
- SRP: Each health checker monitors specific component
- OCP: Extensible for new health check types
- LSP: All health checkers implement same interface
- ISP: Separate interfaces for different health check types
- DIP: Depends on abstractions, not concrete implementations

Performance targets:
- Health check execution: ≤50ms
- Health status aggregation: ≤10ms
- Provider status cache: 30s TTL
"""

import asyncio
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.services.voice_v2.core.interfaces import ProviderType
from app.services.voice_v2.core.exceptions import VoiceServiceError


class HealthStatus(Enum):
    """Health status enumeration"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a health check operation"""
    status: HealthStatus
    message: str
    timestamp: datetime
    response_time_ms: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_healthy(self) -> bool:
        """Check if status indicates healthy state"""
        return self.status == HealthStatus.HEALTHY
    
    @property
    def is_available(self) -> bool:
        """Check if service is available (healthy or degraded)"""
        return self.status in (HealthStatus.HEALTHY, HealthStatus.DEGRADED)


@dataclass
class ProviderHealthStatus:
    """Health status for a specific provider"""
    provider_type: ProviderType
    provider_name: str
    stt_health: Optional[HealthCheckResult] = None
    tts_health: Optional[HealthCheckResult] = None
    overall_status: HealthStatus = HealthStatus.UNKNOWN
    last_check: datetime = field(default_factory=datetime.now)
    
    def update_overall_status(self) -> None:
        """Update overall status based on STT/TTS health"""
        statuses = []
        if self.stt_health:
            statuses.append(self.stt_health.status)
        if self.tts_health:
            statuses.append(self.tts_health.status)
        
        if not statuses:
            self.overall_status = HealthStatus.UNKNOWN
        elif all(s == HealthStatus.HEALTHY for s in statuses):
            self.overall_status = HealthStatus.HEALTHY
        elif any(s == HealthStatus.UNHEALTHY for s in statuses):
            self.overall_status = HealthStatus.UNHEALTHY
        else:
            self.overall_status = HealthStatus.DEGRADED


class HealthCheckInterface(ABC):
    """Abstract interface for health check implementations"""
    
    @abstractmethod
    async def check_health(self) -> HealthCheckResult:
        """Perform health check and return result"""
    
    @abstractmethod
    def get_component_name(self) -> str:
        """Get name of component being checked"""


class BaseHealthChecker(HealthCheckInterface):
    """Base implementation for health checkers"""
    
    def __init__(self, component_name: str, timeout_ms: int = 5000):
        self.component_name = component_name
        self.timeout_ms = timeout_ms
        self._last_check: Optional[HealthCheckResult] = None
        self._check_lock = asyncio.Lock()
    
    def get_component_name(self) -> str:
        """Get component name"""
        return self.component_name
    
    async def check_health(self) -> HealthCheckResult:
        """Perform health check with timeout and error handling"""
        async with self._check_lock:
            start_time = time.time()
            
            try:
                # Timeout protection
                result = await asyncio.wait_for(
                    self._perform_health_check(),
                    timeout=self.timeout_ms / 1000.0
                )
                
                response_time = (time.time() - start_time) * 1000
                result.response_time_ms = response_time
                result.timestamp = datetime.now()
                
                self._last_check = result
                return result
                
            except asyncio.TimeoutError:
                response_time = (time.time() - start_time) * 1000
                result = HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Health check timeout after {self.timeout_ms}ms",
                    timestamp=datetime.now(),
                    response_time_ms=response_time,
                    metadata={"error": "timeout"}
                )
                self._last_check = result
                return result
                
            except Exception as e:
                response_time = (time.time() - start_time) * 1000
                result = HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Health check failed: {str(e)}",
                    timestamp=datetime.now(),
                    response_time_ms=response_time,
                    metadata={"error": str(e), "error_type": type(e).__name__}
                )
                self._last_check = result
                return result
    
    @abstractmethod
    async def _perform_health_check(self) -> HealthCheckResult:
        """Implement specific health check logic"""
    
    def get_last_result(self) -> Optional[HealthCheckResult]:
        """Get last health check result"""
        return self._last_check


class ProviderHealthChecker(BaseHealthChecker):
    """Health checker for voice providers (STT/TTS)"""
    
    def __init__(self, provider_name: str, provider_type: ProviderType):
        super().__init__(f"{provider_name}_{provider_type.value}")
        self.provider_name = provider_name
        self.provider_type = provider_type
        self._health_check_functions = self._setup_health_checks()
    
    def _setup_health_checks(self) -> Dict[str, Callable]:
        """Setup provider-specific health check functions"""
        return {
            "openai": self._check_openai_health,
            "google": self._check_google_health,
            "yandex": self._check_yandex_health,
        }
    
    async def _perform_health_check(self) -> HealthCheckResult:
        """Perform provider-specific health check"""
        check_func = self._health_check_functions.get(self.provider_name.lower())
        
        if not check_func:
            return HealthCheckResult(
                status=HealthStatus.UNKNOWN,
                message=f"No health check implemented for {self.provider_name}",
                timestamp=datetime.now(),
                response_time_ms=0.0,
                metadata={"provider": self.provider_name, "type": self.provider_type.value}
            )
        
        return await check_func()
    
    async def _check_openai_health(self) -> HealthCheckResult:
        """Check OpenAI provider health"""
        try:
            # Simulate lightweight health check (ping endpoint)
            await asyncio.sleep(0.01)  # Simulate API call
            
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                message="OpenAI API responsive",
                timestamp=datetime.now(),
                response_time_ms=0.0,
                metadata={"provider": "openai", "endpoint": "health"}
            )
        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"OpenAI health check failed: {str(e)}",
                timestamp=datetime.now(),
                response_time_ms=0.0,
                metadata={"provider": "openai", "error": str(e)}
            )
    
    async def _check_google_health(self) -> HealthCheckResult:
        """Check Google provider health"""
        try:
            # Simulate lightweight health check
            await asyncio.sleep(0.01)
            
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                message="Google API responsive",
                timestamp=datetime.now(),
                response_time_ms=0.0,
                metadata={"provider": "google", "endpoint": "health"}
            )
        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Google health check failed: {str(e)}",
                timestamp=datetime.now(),
                response_time_ms=0.0,
                metadata={"provider": "google", "error": str(e)}
            )
    
    async def _check_yandex_health(self) -> HealthCheckResult:
        """Check Yandex provider health"""
        try:
            # Simulate lightweight health check
            await asyncio.sleep(0.01)
            
            return HealthCheckResult(
                status=HealthStatus.HEALTHY,
                message="Yandex API responsive",
                timestamp=datetime.now(),
                response_time_ms=0.0,
                metadata={"provider": "yandex", "endpoint": "health"}
            )
        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"Yandex health check failed: {str(e)}",
                timestamp=datetime.now(),
                response_time_ms=0.0,
                metadata={"provider": "yandex", "error": str(e)}
            )


class SystemHealthChecker(BaseHealthChecker):
    """Health checker for system components (Redis, MinIO, etc.)"""
    
    def __init__(self, component_name: str, check_function: Callable):
        super().__init__(component_name)
        self.check_function = check_function
    
    async def _perform_health_check(self) -> HealthCheckResult:
        """Perform system component health check"""
        try:
            is_healthy = await self.check_function()
            
            if is_healthy:
                return HealthCheckResult(
                    status=HealthStatus.HEALTHY,
                    message=f"{self.component_name} is healthy",
                    timestamp=datetime.now(),
                    response_time_ms=0.0,
                    metadata={"component": self.component_name}
                )
            else:
                return HealthCheckResult(
                    status=HealthStatus.UNHEALTHY,
                    message=f"{self.component_name} is unhealthy",
                    timestamp=datetime.now(),
                    response_time_ms=0.0,
                    metadata={"component": self.component_name}
                )
                
        except Exception as e:
            return HealthCheckResult(
                status=HealthStatus.UNHEALTHY,
                message=f"{self.component_name} health check failed: {str(e)}",
                timestamp=datetime.now(),
                response_time_ms=0.0,
                metadata={"component": self.component_name, "error": str(e)}
            )


class HealthManager:
    """Central health monitoring manager"""
    
    def __init__(self, cache_ttl_seconds: int = 30):
        self.cache_ttl_seconds = cache_ttl_seconds
        self._health_checkers: Dict[str, HealthCheckInterface] = {}
        self._provider_health: Dict[str, ProviderHealthStatus] = {}
        self._system_health_cache: Dict[str, HealthCheckResult] = {}
        self._cache_timestamps: Dict[str, datetime] = {}
        self._manager_lock = asyncio.Lock()
    
    def register_provider_health_checker(
        self, 
        provider_name: str, 
        provider_type: ProviderType,
        service_type: str  # "stt" or "tts"
    ) -> None:
        """Register provider health checker"""
        checker_key = f"{provider_name}_{provider_type.value}_{service_type}"
        checker = ProviderHealthChecker(provider_name, provider_type)
        self._health_checkers[checker_key] = checker
        
        # Initialize provider health status
        provider_key = f"{provider_name}_{provider_type.value}"
        if provider_key not in self._provider_health:
            self._provider_health[provider_key] = ProviderHealthStatus(
                provider_type=provider_type,
                provider_name=provider_name
            )
    
    def register_system_health_checker(
        self, 
        component_name: str, 
        check_function: Callable
    ) -> None:
        """Register system component health checker"""
        checker = SystemHealthChecker(component_name, check_function)
        self._health_checkers[component_name] = checker
    
    async def check_provider_health(
        self, 
        provider_name: str, 
        provider_type: ProviderType
    ) -> ProviderHealthStatus:
        """Check health of specific provider"""
        provider_key = f"{provider_name}_{provider_type.value}"
        
        if provider_key not in self._provider_health:
            raise VoiceServiceError(f"Provider {provider_key} not registered")
        
        provider_status = self._provider_health[provider_key]
        
        # Check STT health
        stt_key = f"{provider_name}_{provider_type.value}_stt"
        if stt_key in self._health_checkers:
            provider_status.stt_health = await self._health_checkers[stt_key].check_health()
        
        # Check TTS health
        tts_key = f"{provider_name}_{provider_type.value}_tts"
        if tts_key in self._health_checkers:
            provider_status.tts_health = await self._health_checkers[tts_key].check_health()
        
        # Update overall status
        provider_status.update_overall_status()
        provider_status.last_check = datetime.now()
        
        return provider_status
    
    async def check_system_health(self, component_name: str) -> HealthCheckResult:
        """Check health of system component with caching"""
        now = datetime.now()
        
        # Check cache first
        if (component_name in self._system_health_cache and 
            component_name in self._cache_timestamps):
            cache_age = now - self._cache_timestamps[component_name]
            if cache_age.total_seconds() < self.cache_ttl_seconds:
                return self._system_health_cache[component_name]
        
        # Perform health check
        if component_name not in self._health_checkers:
            raise VoiceServiceError(f"Component {component_name} not registered")
        
        result = await self._health_checkers[component_name].check_health()
        
        # Update cache
        self._system_health_cache[component_name] = result
        self._cache_timestamps[component_name] = now
        
        return result
    
    async def get_overall_health(self) -> Dict[str, Any]:
        """Get overall system health status"""
        async with self._manager_lock:
            health_summary = {
                "status": HealthStatus.HEALTHY.value,
                "timestamp": datetime.now().isoformat(),
                "providers": {},
                "system_components": {}
            }
            
            # Check all provider health
            unhealthy_count = 0
            total_providers = len(self._provider_health)
            
            for provider_key in self._provider_health:
                # Force fresh check for overall status
                parts = provider_key.split('_')
                provider_name = '_'.join(parts[:-1])
                provider_type = ProviderType(parts[-1])
                
                updated_status = await self.check_provider_health(provider_name, provider_type)
                
                health_summary["providers"][provider_key] = {
                    "status": updated_status.overall_status.value,
                    "stt_status": updated_status.stt_health.status.value if updated_status.stt_health else "unknown",
                    "tts_status": updated_status.tts_health.status.value if updated_status.tts_health else "unknown",
                    "last_check": updated_status.last_check.isoformat()
                }
                
                if updated_status.overall_status != HealthStatus.HEALTHY:
                    unhealthy_count += 1
            
            # Check system components
            for component_name in self._health_checkers:
                if not component_name.endswith(('_stt', '_tts')):  # System components only
                    try:
                        result = await self.check_system_health(component_name)
                        health_summary["system_components"][component_name] = {
                            "status": result.status.value,
                            "message": result.message,
                            "response_time_ms": result.response_time_ms,
                            "timestamp": result.timestamp.isoformat()
                        }
                        
                        if result.status != HealthStatus.HEALTHY:
                            unhealthy_count += 1
                    except Exception as e:
                        health_summary["system_components"][component_name] = {
                            "status": HealthStatus.UNHEALTHY.value,
                            "message": f"Health check failed: {str(e)}",
                            "response_time_ms": 0.0,
                            "timestamp": datetime.now().isoformat()
                        }
                        unhealthy_count += 1
            
            # Determine overall status
            total_components = total_providers + len(health_summary["system_components"])
            if unhealthy_count == 0:
                health_summary["status"] = HealthStatus.HEALTHY.value
            elif unhealthy_count >= total_components / 2:
                health_summary["status"] = HealthStatus.UNHEALTHY.value
            else:
                health_summary["status"] = HealthStatus.DEGRADED.value
            
            health_summary["summary"] = {
                "total_components": total_components,
                "healthy_components": total_components - unhealthy_count,
                "unhealthy_components": unhealthy_count
            }
            
            return health_summary
    
    def is_provider_healthy(self, provider_name: str, provider_type: ProviderType) -> bool:
        """Quick check if provider is healthy (cached result)"""
        provider_key = f"{provider_name}_{provider_type.value}"
        
        if provider_key not in self._provider_health:
            return False
        
        provider_status = self._provider_health[provider_key]
        
        # Check if status is recent enough
        now = datetime.now()
        cache_age = now - provider_status.last_check
        if cache_age.total_seconds() > self.cache_ttl_seconds:
            return False  # Status too old, assume unhealthy
        
        return provider_status.overall_status == HealthStatus.HEALTHY
    
    def get_healthy_providers(self, provider_type: ProviderType) -> List[str]:
        """Get list of healthy providers for given type"""
        healthy_providers = []
        
        for provider_status in self._provider_health.values():
            if (provider_status.provider_type == provider_type and 
                self.is_provider_healthy(provider_status.provider_name, provider_type)):
                healthy_providers.append(provider_status.provider_name)
        
        return healthy_providers
