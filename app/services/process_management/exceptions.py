"""
Process management exceptions module.

This module contains all custom exceptions for the process management system.
"""

__all__ = [
    'ProcessManagerError',
    'ProcessStartupError',
    'ProcessTerminationError',
    'ProcessNotFoundError',
    'AgentProcessError',
    'IntegrationProcessError'
]


class ProcessManagerError(Exception):
    """Base exception for all process manager errors"""

    def __init__(self, message: str = "Process manager error"):
        super().__init__(message)
        self.message = message


class ProcessStartupError(ProcessManagerError):
    """Raised when process fails to start"""

    def __init__(self, message: str = "Process failed to start"):
        super().__init__(message)


class ProcessTerminationError(ProcessManagerError):
    """Raised when process fails to terminate gracefully"""

    def __init__(self, message: str = "Process failed to terminate"):
        super().__init__(message)


class ProcessNotFoundError(ProcessManagerError):
    """Raised when process is not found"""

    def __init__(self, message: str = "Process not found"):
        super().__init__(message)


class AgentProcessError(ProcessManagerError):
    """Raised for agent-specific process errors"""

    def __init__(self, message: str = "Agent process error"):
        super().__init__(message)


class IntegrationProcessError(ProcessManagerError):
    """Raised for integration-specific process errors"""

    def __init__(self, message: str = "Integration process error"):
        super().__init__(message)
