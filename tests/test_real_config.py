#!/usr/bin/env python3
"""
Test script to reproduce the exact production error with real agent configuration.
"""

import sys
import os
import logging
from functools import partial

# Add the app directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from app.agent_runner.common.tools_registry import make_api_request, ToolsRegistry
from langchain_core.tools import Tool

def setup_logging():
    """Setup detailed logging for debugging."""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)

def test_real_production_config():
    """Test with the exact configuration structure from production."""
    logger = setup_logging()
    logger.info("Testing real production API configuration...")
    
    # Real configuration structure that caused the error in production
    agent_config = {
        "config": {
            "simple": {
                "settings": {
                    "tools": [
                        {
                            "id": "get_bonus_balance",
                            "name": "Получить ББ",
                            "type": "apiRequest", 
                            "enabled": True,
                            "description": "Получение баланса бонусных баллов пользователя",
                            "settings": {
                                "apiUrl": "https://httpbin.org/get",  # Use test URL
                                "method": "GET",
                                "headers": [
                                    {"key": "Authorization", "value": "Bearer test-token"},
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "User-Agent", "value": "PlatformAI-Agent/1.0"}
                                ],
                                "params": [
                                    {
                                        "key": "phone",
                                        "value": "{phone_number}"
                                    }
                                ],
                                "returnToAgent": True,
                                "rewriteQuery": True
                            }
                        }
                    ]
                }
            }
        }
    }
    
    # Test 1: Direct make_api_request call
    logger.info("=== Test 1: Direct make_api_request call ===")
    
    api_config = {
        "id": "get_bonus_balance",
        "name": "Получить ББ",
        "description": "Получение баланса бонусных баллов пользователя",
        "apiUrl": "https://httpbin.org/get",
        "method": "GET",
        "headers": [
            {"key": "Authorization", "value": "Bearer test-token"},
            {"key": "Content-Type", "value": "application/json"},
            {"key": "User-Agent", "value": "PlatformAI-Agent/1.0"}
        ],
        "params": [
            {
                "key": "phone",
                "value": "1234567890"  # No placeholder
            }
        ]
    }
    
    agent_state = {
        'config': agent_config,
        'user_data': {
            'phone_number': '1234567890',
            'user_id': 'test_user',
            'is_authenticated': True
        },
    }
    
    try:
        result = make_api_request(
            api_config=api_config,
            agent_state=agent_state,
            log_adapter=logger
        )
        if "error" in result.lower():
            logger.error(f"❌ Direct API call failed: {result}")
            return False
        else:
            logger.info("✅ Direct API call succeeded!")
    except Exception as e:
        logger.error(f"❌ Direct API call failed with exception: {e}", exc_info=True)
        return False
    
    # Test 2: Tool creation via ToolsRegistry
    logger.info("=== Test 2: Tool creation via ToolsRegistry ===")
    
    try:
        tool = ToolsRegistry.create_api_tool(
            api_config=api_config,
            agent_state=agent_state,
            log_adapter=logger
        )
        logger.info(f"✅ Tool created successfully: {tool.name}")
        
        # Test tool execution
        logger.info("Testing tool execution...")
        result = tool.func()
        if "error" in result.lower():
            logger.error(f"❌ Tool execution failed: {result}")
            return False
        else:
            logger.info("✅ Tool execution succeeded!")
            
    except Exception as e:
        logger.error(f"❌ Tool creation/execution failed: {e}", exc_info=True)
        return False
    
    # Test 3: Using functools.partial like in production
    logger.info("=== Test 3: Using functools.partial ===")
    
    try:
        # This mimics how tools are created in configure_tools_centralized
        api_function = partial(
            make_api_request,
            api_config=api_config,
            agent_state=agent_state,
            log_adapter=logger
        )
        
        # Create Tool with partial function
        tool = Tool(
            name="get_bonus_balance",
            description="Получение баланса бонусных баллов пользователя",
            func=api_function
        )
        
        logger.info(f"✅ Tool with partial created: {tool.name}")
        
        # Test execution
        result = tool.func()
        if "error" in result.lower():
            logger.error(f"❌ Partial tool execution failed: {result}")
            return False
        else:
            logger.info("✅ Partial tool execution succeeded!")
            
    except Exception as e:
        logger.error(f"❌ Partial tool test failed: {e}", exc_info=True)
        return False
    
    logger.info("🎉 All production configuration tests passed!")
    return True

if __name__ == "__main__":
    success = test_real_production_config()
    if success:
        print("\n✅ All tests passed - the fix should work in production!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed - need further investigation")
        sys.exit(1)
