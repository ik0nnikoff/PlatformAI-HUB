"""
Configuration-based initialization для STT провайдеров

Реализует:
- Configuration validation и type safety
- Environment-based configuration override
- Configuration templates для различных deployment scenarios
- Hot-reload configuration support
"""

import os
import json
import yaml
from typing import Dict, List, Any, Optional
from pathlib import Path
from dataclasses import dataclass, field, asdict
from enum import Enum
import logging

from app.services.voice_v2.providers.stt.dynamic_loader import ProviderLoadingConfig
from app.core.logging_config import setup_logger


class ConfigFormat(str, Enum):
    """Supported configuration formats"""
    JSON = "json"
    YAML = "yaml"
    ENV = "env"


class ConfigSource(str, Enum):
    """Configuration sources"""
    FILE = "file"
    ENVIRONMENT = "environment"
    DATABASE = "database"
    REMOTE = "remote"


@dataclass
class STTSystemConfig:
    """System-wide STT configuration"""
    enabled: bool = True
    default_language: str = "auto"
    max_file_size_mb: int = 25
    max_duration_seconds: int = 120
    cache_ttl_seconds: int = 3600
    loading_config: ProviderLoadingConfig = field(default_factory=ProviderLoadingConfig)

    # Performance settings
    connection_pool_size: int = 10
    per_host_connections: int = 5
    request_timeout: int = 30

    # Fallback settings
    fallback_enabled: bool = True
    fallback_timeout: int = 5
    max_fallback_attempts: int = 3


@dataclass
class AgentSTTConfig:
    """Agent-specific STT configuration"""
    agent_id: str
    enabled: bool = True
    providers: List[Dict[str, Any]] = field(default_factory=list)
    system_config: STTSystemConfig = field(default_factory=STTSystemConfig)
    custom_settings: Dict[str, Any] = field(default_factory=dict)


class ConfigurationError(Exception):
    """Configuration error exception for STT configuration issues"""

    def __init__(self, message: str, config_path: Optional[str] = None):
        super().__init__(message)
        self.config_path = config_path

    def __str__(self) -> str:
        if self.config_path:
            return f"Configuration error in {self.config_path}: {super().__str__()}"
        return super().__str__()


