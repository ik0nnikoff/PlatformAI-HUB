"""
Process management configuration module.

This module handles configuration management, path resolution, and environment setup.
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Dict
from dataclasses import dataclass

__all__ = ['ProcessDefaults', 'PathResolver', 'ProcessConfiguration', 'EnvironmentConfig']


@dataclass
class ProcessDefaults:
    """Default configuration values for process management"""
    graceful_shutdown_timeout: int = 30
    agent_status_key_template: str = "agent_process:{agent_id}:status"
    integration_status_key_template: str = (
        "integration_process:{integration_type}:{agent_id}:status"
    )

    def __post_init__(self):
        """Initialize status constants after dataclass initialization"""
        # Status constants grouped into a dictionary to reduce attribute count
        self.statuses = {
            'not_found': "not_found",
            'stopped': "stopped",
            'running': "running",
            'starting': "starting",
            'stopping': "stopping",
            'error': "error",
            'error_process_lost': "error_process_lost",
            'error_start_failed': "error_start_failed",
            'error_stop_failed': "error_stop_failed"
        }

    # Legacy property for backward compatibility  # pylint: disable=invalid-name
    @property
    def STATUS_NOT_FOUND(self) -> str:
        """Legacy constant for backward compatibility"""
        return self.statuses['not_found']

    @property
    def STATUS_STOPPED(self) -> str:
        """Legacy constant for backward compatibility"""
        return self.statuses['stopped']

    @property
    def STATUS_RUNNING(self) -> str:
        """Legacy constant for backward compatibility"""
        return self.statuses['running']

    @property
    def STATUS_STARTING(self) -> str:
        """Legacy constant for backward compatibility"""
        return self.statuses['starting']

    @property
    def STATUS_STOPPING(self) -> str:
        """Legacy constant for backward compatibility"""
        return self.statuses['stopping']

    @property
    def STATUS_ERROR(self) -> str:
        """Legacy constant for backward compatibility"""
        return self.statuses['error']

    @property  # pylint: disable=invalid-name
    def GRACEFUL_SHUTDOWN_TIMEOUT(self) -> int:
        """Legacy constant for backward compatibility"""
        return self.graceful_shutdown_timeout

    @property  # pylint: disable=invalid-name
    def STATUS_ERROR_PROCESS_LOST(self) -> str:
        """Legacy constant for backward compatibility"""
        return self.statuses['error_process_lost']

    @property  # pylint: disable=invalid-name
    def STATUS_ERROR_START_FAILED(self) -> str:
        """Legacy constant for backward compatibility"""
        return self.statuses['error_start_failed']

    @property  # pylint: disable=invalid-name
    def STATUS_ERROR_STOP_FAILED(self) -> str:
        """Legacy constant for backward compatibility"""
        return self.statuses['error_stop_failed']

    # Legacy property accessors for individual status values
    @property
    def status_not_found(self) -> str:
        """Status value accessor"""
        return self.statuses['not_found']

    @property
    def status_stopped(self) -> str:
        """Status value accessor"""
        return self.statuses['stopped']

    @property
    def status_running(self) -> str:
        """Status value accessor"""
        return self.statuses['running']

    @property
    def status_starting(self) -> str:
        """Status value accessor"""
        return self.statuses['starting']

    @property
    def status_stopping(self) -> str:
        """Status value accessor"""
        return self.statuses['stopping']

    @property
    def status_error(self) -> str:
        """Status value accessor"""
        return self.statuses['error']

    @property
    def status_error_process_lost(self) -> str:
        """Status value accessor"""
        return self.statuses['error_process_lost']

    @property
    def status_error_start_failed(self) -> str:
        """Status value accessor"""
        return self.statuses['error_start_failed']

    @property
    def status_error_stop_failed(self) -> str:
        """Status value accessor"""
        return self.statuses['error_stop_failed']


class PathResolutionError(Exception):
    """Raised when path resolution fails"""


class PathResolver:
    """Utility class for resolving project paths"""

    @staticmethod
    def find_project_root() -> Path:
        """Find project root by looking for key files"""
        current = Path.cwd()
        key_files = ["pyproject.toml", "setup.py", "requirements.txt", ".git"]

        for parent in [current] + list(current.parents):
            if any((parent / key_file).exists() for key_file in key_files):
                return parent

        # Fallback to current working directory
        fallback_root = Path.cwd()

        # Validate fallback
        if not fallback_root.exists():
            try:
                fallback_root.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise PathResolutionError(
                    f'Failed to determine project root: {e}. '
                    f'Fallback CWD {fallback_root} is also invalid.'
                ) from e

        return fallback_root

    @staticmethod
    def get_script_path(script_name: str, project_root: Path) -> Path:
        """Get path to a script relative to project root"""
        return project_root / "app" / "agent_runner" / script_name

    @staticmethod
    def get_integration_path(integration_type: str, project_root: Path) -> Path:
        """Get path to integration script"""
        return project_root / "app" / "integrations" / integration_type / "main.py"


class EnvironmentConfig:
    """Environment-specific configuration"""

    def __init__(self):
        self.environment = os.getenv('ENVIRONMENT', 'development')
        self.redis_host = os.getenv('REDIS_HOST', 'localhost')
        self.redis_port = int(os.getenv('REDIS_PORT', '6379'))
        self.redis_db = int(os.getenv('REDIS_DB', '0'))

    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.environment == 'development'

    def get_redis_url(self) -> str:
        """Get Redis connection URL"""
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


class ProcessConfiguration:
    """Configuration management for process manager"""

    def __init__(self, custom_config: Dict = None):
        self.defaults = ProcessDefaults()
        self.custom_config = custom_config or {}
        self.env_config = EnvironmentConfig()

        # Core configuration grouped together
        self.core_config = {
            'project_root': PathResolver.find_project_root(),
            'python_executable': self._get_python_executable()
        }

        # Set up agent script path after core config is ready
        self.core_config['agent_script_path'] = PathResolver.get_script_path(
            "runner_main.py", self.core_config['project_root']
        )

        # Agent configuration
        self.agent_config = {
            'runner_module_path': "app.agent_runner.runner_main",
            'runner_script_full_path': str(self.core_config['agent_script_path'])
        }

        # Integration configuration grouped together
        self.integration_config = {
            'module_paths': {
                "TELEGRAM": "app.integrations.telegram.telegram_bot_main",
                "WHATSAPP": "app.integrations.whatsapp.whatsapp_main"
            },
            'script_full_paths': {
                "TELEGRAM": str(
                    self.core_config['project_root'] / "app" / "integrations" /
                    "telegram" / "telegram_bot_main.py"
                ),
                "WHATSAPP": str(
                    self.core_config['project_root'] / "app" / "integrations" /
                    "whatsapp" / "whatsapp_main.py"
                )
            }
        }

        # Process environment variables
        self.process_env = self._init_process_environment()

    # Legacy properties for backward compatibility
    @property
    def project_root(self):
        """Legacy property for project root"""
        return self.core_config['project_root']

    @property
    def python_executable(self) -> str:
        """Legacy property for python executable"""
        return self.core_config['python_executable']

    @property
    def agent_script_path(self):
        """Legacy property for agent script path"""
        return self.core_config['agent_script_path']

    @property
    def agent_runner_module_path(self) -> str:
        """Legacy property for agent runner module path"""
        return self.agent_config['runner_module_path']

    @property
    def agent_runner_script_full_path(self) -> str:
        """Legacy property for agent runner script full path"""
        return self.agent_config['runner_script_full_path']

    @property
    def integration_module_paths(self) -> Dict[str, str]:
        """Legacy property for integration module paths"""
        return self.integration_config['module_paths']

    @property
    def integration_script_full_paths(self) -> Dict[str, str]:
        """Legacy property for integration script full paths"""
        return self.integration_config['script_full_paths']

    @property
    def redis_host(self) -> str:
        """Get Redis host"""
        return self.env_config.redis_host

    @property
    def redis_port(self) -> int:
        """Get Redis port"""
        return self.env_config.redis_port

    @property
    def redis_db(self) -> int:
        """Get Redis database"""
        return self.env_config.redis_db

    def get_graceful_shutdown_timeout(self) -> int:
        """Get timeout for graceful shutdown"""
        return self.custom_config.get(
            'graceful_shutdown_timeout',
            self.defaults.graceful_shutdown_timeout
        )

    def get_agent_status_key(self, agent_id: str) -> str:
        """Get Redis key for agent status"""
        return self.defaults.agent_status_key_template.format(agent_id=agent_id)

    def get_integration_status_key(self, integration_type: str, agent_id: str) -> str:
        """Get Redis key for integration status"""
        return self.defaults.integration_status_key_template.format(
            integration_type=integration_type, agent_id=agent_id
        )

    def get_integration_script_path(self, integration_type: str) -> Path:
        """Get path to integration script"""
        return PathResolver.get_integration_path(integration_type, self.project_root)

    def _get_python_executable(self) -> str:
        """Get the Python executable path with validation"""
        # First try getting from virtual environment
        python_executable = "python"

        # Check if current executable works
        try:
            result = subprocess.run(
                [python_executable, "--version"],
                capture_output=True,
                text=True,
                check=True
            )
            if result.returncode == 0:
                return python_executable
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass

        # Fallback to sys.executable
        try:
            return sys.executable
        except (AttributeError, TypeError):
            return "python"  # Last resort fallback

    def _init_process_environment(self):
        """Initialize process environment variables"""
        process_env = os.environ.copy()
        process_env["PYTHONPATH"] = str(self.project_root) + (
            os.pathsep + process_env.get("PYTHONPATH", "")
            if process_env.get("PYTHONPATH")
            else ""
        )
        process_env["PYTHONUNBUFFERED"] = "1"  # Common practice for scripts
        return process_env
