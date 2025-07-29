"""
Enhanced Provider Factory Interface
Phase 3.5.3.2 - File splitting для улучшения поддерживаемости
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Union, List, Optional, TYPE_CHECKING

from .types import ProviderCategory

if TYPE_CHECKING:
    from .models import ProviderInfo, ProviderHealthInfo
    from ..stt.base_stt import BaseSTTProvider
    from ..tts.base_tts import BaseTTSProvider


class IEnhancedProviderFactory(ABC):
    """
    Enhanced abstract provider factory interface.
    Implements Interface Segregation Principle - comprehensive interface для advanced factory operations.
    Follows LSP - all implementations должны быть fully substitutable.
    """

    @abstractmethod
    async def create_provider(
        self,
        provider_name: str,
        config: Dict[str, Any]
    ) -> Union["BaseSTTProvider", "BaseTTSProvider"]:
        """Create provider instance with enhanced error handling"""
        raise NotImplementedError

    @abstractmethod
    def register_provider(self, provider_info: "ProviderInfo") -> None:
        """Register new provider in registry"""
        raise NotImplementedError

    @abstractmethod
    def get_available_providers(
        self,
        category: Optional[ProviderCategory] = None,
        enabled_only: bool = True
    ) -> List["ProviderInfo"]:
        """Get list of available providers with filtering"""
        raise NotImplementedError

    @abstractmethod
    async def health_check(self, provider_name: Optional[str] = None) -> Dict[str, "ProviderHealthInfo"]:
        """Enhanced provider health check"""
        raise NotImplementedError

    @abstractmethod
    def get_provider_info(self, provider_name: str) -> Optional["ProviderInfo"]:
        """Get provider metadata"""
        raise NotImplementedError
