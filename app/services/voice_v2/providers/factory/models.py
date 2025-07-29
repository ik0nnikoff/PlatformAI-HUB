"""
Provider Models for Enhanced Voice Provider Factory
Phase 3.5.3.2 - File splitting для улучшения поддерживаемости
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime

from .types import ProviderCategory, ProviderType, ProviderStatus
from app.services.voice_v2.core.exceptions import ConfigurationError


@dataclass
class ProviderHealthInfo:
    """Provider health information tracking"""
    status: ProviderStatus = ProviderStatus.UNKNOWN
    last_check: Optional[datetime] = None
    failure_count: int = 0
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    response_time_avg: float = 0.0
    
    def record_success(self, response_time: float) -> None:
        """Record successful operation"""
        self.status = ProviderStatus.HEALTHY
        self.last_check = datetime.now()
        self.consecutive_failures = 0
        self.response_time_avg = (self.response_time_avg + response_time) / 2
    
    def record_failure(self, error: str) -> None:
        """Record failed operation"""
        self.failure_count += 1
        self.consecutive_failures += 1
        self.last_error = error
        self.last_check = datetime.now()
        
        # Update status based on consecutive failures
        if self.consecutive_failures >= 5:
            self.status = ProviderStatus.UNHEALTHY
        elif self.consecutive_failures >= 2:
            self.status = ProviderStatus.DEGRADED


@dataclass
class ProviderInfo:
    """
    Enhanced provider metadata structure.
    Implements Interface Segregation - contains all necessary metadata для comprehensive management.
    """
    name: str
    category: ProviderCategory
    provider_type: ProviderType
    module_path: str
    class_name: str
    description: str = ""
    version: str = "1.0.0"
    dependencies: List[str] = field(default_factory=list)
    config_schema: Dict[str, Any] = field(default_factory=dict)
    priority: int = 100  # Lower number = higher priority
    enabled: bool = True
    health_info: ProviderHealthInfo = field(default_factory=ProviderHealthInfo)
    
    def __post_init__(self):
        """Validate provider info after initialization"""
        if not self.name:
            raise ConfigurationError("Provider name cannot be empty")
        if not self.module_path:
            raise ConfigurationError("Provider module_path cannot be empty")
