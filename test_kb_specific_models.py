#!/usr/bin/env python3
"""
Тест для проверки функциональности KB-специфичных моделей в системе PlatformAI agent.
"""

import json
import logging
from typing import Dict, Any, List
from unittest.mock import Mock, MagicMock
from langchain_core.messages import ToolMessage
from app.agent_runner.langgraph.factory import GraphFactory


def create_test_agent_config() -> Dict[str, Any]:
    """Создает тестовую конфигурацию агента с KB-специфичными настройками."""
    return {
        "config": {
            "simple": {
                "settings": {
                    "model": {
                        "provider": "openai",
                        "modelId": "gpt-4o-mini",
                        "temperature": 0.3,
                        "streaming": True
                    },
                    "tools": [
                        {
                            "id": "knowledge_base_1",
                            "type": "simple_rag",
                            "name": "Company Knowledge Base",
                            "description": "Search company documents",
                            "settings": {
                                "datastore_id": "kb1_datastore",
                                "knowledgeBaseIds": ["kb_1"],
                                "search_type": "similarity",
                                "k": 5,
                                # KB-специфичные настройки модели
                                "modelId": "gpt-4",
                                "provider": "openai", 
                                "temperature": 0.1
                            }
                        },
                        {
                            "id": "knowledge_base_2", 
                            "type": "simple_rag",
                            "name": "Technical Documentation",
                            "description": "Search technical docs",
                            "settings": {
                                "datastore_id": "kb2_datastore",
                                "knowledgeBaseIds": ["kb_2"],
                                "search_type": "similarity",
                                "k": 3,
                                # KB-специфичные настройки модели
                                "modelId": "gpt-3.5-turbo",
                                "provider": "openai",
                                "temperature": 0.0
                            }
                        },
                        {
                            "id": "safe_tool_1",
                            "type": "calculator",
                            "name": "Calculator",
                            "description": "Perform calculations"
                        }
                    ]
                }
            }
        }
    }


def test_kb_id_extraction():
    """Тестирует извлечение KB IDs из ToolMessage."""
    print("Testing KB ID extraction from ToolMessage...")
    
    # Создаем mock logger с DEBUG уровнем
    logger = logging.getLogger("test")
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    # Создаем GraphFactory с тестовой конфигурацией
    config = create_test_agent_config()
    factory = GraphFactory(config, "test_agent", logger)
    
    print(f"Test config tools: {config['config']['simple']['settings']['tools']}")
    
    # Создаем тестовое ToolMessage
    tool_message = ToolMessage(
        content="Document 1 content\n---RETRIEVER_DOC---\nDocument 2 content",
        name="knowledge_base_1",
        tool_call_id="test_call_id_1"
    )
    
    # Тестируем извлечение KB IDs
    kb_ids = factory._extract_kb_ids_from_tool_message(tool_message)
    
    print(f"Extracted KB IDs: {kb_ids}")
    assert kb_ids == ["kb_1"], f"Expected ['kb_1'], got {kb_ids}"
    print("✓ KB ID extraction test passed")


def test_kb_specific_model_config():
    """Тестирует получение KB-специфичной конфигурации модели."""
    print("\nTesting KB-specific model configuration...")
    
    # Создаем mock logger
    logger = logging.getLogger("test")
    
    # Создаем GraphFactory с тестовой конфигурацией
    config = create_test_agent_config()
    factory = GraphFactory(config, "test_agent", logger)
    
    # Тестируем получение конфигурации для KB 1
    kb_config = factory._get_knowledge_base_model_config(["kb_1"])
    print(f"KB1 model config: {kb_config}")
    
    expected_kb1_config = {
        "model_id": "gpt-4",
        "provider": "openai",
        "temperature": 0.1
    }
    
    assert kb_config == expected_kb1_config, f"Expected {expected_kb1_config}, got {kb_config}"
    
    # Тестируем получение конфигурации для KB 2
    kb_config = factory._get_knowledge_base_model_config(["kb_2"])
    print(f"KB2 model config: {kb_config}")
    
    expected_kb2_config = {
        "model_id": "gpt-3.5-turbo",
        "provider": "openai",
        "temperature": 0.0
    }
    
    assert kb_config == expected_kb2_config, f"Expected {expected_kb2_config}, got {kb_config}"
    
    # Тестируем случай, когда KB не найдена
    kb_config = factory._get_knowledge_base_model_config(["nonexistent_kb"])
    print(f"Nonexistent KB config: {kb_config}")
    assert kb_config is None, f"Expected None for nonexistent KB, got {kb_config}"
    
    print("✓ KB-specific model configuration test passed")


