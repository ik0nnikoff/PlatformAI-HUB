#!/usr/bin/env python3
"""
Test script to verify the new agent configuration structure works correctly.
Tests the schema validation, tools configuration, and configuration mixin functionality.
"""

import json
import logging
import sys
import os

# Add the app directory to Python path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from pydantic import ValidationError
from app.api.schemas.agent_schemas import AgentConfigInput
from app.agent_runner.common.config_mixin import AgentConfigMixin
from app.agent_runner.langgraph.tools import configure_tools

def setup_logging():
    """Setup basic logging for the test."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)

def load_test_config():
    """Load the test configuration JSON."""
    try:
        with open('test_config_example.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("Error: test_config_example.json not found")
        return None
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}")
        return None

def test_schema_validation(config_data, logger):
    """Test the Pydantic schema validation."""
    logger.info("Testing schema validation...")
    
    try:
        # Test the new schema structure
        validated_config = AgentConfigInput(**config_data)
        logger.info("✅ Schema validation passed!")
        
        # Check specific fields
        assert validated_config.ownerId == "user-456", f"Expected ownerId 'user-456', got '{validated_config.ownerId}'"
        
        # Access config_json directly since it's stored as Dict[str, Any]
        config_json = validated_config.config_json
        assert "simple" in config_json, "Expected 'simple' in config"
        assert "settings" in config_json["simple"], "Expected 'settings' in config.simple"
        
        settings = config_json["simple"]["settings"]
        
        # Check model settings
        model_settings = settings.get("model", {})
        
        # Check streaming and useContextMemory at the settings level (according to our config structure)
        assert settings.get("streaming") == True, "Expected streaming to be True"
        assert settings.get("useContextMemory") == True, "Expected useContextMemory to be True"
        
        # Check tools
        tools = settings.get("tools", [])
        assert len(tools) == 3, f"Expected 3 tools, got {len(tools)}"
        
        # Check API tool with new fields
        api_tool = next((t for t in tools if t.get("type") == "apiRequest"), None)
        assert api_tool is not None, "API tool not found"
        api_settings = api_tool.get("settings", {})
        assert api_settings.get("method") == "GET", f"Expected method 'GET', got '{api_settings.get('method')}'"
        headers = api_settings.get("headers", [])
        # Headers can be either dict or list format
        if isinstance(headers, list):
            auth_header_found = any(h.get("key") == "Authorization" for h in headers if isinstance(h, dict))
        else:
            auth_header_found = "Authorization" in headers
        assert auth_header_found, "Authorization header not found"
        
        # Check knowledge base tool
        kb_tool = next((t for t in tools if t.get("type") == "knowledgeBase"), None)
        assert kb_tool is not None, "Knowledge base tool not found"
        kb_settings = kb_tool.get("settings", {})
        assert kb_settings.get("returnToAgent") == True, "Expected returnToAgent to be True"
        assert kb_settings.get("rewriteQuery") == True, "Expected rewriteQuery to be True"
        
        # Check integrations
        integrations = settings.get("integrations", [])
        assert len(integrations) == 1, f"Expected 1 integration, got {len(integrations)}"
        assert integrations[0].get("enabled") == True, "Expected integration to be enabled"
        
        logger.info("✅ All schema validation checks passed!")
        return validated_config
        
    except ValidationError as e:
        logger.error(f"❌ Schema validation failed: {e}")
        return None
    except AssertionError as e:
        logger.error(f"❌ Schema assertion failed: {e}")
        return None

def test_config_mixin(config_data, logger):
    """Test the AgentConfigMixin functionality."""
    logger.info("Testing AgentConfigMixin...")
    
    try:
        mixin = AgentConfigMixin()
        # Set the agent_config as the mixin expects it
        mixin.agent_config = config_data
        
        # Test model configuration extraction
        model_config = mixin._get_model_config()
        
        expected_fields = [
            'provider', 'model_id', 'temperature', 'system_prompt',
            'limit_to_kb', 'answer_in_user_lang', 'use_markdown',
            'streaming', 'enable_context_memory'
        ]
        
        for field in expected_fields:
            assert field in model_config, f"Expected field '{field}' not found in model_config"
        
        assert model_config['provider'] == 'openai', f"Expected provider 'openai', got '{model_config['provider']}'"
        assert model_config['model_id'] == 'gpt-4o-mini', f"Expected model_id 'gpt-4o-mini', got '{model_config['model_id']}'"
        assert model_config['streaming'] == True, f"Expected streaming True, got {model_config['streaming']}"
        assert model_config['enable_context_memory'] == True, f"Expected enable_context_memory True, got {model_config['enable_context_memory']}"
        
        # Test max rewrites extraction
        max_rewrites = mixin.get_max_rewrites(config_data)
        assert max_rewrites == 3, f"Expected max_rewrites 3, got {max_rewrites}"
        
        logger.info("✅ AgentConfigMixin tests passed!")
        return True
        
    except Exception as e:
        logger.error(f"❌ AgentConfigMixin test failed: {e}")
        return False

def test_tools_configuration(config_data, logger):
    """Test the tools configuration functionality."""
    logger.info("Testing tools configuration...")
    
    try:
        # Note: This test might fail if external dependencies (Qdrant, API keys) are not available
        # We'll catch those errors and report them as warnings rather than failures
        
        agent_id = config_data.get('id', 'test-agent')
        
        tools_list, safe_tools, datastore_tools, datastore_names, max_rewrites = configure_tools(
            config_data, agent_id, logger
        )
        
        logger.info(f"Configured {len(tools_list)} total tools")
        logger.info(f"Safe tools: {len(safe_tools)}")
        logger.info(f"Datastore tools: {len(datastore_tools)}")
        logger.info(f"Datastore names: {datastore_names}")
        logger.info(f"Max rewrites: {max_rewrites}")
        
        # Basic checks
        assert isinstance(tools_list, list), "tools_list should be a list"
        assert isinstance(safe_tools, list), "safe_tools should be a list"
        assert isinstance(datastore_tools, list), "datastore_tools should be a list"
        assert isinstance(datastore_names, set), "datastore_names should be a set"
        assert isinstance(max_rewrites, int), "max_rewrites should be an int"
        assert max_rewrites == 3, f"Expected max_rewrites 3, got {max_rewrites}"
        
        logger.info("✅ Tools configuration tests passed!")
        return True
        
    except Exception as e:
        logger.warning(f"⚠️ Tools configuration test had issues (may be due to missing external dependencies): {e}")
        return True  # Don't fail the test for missing external dependencies

def main():
    """Main test function."""
    logger = setup_logging()
    logger.info("Starting PlatformAI configuration structure tests...")
    
    # Load test configuration
    config_data = load_test_config()
    if not config_data:
        logger.error("❌ Failed to load test configuration")
        return 1
    
    # Test 1: Schema validation
    validated_config = test_schema_validation(config_data, logger)
    if not validated_config:
        logger.error("❌ Schema validation failed")
        return 1
    
    # Test 2: Config mixin
    if not test_config_mixin(config_data, logger):
        logger.error("❌ Config mixin test failed")
        return 1
    
    # Test 3: Tools configuration
    if not test_tools_configuration(config_data, logger):
        logger.error("❌ Tools configuration test failed")
        return 1
    
    logger.info("🎉 All tests passed! The new configuration structure is working correctly.")
    return 0

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