class STTConfigManager:
    """
    Manager для STT configuration

    Implements:
    - Configuration loading from multiple sources
    - Environment-based overrides
    - Configuration validation
    - Hot-reload support
    """

    def __init__(
        self,
        config_dir: Optional[Path] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize configuration manager"""
        self.logger = logger or setup_logger("stt_config_manager")
        self.config_dir = config_dir or Path("config/voice_v2/stt")

        # Configuration cache
        self._config_cache: Dict[str, AgentSTTConfig] = {}
        self._system_config: Optional[STTSystemConfig] = None
        self._config_timestamps: Dict[str, float] = {}

        # Environment prefix for overrides
        self.env_prefix = "VOICE_V2_STT"

    def load_system_config(
        self,
        config_path: Optional[Path] = None,
        config_format: ConfigFormat = ConfigFormat.YAML
    ) -> STTSystemConfig:
        """
        Load system-wide STT configuration

        Args:
            config_path: Path to configuration file
            config_format: Configuration format

        Returns:
            System configuration
        """
        self.logger.debug("Loading system STT configuration")

        try:
            # Default config path
            if config_path is None:
                config_path = self.config_dir / f"system.{config_format.value}"

            # Load base configuration
            if config_path.exists():
                config_data = self._load_config_file(config_path, config_format)
            else:
                self.logger.warning("System config file not found: %s", config_path)
                config_data = {}

            # Apply environment overrides
            config_data = self._apply_env_overrides(config_data, "SYSTEM")

            # Create configuration object
            loading_config_data = config_data.pop("loading_config", {})
            loading_config = ProviderLoadingConfig(**loading_config_data)

            system_config = STTSystemConfig(
                loading_config=loading_config,
                **config_data
            )

            # Cache configuration
            self._system_config = system_config

            self.logger.info("System STT configuration loaded successfully")
            return system_config

        except Exception as e:
            self.logger.error("Failed to load system configuration: %s", e)
            # Return default configuration on error
            return STTSystemConfig()

    def load_agent_config(
        self,
        agent_id: str,
        config_source: ConfigSource = ConfigSource.FILE,
        config_data: Optional[Dict[str, Any]] = None
    ) -> AgentSTTConfig:
        """
        Load agent-specific STT configuration

        Args:
            agent_id: Agent identifier
            config_source: Source of configuration
            config_data: Configuration data (for non-file sources)

        Returns:
            Agent configuration
        """
        self.logger.debug("Loading STT configuration for agent %s", agent_id)

        try:
            if config_source == ConfigSource.FILE:
                return self._load_agent_config_from_file(agent_id)
            elif config_source == ConfigSource.ENVIRONMENT:
                return self._load_agent_config_from_env(agent_id)
            elif config_source == ConfigSource.DATABASE:
                return self._load_agent_config_from_db(agent_id, config_data)
            else:
                raise ConfigurationError(f"Unsupported config source: {config_source}")

        except Exception as e:
            self.logger.error("Failed to load agent configuration for %s: %s", agent_id, e)
            # Return default configuration on error
            return self._create_default_agent_config(agent_id)

    def _load_agent_config_from_file(self, agent_id: str) -> AgentSTTConfig:
        """Load agent configuration from file"""
        config_paths = [
            self.config_dir / f"agents/{agent_id}.yaml",
            self.config_dir / f"agents/{agent_id}.json",
            self.config_dir / "agents/default.yaml",
            self.config_dir / "agents/default.json"
        ]

        config_data = {}
        for config_path in config_paths:
            if config_path.exists():
                config_format = ConfigFormat.YAML if config_path.suffix == ".yaml" else ConfigFormat.JSON
                config_data = self._load_config_file(config_path, config_format)
                break

        # Apply environment overrides
        config_data = self._apply_env_overrides(config_data, f"AGENT_{agent_id.upper()}")

        return self._create_agent_config(agent_id, config_data)

    def _load_agent_config_from_env(self, agent_id: str) -> AgentSTTConfig:
        """Load agent configuration from environment variables"""
        config_data = {}

        # Load provider configurations from environment
        providers = []
        provider_types = ["openai", "google", "yandex"]

        for provider_type in provider_types:
            env_key = f"{self.env_prefix}_AGENT_{agent_id.upper()}_{provider_type.upper()}_ENABLED"
            if os.getenv(env_key, "").lower() in ("true", "1", "yes"):
                provider_config = self._load_provider_config_from_env(agent_id, provider_type)
                providers.append(provider_config)

        if providers:
            config_data["providers"] = providers

        return self._create_agent_config(agent_id, config_data)

    def _load_agent_config_from_db(
        self,
        agent_id: str,
        config_data: Optional[Dict[str, Any]]
    ) -> AgentSTTConfig:
        """Load agent configuration from database/external source"""
        if not config_data:
            return self._create_default_agent_config(agent_id)

        return self._create_agent_config(agent_id, config_data)

    def _load_provider_config_from_env(self, agent_id: str, provider_type: str) -> Dict[str, Any]:
        """Load provider configuration from environment variables"""
        prefix = f"{self.env_prefix}_AGENT_{agent_id.upper()}_{provider_type.upper()}"

        config = {
            "provider": provider_type,
            "enabled": os.getenv(f"{prefix}_ENABLED", "true").lower() in ("true", "1", "yes"),
            "priority": int(os.getenv(f"{prefix}_PRIORITY", "1")),
            "settings": {}
        }

        # Provider-specific settings
        if provider_type == "openai":
            config["settings"]["api_key"] = os.getenv(
                f"{prefix}_API_KEY", os.getenv("OPENAI_API_KEY"))
            config["settings"]["model"] = os.getenv(f"{prefix}_MODEL", "whisper-1")
        elif provider_type == "google":
            config["settings"]["credentials_path"] = os.getenv(f"{prefix}_CREDENTIALS_PATH")
            config["settings"]["project_id"] = os.getenv(f"{prefix}_PROJECT_ID")
        elif provider_type == "yandex":
            config["settings"]["api_key"] = os.getenv(
                f"{prefix}_API_KEY", os.getenv("YANDEX_API_KEY"))
            config["settings"]["folder_id"] = os.getenv(
                f"{prefix}_FOLDER_ID", os.getenv("YANDEX_FOLDER_ID"))

        return config

    def _create_agent_config(self, agent_id: str, config_data: Dict[str, Any]) -> AgentSTTConfig:
        """Create agent configuration object"""
        # Load system config if not loaded
        if self._system_config is None:
            self._system_config = self.load_system_config()

        # Validate provider configurations
        providers = config_data.get("providers", [])
        validated_providers = []

        for provider_config in providers:
            try:
                validated_config = self._validate_provider_config(provider_config)
                validated_providers.append(validated_config)
            except Exception as e:
                self.logger.warning("Invalid provider config: %s", e)

        agent_config = AgentSTTConfig(
            agent_id=agent_id,
            enabled=config_data.get("enabled", True),
            providers=validated_providers,
            system_config=self._system_config,
            custom_settings=config_data.get("custom_settings", {})
        )

        # Cache configuration
        self._config_cache[agent_id] = agent_config

        return agent_config

    def _create_default_agent_config(self, agent_id: str) -> AgentSTTConfig:
        """Create default agent configuration"""
        # Load system config if not loaded
        if self._system_config is None:
            self._system_config = self.load_system_config()

        # Create default providers based on environment
        providers = []

        # OpenAI provider
        if os.getenv("OPENAI_API_KEY"):
            providers.append({
                "provider": "openai",
                "enabled": True,
                "priority": 1,
                "settings": {
                    "api_key": os.getenv("OPENAI_API_KEY"),
                    "model": "whisper-1"
                }
            })

        # Google provider
        if os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("GOOGLE_CLOUD_PROJECT_ID"):
            providers.append({
                "provider": "google",
                "enabled": True,
                "priority": 2,
                "settings": {
                    "credentials_path": os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
                    "project_id": os.getenv("GOOGLE_CLOUD_PROJECT_ID")
                }
            })

        # Yandex provider
        if os.getenv("YANDEX_API_KEY") and os.getenv("YANDEX_FOLDER_ID"):
            providers.append({
                "provider": "yandex",
                "enabled": True,
                "priority": 3,
                "settings": {
                    "api_key": os.getenv("YANDEX_API_KEY"),
                    "folder_id": os.getenv("YANDEX_FOLDER_ID")
                }
            })

        return AgentSTTConfig(
            agent_id=agent_id,
            enabled=True,
            providers=providers,
            system_config=self._system_config
        )

    def _validate_provider_config(self, provider_config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate provider configuration"""
        self._validate_required_fields(provider_config)
        provider_type = self._validate_provider_type(provider_config)
        self._validate_provider_specific_settings(provider_type, provider_config)
        return provider_config

    def _validate_required_fields(self, provider_config: Dict[str, Any]):
        """Validate required configuration fields"""
        required_fields = ["provider"]
        for field in required_fields:
            if field not in provider_config:
                raise ConfigurationError(f"Missing required field: {field}")

    def _validate_provider_type(self, provider_config: Dict[str, Any]) -> str:
        """Validate and return provider type"""
        provider_type = provider_config["provider"].lower()
        supported_providers = ["openai", "google", "yandex"]

        if provider_type not in supported_providers:
            raise ConfigurationError(f"Unsupported provider type: {provider_type}")

        return provider_type

    def _validate_provider_specific_settings(
            self, provider_type: str, provider_config: Dict[str, Any]):
        """Validate provider-specific settings"""
        settings = provider_config.get("settings", {})

        validation_map = {
            "openai": self._validate_openai_settings,
            "google": self._validate_google_settings,
            "yandex": self._validate_yandex_settings
        }

        validator = validation_map.get(provider_type)
        if validator:
            validator(settings)

    def _validate_openai_settings(self, settings: Dict[str, Any]):
        """Validate OpenAI-specific settings"""
        if not settings.get("api_key"):
            raise ConfigurationError("OpenAI provider requires api_key")

    def _validate_google_settings(self, settings: Dict[str, Any]):
        """Validate Google-specific settings"""
        if not settings.get("credentials_path") and not settings.get("project_id"):
            raise ConfigurationError("Google provider requires credentials_path or project_id")

    def _validate_yandex_settings(self, settings: Dict[str, Any]):
        """Validate Yandex-specific settings"""
        if not settings.get("api_key") or not settings.get("folder_id"):
            raise ConfigurationError("Yandex provider requires api_key and folder_id")

    def _load_config_file(self, config_path: Path, config_format: ConfigFormat) -> Dict[str, Any]:
        """Load configuration from file"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                if config_format == ConfigFormat.JSON:
                    return json.load(f)
                elif config_format == ConfigFormat.YAML:
                    return yaml.safe_load(f) or {}
                else:
                    raise ConfigurationError(f"Unsupported config format: {config_format}")
        except Exception as e:
            raise ConfigurationError(f"Failed to load config file {config_path}: {e}")

    def _apply_env_overrides(self, config_data: Dict[str, Any], scope: str) -> Dict[str, Any]:
        """Apply environment variable overrides"""
        env_prefix = f"{self.env_prefix}_{scope}"

        # Common overrides
        overrides = {
            f"{env_prefix}_ENABLED": "enabled",
            f"{env_prefix}_DEFAULT_LANGUAGE": "default_language",
            f"{env_prefix}_MAX_FILE_SIZE_MB": "max_file_size_mb",
            f"{env_prefix}_CACHE_TTL_SECONDS": "cache_ttl_seconds"
        }

        for env_key, config_key in overrides.items():
            env_value = os.getenv(env_key)
            if env_value is not None:
                # Type conversion
                if config_key in ["enabled"]:
                    config_data[config_key] = env_value.lower() in ("true", "1", "yes")
                elif config_key in ["max_file_size_mb", "cache_ttl_seconds"]:
                    config_data[config_key] = int(env_value)
                else:
                    config_data[config_key] = env_value

        return config_data

    def save_agent_config(
        self,
        agent_config: AgentSTTConfig,
        config_format: ConfigFormat = ConfigFormat.YAML
    ) -> bool:
        """Save agent configuration to file"""
        try:
            config_path = self.config_dir / "agents" / \
                f"{agent_config.agent_id}.{config_format.value}"
            config_path.parent.mkdir(parents=True, exist_ok=True)

            # Convert to dictionary
            config_dict = asdict(agent_config)

            # Remove agent_id and system_config from saved data
            config_dict.pop("agent_id", None)
            config_dict.pop("system_config", None)

            # Save to file
            with open(config_path, 'w', encoding='utf-8') as f:
                if config_format == ConfigFormat.JSON:
                    json.dump(config_dict, f, indent=2, ensure_ascii=False)
                elif config_format == ConfigFormat.YAML:
                    yaml.dump(config_dict, f, default_flow_style=False, allow_unicode=True)

            self.logger.info("Saved agent configuration: %s", config_path)
            return True

        except Exception as e:
            self.logger.error("Failed to save agent configuration: %s", e)
            return False

    def reload_agent_config(self, agent_id: str) -> AgentSTTConfig:
        """Reload agent configuration"""
        # Remove from cache
        self._config_cache.pop(agent_id, None)

        # Load fresh configuration
        return self.load_agent_config(agent_id)

    def get_cached_config(self, agent_id: str) -> Optional[AgentSTTConfig]:
        """Get cached agent configuration"""
        return self._config_cache.get(agent_id)

    def create_config_template(
        self,
        template_name: str,
        providers: List[str],
        config_format: ConfigFormat = ConfigFormat.YAML
    ) -> bool:
        """Create configuration template"""
        try:
            template_path = self.config_dir / "templates" / f"{template_name}.{config_format.value}"
            template_path.parent.mkdir(parents=True, exist_ok=True)

            # Create template configuration
            template_config = {
                "enabled": True,
                "providers": []
            }

            for provider in providers:
                if provider == "openai":
                    template_config["providers"].append({
                        "provider": "openai",
                        "enabled": True,
                        "priority": 1,
                        "settings": {
                            "api_key": "${OPENAI_API_KEY}",
                            "model": "whisper-1"
                        }
                    })
                elif provider == "google":
                    template_config["providers"].append({
                        "provider": "google",
                        "enabled": True,
                        "priority": 2,
                        "settings": {
                            "credentials_path": "${GOOGLE_APPLICATION_CREDENTIALS}",
                            "project_id": "${GOOGLE_CLOUD_PROJECT_ID}"
                        }
                    })
                elif provider == "yandex":
                    template_config["providers"].append({
                        "provider": "yandex",
                        "enabled": True,
                        "priority": 3,
                        "settings": {
                            "api_key": "${YANDEX_API_KEY}",
                            "folder_id": "${YANDEX_FOLDER_ID}"
                        }
                    })

            # Save template
            with open(template_path, 'w', encoding='utf-8') as f:
                if config_format == ConfigFormat.JSON:
                    json.dump(template_config, f, indent=2, ensure_ascii=False)
                elif config_format == ConfigFormat.YAML:
                    yaml.dump(template_config, f, default_flow_style=False, allow_unicode=True)

            self.logger.info("Created configuration template: %s", template_path)
            return True

        except Exception as e:
            self.logger.error("Failed to create configuration template: %s", e)
            return False


# Export public interface
__all__ = [
    "ConfigFormat",
    "ConfigSource",
    "STTSystemConfig",
    "AgentSTTConfig",
    "STTConfigManager",
    "ConfigurationError"
]