def test_node_config_with_kb_ids():
    """Тестирует получение конфигурации узла с KB IDs."""
    print("\nTesting node configuration with KB IDs...")
    
    # Создаем mock logger
    logger = logging.getLogger("test")
    
    # Создаем GraphFactory с тестовой конфигурацией
    config = create_test_agent_config()
    factory = GraphFactory(config, "test_agent", logger)
    
    # Тестируем конфигурацию grading узла с KB 1
    grading_config = factory._get_kb_specific_node_config("grading", ["kb_1"])
    print(f"Grading config with KB1: {grading_config}")
    
    # Для grading узла температура должна быть 0.0 (переопределена)
    expected_grading_config = {
        "model_id": "gpt-4",
        "provider": "openai", 
        "temperature": 0.0,  # Специальная температура для grading
        "streaming": False
    }
    
    assert grading_config == expected_grading_config, f"Expected {expected_grading_config}, got {grading_config}"
    
    # Тестируем конфигурацию rewrite узла с KB 2
    rewrite_config = factory._get_kb_specific_node_config("rewrite", ["kb_2"])
    print(f"Rewrite config with KB2: {rewrite_config}")
    
    # Для rewrite узла температура должна быть 0.0 (переопределена)
    expected_rewrite_config = {
        "model_id": "gpt-3.5-turbo",
        "provider": "openai",
        "temperature": 0.0,  # Специальная температура для rewrite
        "streaming": False
    }
    
    assert rewrite_config == expected_rewrite_config, f"Expected {expected_rewrite_config}, got {rewrite_config}"
    
    # Тестируем конфигурацию generate узла с KB 1
    generate_config = factory._get_kb_specific_node_config("generate", ["kb_1"])
    print(f"Generate config with KB1: {generate_config}")
    
    # Для generate узла используется оригинальная температура KB
    expected_generate_config = {
        "model_id": "gpt-4",
        "provider": "openai",
        "temperature": 0.1,  # Оригинальная температура KB
        "streaming": True
    }
    
    assert generate_config == expected_generate_config, f"Expected {expected_generate_config}, got {generate_config}"
    
    # Тестируем fallback к глобальной конфигурации
    fallback_config = factory._get_kb_specific_node_config("generate", ["nonexistent_kb"])
    print(f"Fallback config: {fallback_config}")
    
    expected_fallback_config = {
        "model_id": "gpt-4o-mini",
        "provider": "openai",
        "temperature": 0.3,
        "streaming": True
    }
    
    assert fallback_config == expected_fallback_config, f"Expected {expected_fallback_config}, got {fallback_config}"
    
    print("✓ Node configuration with KB IDs test passed")


def test_factory_integration():
    """Тестирует интеграцию с GraphFactory._get_node_config."""
    print("\nTesting GraphFactory integration...")
    
    # Создаем mock logger
    logger = logging.getLogger("test")
    
    # Создаем GraphFactory с тестовой конфигурацией
    config = create_test_agent_config()
    factory = GraphFactory(config, "test_agent", logger)
    
    # Тестируем _get_node_config для RAG узлов с KB IDs
    grading_config = factory._get_node_config("grading", ["kb_1"])
    print(f"Factory grading config: {grading_config}")
    
    expected_config = {
        "model_id": "gpt-4",
        "provider": "openai",
        "temperature": 0.0,  # Переопределена для grading
        "streaming": False
    }
    
    assert grading_config == expected_config, f"Expected {expected_config}, got {grading_config}"
    
    # Тестируем _get_node_config для agent узла (должен использовать стандартную конфигурацию)
    agent_config = factory._get_node_config("agent")
    print(f"Factory agent config: {agent_config}")
    
    expected_agent_config = {
        "model_id": "gpt-4o-mini",
        "provider": "openai",
        "temperature": 0.3,
        "streaming": True
    }
    
    assert agent_config == expected_agent_config, f"Expected {expected_agent_config}, got {agent_config}"
    
    print("✓ GraphFactory integration test passed")


if __name__ == "__main__":
    print("Starting KB-specific models tests...\n")
    
    try:
        test_kb_id_extraction()
        test_kb_specific_model_config()
        test_node_config_with_kb_ids()
        test_factory_integration()
        
        print("\n🎉 All tests passed! KB-specific models implementation is working correctly.")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
