"""
Базовый миксин для работы с конфигурацией агентов.
Обеспечивает единообразный доступ к настройкам модели и агента.
"""

from typing import Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class AgentConfigMixin:
    """
    Миксин для работы с конфигурацией агентов.
    Предоставляет централизованный доступ к настройкам модели и агента.
    """
    
    def __init__(self, *args, **kwargs):
        """Инициализация миксина."""
        super().__init__(*args, **kwargs)
        # Предполагаем, что agent_config будет установлен в классе-наследнике
        self.agent_config: Dict[str, Any] = getattr(self, 'agent_config', {})
    
    def _get_model_settings(self) -> Dict[str, Any]:
        """
        Получает настройки модели из конфигурации агента.
        
        Returns:
            Dict[str, Any]: Словарь настроек модели или пустой словарь если путь не найден.
        """
        if not self.agent_config:
            logger.warning("Agent config is not set or empty")
            return {}
        
        return (self.agent_config
                .get("config", {})
                .get("simple", {})
                .get("settings", {})
                .get("model", {}))
    
    def _get_agent_settings(self) -> Dict[str, Any]:
        """
        Получает общие настройки агента из конфигурации.
        
        Returns:
            Dict[str, Any]: Словарь настроек агента или пустой словарь если путь не найден.
        """
        if not self.agent_config:
            logger.warning("Agent config is not set or empty")
            return {}
        
        return (self.agent_config
                .get("config", {})
                .get("simple", {})
                .get("settings", {}))
    
    def _get_tools_settings(self) -> List[Dict[str, Any]]:
        """
        Получает настройки инструментов из конфигурации агента.
        Поддерживает новую структуру config.simple.settings.tools[].
        
        Returns:
            List[Dict[str, Any]]: Список конфигураций инструментов или пустой список если путь не найден.
        """
        agent_settings = self._get_agent_settings()
        tools = agent_settings.get("tools", [])
        
        # Убеждаемся, что возвращаем список
        if not isinstance(tools, list):
            logger.warning(f"Tools configuration is not a list: {type(tools)}. Returning empty list.")
            return []
        
        return tools
    
    def _get_integrations_settings(self) -> List[Dict[str, Any]]:
        """
        Получает настройки интеграций из конфигурации агента.
        Поддерживает новую структуру config.simple.settings.integrations[].
        
        Returns:
            List[Dict[str, Any]]: Список конфигураций интеграций или пустой список если путь не найден.
        """
        agent_settings = self._get_agent_settings()
        integrations = agent_settings.get("integrations", [])
        
        # Убеждаемся, что возвращаем список
        if not isinstance(integrations, list):
            logger.warning(f"Integrations configuration is not a list: {type(integrations)}. Returning empty list.")
            return []
        
        return integrations
    
    def _get_config_value(self, path: str, default: Any = None) -> Any:
        """
        Получает значение из конфигурации по указанному пути.
        
        Args:
            path: Путь к значению в формате "config.simple.settings.model.provider"
            default: Значение по умолчанию
            
        Returns:
            Any: Значение из конфигурации или default
        """
        if not self.agent_config:
            return default
        
        current = self.agent_config
        for key in path.split('.'):
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        
        return current
    
    def _get_model_value(self, key: str, default: Any = None) -> Any:
        """
        Получает значение из настроек модели.
        
        Args:
            key: Ключ в настройках модели
            default: Значение по умолчанию
            
        Returns:
            Any: Значение из настроек модели или default
        """
        model_settings = self._get_model_settings()
        return model_settings.get(key, default)
    
    def _validate_config_structure(self) -> bool:
        """
        Проверяет корректность структуры конфигурации агента.
        
        Returns:
            bool: True если структура корректна, False в противном случае
        """
        if not self.agent_config:
            logger.error("Agent config is not set")
            return False
        
        required_paths = [
            "config",
            "config.simple",
            "config.simple.settings"
        ]
        
        for path in required_paths:
            if self._get_config_value(path) is None:
                logger.error(f"Missing required config path: {path}")
                return False
        
        return True
    
    def _get_model_config(self) -> Dict[str, Any]:
        """
        Получает конфигурацию модели со всеми основными параметрами.
        
        Returns:
            Dict[str, Any]: Конфигурация модели
        """
        model_settings = self._get_model_settings()
        
        return {
            "model_id": model_settings.get("modelId", "gpt-4o-mini"),
            "provider": model_settings.get("provider", "OpenAI"), 
            "temperature": model_settings.get("temperature", 0.1),
            "system_prompt": model_settings.get("systemPrompt", "You are a helpful AI assistant."),
            "limit_to_kb": model_settings.get("limitToKnowledgeBase", False),
            "answer_in_user_lang": model_settings.get("answerInUserLanguage", True),
            "use_markdown": model_settings.get("useMarkdown", True),
            "enable_context_memory": model_settings.get("enableContextMemory", True),
            "context_memory_depth": model_settings.get("contextMemoryDepth", 10),
            "streaming": model_settings.get("streaming", True)
        }
    
    def get_max_rewrites(self, agent_config: Dict[str, Any]) -> int:
        """
        Получает настройку maxRewrites из конфигурации агента.
        Функция для использования в tools.py.
        
        Args:
            agent_config: Конфигурация агента
            
        Returns:
            int: Значение maxRewrites или 3 по умолчанию
        """
        # Temporarily set agent_config for methods to work
        temp_config = self.agent_config
        self.agent_config = agent_config
        
        try:
            # First try to get from tools settings
            tools_settings = self._get_tools_settings()
            for tool in tools_settings:
                if tool.get("type") == "knowledgeBase":
                    tool_settings = tool.get("settings", {})
                    if "rewriteAttempts" in tool_settings:
                        return tool_settings["rewriteAttempts"]
            
            # Fallback to old path or default
            return (agent_config
                    .get("config", {})
                    .get("simple", {})
                    .get("settings", {})
                    .get("model", {})
                    .get("maxRewrites", 3))
        finally:
            # Restore original config
            self.agent_config = temp_config
