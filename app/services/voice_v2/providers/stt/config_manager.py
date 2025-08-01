# STT Configuration Manager Simplification
# Enterprise → Simple pattern transformation
# Features removed:
# - Hot-reload monitoring
# - Multiple config sources (FILE/DATABASE/ENV) - only ENV-based
# - Complex validation and caching
# - Template generation
# - File-based configurations
#
# Features preserved:
# - Basic provider configuration from environment
# - Simple validation
# - Agent-specific settings

import os
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from app.core.logging_config import setup_logger


@dataclass
class STTProviderConfig:
    """Simplified STT provider configuration"""
    provider: str
    enabled: bool
    priority: int
    settings: Dict[str, Any]


@dataclass  
class AgentSTTConfig:
    """Simplified agent STT configuration"""
    agent_id: str
    enabled: bool
    providers: List[STTProviderConfig]
    custom_settings: Dict[str, Any]


class STTConfigManager:
    """
    Simplified STT configuration manager
    
    Replaces enterprise configuration system with basic environment-based setup.
    Removes hot-reload, multiple sources, file management complexity.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        """Initialize simplified configuration manager"""
        self.logger = logger or setup_logger("stt_config_manager")
        self._config_cache: Dict[str, AgentSTTConfig] = {}

    def get_agent_config(self, agent_id: str, voice_config: Optional[Dict[str, Any]] = None) -> AgentSTTConfig:
        """
        Get agent STT configuration
        
        Priority:
        1. Provided voice_config
        2. Cached configuration
        3. Environment defaults
        """
        # Check cache first
        if agent_id in self._config_cache:
            return self._config_cache[agent_id]

        # Create configuration
        if voice_config and voice_config.get("providers"):
            config = self._create_config_from_voice_settings(agent_id, voice_config)
        else:
            config = self._create_default_config(agent_id)

        # Cache and return
        self._config_cache[agent_id] = config
        return config

    def _create_config_from_voice_settings(
        self, 
        agent_id: str, 
        voice_config: Dict[str, Any]
    ) -> AgentSTTConfig:
        """Create configuration from agent voice settings"""
        providers = []
        
        for provider_data in voice_config.get("providers", []):
            if provider_data.get("provider") in ["openai", "google", "yandex"]:
                provider_config = STTProviderConfig(
                    provider=provider_data["provider"],
                    enabled=provider_data.get("enabled", True),
                    priority=provider_data.get("priority", 1),
                    settings=provider_data.get("settings", {})
                )
                
                # Validate provider has required settings
                if self._validate_provider(provider_config):
                    providers.append(provider_config)

        return AgentSTTConfig(
            agent_id=agent_id,
            enabled=voice_config.get("enabled", True),
            providers=providers,
            custom_settings=voice_config.get("custom_settings", {})
        )

    def _create_default_config(self, agent_id: str) -> AgentSTTConfig:
        """Create default configuration from environment"""
        providers = []

        # OpenAI provider
        if os.getenv("OPENAI_API_KEY"):
            providers.append(STTProviderConfig(
                provider="openai",
                enabled=True,
                priority=1,
                settings={
                    "api_key": os.getenv("OPENAI_API_KEY"),
                    "model": "whisper-1"
                }
            ))

        # Google provider  
        if os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("GOOGLE_CLOUD_PROJECT_ID"):
            providers.append(STTProviderConfig(
                provider="google",
                enabled=True,
                priority=2,
                settings={
                    "credentials_path": os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
                    "project_id": os.getenv("GOOGLE_CLOUD_PROJECT_ID")
                }
            ))

        # Yandex provider
        if os.getenv("YANDEX_API_KEY") and os.getenv("YANDEX_FOLDER_ID"):
            providers.append(STTProviderConfig(
                provider="yandex", 
                enabled=True,
                priority=3,
                settings={
                    "api_key": os.getenv("YANDEX_API_KEY"),
                    "folder_id": os.getenv("YANDEX_FOLDER_ID")
                }
            ))

        return AgentSTTConfig(
            agent_id=agent_id,
            enabled=True,
            providers=providers,
            custom_settings={}
        )

    def _validate_provider(self, provider: STTProviderConfig) -> bool:
        """Basic provider validation"""
        settings = provider.settings

        if provider.provider == "openai":
            return bool(settings.get("api_key"))
        elif provider.provider == "google":
            return bool(settings.get("credentials_path") or settings.get("project_id"))
        elif provider.provider == "yandex":
            return bool(settings.get("api_key") and settings.get("folder_id"))
        
        return False

    def clear_cache(self, agent_id: Optional[str] = None) -> None:
        """Clear configuration cache"""
        if agent_id:
            self._config_cache.pop(agent_id, None)
        else:
            self._config_cache.clear()


# Export interface
__all__ = [
    "STTProviderConfig",
    "AgentSTTConfig", 
    "STTConfigManager"
]
