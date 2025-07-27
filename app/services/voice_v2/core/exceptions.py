"""
Voice_v2 Exception Hierarchy

Following Single Responsibility Principle (SRP):
- Each exception class has ONE clear responsibility
- Clear inheritance hierarchy for proper error handling
- Specific exception types for different failure scenarios

Design principles:
- VoiceServiceError: Base exception for all voice_v2 errors
- VoiceServiceTimeout: Specifically for timeout scenarios
- VoiceProviderError: Provider-specific failures
- VoiceConfigurationError: Configuration validation failures
"""

from typing import Optional, Dict, Any


class VoiceServiceError(Exception):
    """
    Base exception for all voice_v2 service errors
    
    Single Responsibility: Root exception for voice_v2 system
    Provides consistent error interface with context information
    """
    
    def __init__(
        self, 
        message: str, 
        error_code: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        """
        Initialize voice service error
        
        Args:
            message: Human-readable error description
            error_code: Machine-readable error identifier
            context: Additional error context information
        """
        super().__init__(message)
        self.message = message
        self.error_code = error_code or "VOICE_SERVICE_ERROR"
        self.context = context or {}
    
    def __str__(self) -> str:
        """String representation with context"""
        if self.context:
            return f"{self.message} (Code: {self.error_code}, Context: {self.context})"
        return f"{self.message} (Code: {self.error_code})"
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for logging/serialization"""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "error_code": self.error_code,
            "context": self.context
        }


class VoiceServiceTimeout(VoiceServiceError):
    """
    Timeout-specific exception for voice operations
    
    Single Responsibility: Handle timeout scenarios specifically
    Used when operations exceed configured time limits
    """
    
    def __init__(
        self, 
        operation: str, 
        timeout_seconds: float,
        provider: Optional[str] = None
    ):
        """
        Initialize timeout error
        
        Args:
            operation: Operation that timed out (STT/TTS)
            timeout_seconds: Configured timeout value
            provider: Provider name that timed out
        """
        context = {
            "operation": operation,
            "timeout_seconds": timeout_seconds,
            "provider": provider
        }
        
        message = f"{operation} operation timed out after {timeout_seconds}s"
        if provider:
            message += f" (Provider: {provider})"
            
        super().__init__(
            message=message,
            error_code="VOICE_TIMEOUT",
            context=context
        )


class VoiceProviderError(VoiceServiceError):
    """
    Provider-specific exception for external service failures
    
    Single Responsibility: Handle provider-specific errors
    Encapsulates errors from OpenAI, Google, Yandex providers
    """
    
    def __init__(
        self, 
        provider: str,
        operation: str,
        original_error: Optional[Exception] = None,
        status_code: Optional[int] = None
    ):
        """
        Initialize provider error
        
        Args:
            provider: Provider name (openai, google, yandex)
            operation: Failed operation (stt, tts)
            original_error: Original exception from provider
            status_code: HTTP status code if applicable
        """
        context = {
            "provider": provider,
            "operation": operation,
            "status_code": status_code,
            "original_error": str(original_error) if original_error else None
        }
        
        message = f"{provider} {operation} operation failed"
        if status_code:
            message += f" (HTTP {status_code})"
        if original_error:
            message += f": {str(original_error)}"
            
        super().__init__(
            message=message,
            error_code=f"PROVIDER_{provider.upper()}_ERROR",
            context=context
        )


class VoiceConfigurationError(VoiceServiceError):
    """
    Configuration validation exception
    
    Single Responsibility: Handle configuration-related errors
    Used for invalid settings, missing credentials, malformed configs
    """
    
    def __init__(
        self, 
        config_field: str,
        invalid_value: Any = None,
        reason: Optional[str] = None
    ):
        """
        Initialize configuration error
        
        Args:
            config_field: Configuration field that failed validation
            invalid_value: The invalid value that caused the error
            reason: Specific reason for configuration failure
        """
        context = {
            "config_field": config_field,
            "invalid_value": invalid_value,
            "reason": reason
        }
        
        message = f"Invalid configuration for '{config_field}'"
        if reason:
            message += f": {reason}"
        if invalid_value is not None:
            message += f" (Value: {invalid_value})"
            
        super().__init__(
            message=message,
            error_code="VOICE_CONFIG_ERROR",
            context=context
        )


# Exception mapping for quick lookup
EXCEPTION_MAP = {
    "timeout": VoiceServiceTimeout,
    "provider": VoiceProviderError,
    "config": VoiceConfigurationError,
    "base": VoiceServiceError,
}


def create_voice_error(
    error_type: str, 
    **kwargs
) -> VoiceServiceError:
    """
    Factory function for creating voice exceptions
    
    Args:
        error_type: Type of error (timeout, provider, config, base)
        **kwargs: Arguments specific to exception type
    
    Returns:
        Appropriate VoiceServiceError subclass instance
    
    Raises:
        ValueError: If error_type is not recognized
    """
    if error_type not in EXCEPTION_MAP:
        valid_types = ", ".join(EXCEPTION_MAP.keys())
        raise ValueError(f"Unknown error type '{error_type}'. Valid types: {valid_types}")
    
    exception_class = EXCEPTION_MAP[error_type]
    return exception_class(**kwargs)
