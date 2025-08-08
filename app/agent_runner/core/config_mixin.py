"""
Базовый миксин для работы с конфигурацией агентов.
Обеспечивает единообразный доступ к настройкам модели и агента.
"""

from typing import Dict, Any, List, Optional
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

    def _extract_kb_ids_from_tool_message(self, tool_message) -> List[str]:
        """
        Извлекает Knowledge Base IDs из ToolMessage.

        Args:
            tool_message: ToolMessage объект из LangChain

        Returns:
            List[str]: Список KB IDs, связанных с этим инструментом
        """
        if not hasattr(tool_message, 'name') or not tool_message.name:
            logger.debug("ToolMessage has no name attribute")
            return []

        tool_name = tool_message.name
        tools_settings = self._get_tools_settings()

        logger.debug(f"Looking for tool with name: {tool_name}")
        logger.debug(f"Available tools: {[t.get('id') + ' (' + t.get('type', 'unknown') + ')' for t in tools_settings]}")

        # Ищем инструмент Knowledge Base с соответствующим ID
        for tool in tools_settings:
            tool_type = tool.get("type")
            tool_id = tool.get("id")
            logger.debug(f"Checking tool: id={tool_id}, type={tool_type}")

            # Поддерживаем различные типы KB инструментов
            if tool_type in ["knowledgeBase", "simple_rag"] and tool_id == tool_name:
                tool_settings = tool.get("settings", {})
                kb_ids = tool_settings.get("knowledgeBaseIds", [])
                logger.debug(f"Found matching tool! KB IDs: {kb_ids}")
                return kb_ids

        logger.debug("No matching tool found")
        return []

    def _get_knowledge_base_model_config(self, kb_ids: List[str]) -> Optional[Dict[str, Any]]:
        """
        Получает конфигурацию модели для указанных KB ID.

        Args:
            kb_ids: Список ID баз знаний

        Returns:
            Optional[Dict[str, Any]]: Конфигурация модели или None
        """
        if not kb_ids:
            return None

        tools_settings = self._get_tools_settings()

        for tool in tools_settings:
            tool_type = tool.get("type")
            # Поддерживаем различные типы KB инструментов
            if tool_type in ["knowledgeBase", "simple_rag"]:
                tool_settings = tool.get("settings", {})
                tool_kb_ids = tool_settings.get("knowledgeBaseIds", [])

                # Проверяем, есть ли пересечение между запрашиваемыми KB ID и настроенными
                if any(kb_id in tool_kb_ids for kb_id in kb_ids):
                    # Проверяем, есть ли специфичная конфигурация модели
                    if any(key in tool_settings for key in ["modelId", "provider", "temperature"]):
                        return {
                            "model_id": tool_settings.get("modelId"),
                            "provider": tool_settings.get("provider"),
                            "temperature": tool_settings.get("temperature")
                        }

        return None

    def _get_kb_specific_node_config(self, node_type: str, kb_ids: List[str] = None) -> Dict[str, Any]:
        """
        Получает конфигурацию узла с учетом настроек базы знаний.

        Args:
            node_type: Тип узла ("grading", "rewrite", "generate")
            kb_ids: Список ID баз знаний

        Returns:
            Dict[str, Any]: Конфигурация узла
        """
        # Получаем базовую конфигурацию
        base_config = self._get_model_config()

        # Специфические настройки для разных типов узлов
        node_specific_overrides = {
            "grading": {
                "streaming": False,
                "temperature": 0.0  # Grading должен быть детерминистическим
            },
            "rewrite": {
                "streaming": False,
                "temperature": 0.0  # Rewrite тоже должен быть детерминистическим
            },
            "generate": {
                "streaming": True,
                "temperature": base_config["temperature"]
            }
        }

        # Применяем специфические настройки узла
        config = {
            "model_id": base_config["model_id"],
            "provider": base_config["provider"],
            "temperature": base_config["temperature"],
            "streaming": True  # По умолчанию
        }

        if node_type in node_specific_overrides:
            config.update(node_specific_overrides[node_type])

        # Если указаны KB IDs, проверяем специфичную конфигурацию модели
        if kb_ids:
            kb_model_config = self._get_knowledge_base_model_config(kb_ids)
            if kb_model_config:
                # Обновляем только те параметры, которые заданы в KB конфигурации
                if kb_model_config.get("model_id"):
                    config["model_id"] = kb_model_config["model_id"]
                if kb_model_config.get("provider"):
                    config["provider"] = kb_model_config["provider"]
                if kb_model_config.get("temperature") is not None:
                    # Для grading и rewrite сохраняем специфичную температуру узла
                    if node_type not in ["grading", "rewrite"]:
                        config["temperature"] = kb_model_config["temperature"]

        return config
