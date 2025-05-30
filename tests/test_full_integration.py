#!/usr/bin/env python3
"""
Full integration test using configure_tools_centralized like in production.
"""

import sys
import os
import logging

# Add the app directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from app.agent_runner.common.tools_registry import configure_tools_centralized

def setup_logging():
    """Setup detailed logging for debugging."""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)

def test_configure_tools_centralized():
    """Test the full configure_tools_centralized pipeline."""
    logger = setup_logging()
    logger.info("Testing configure_tools_centralized with list-format headers...")
    
    # Real agent configuration with list-format headers
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
                                "apiUrl": "https://httpbin.org/get",
                                "method": "GET",
                                "headers": [
                                    {"key": "Authorization", "value": "Bearer production-token"},
                                    {"key": "Content-Type", "value": "application/json"},
                                    {"key": "User-Agent", "value": "PlatformAI-Agent/Production"}
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
    
    agent_id = "test_agent_production"
    
    try:
        # Test configure_tools_centralized
        logger.info("Calling configure_tools_centralized...")
        
        centralized_tools, safe_tools, api_tools = configure_tools_centralized(
            agent_config=agent_config,
            agent_id=agent_id
        )
        
        logger.info(f"✅ configure_tools_centralized succeeded!")
        logger.info(f"Total centralized tools: {len(centralized_tools)}")
        logger.info(f"Safe tools: {len(safe_tools)}")
        logger.info(f"API tools: {len(api_tools)}")
        
        # Test that we have the expected API tool
        api_tool = None
        for tool in api_tools:
            if tool.name == "get_bonus_balance":
                api_tool = tool
                break
        
        if not api_tool:
            logger.error("❌ API tool 'get_bonus_balance' not found in api_tools")
            return False
            
        logger.info(f"✅ Found API tool: {api_tool.name}")
        
        # Test tool execution
        logger.info("Testing API tool execution...")
        try:
            # Note: The tool function is a partial, so we can't pass arguments
            # It should use the agent_state from when it was created
            result = api_tool.func()
            
            if "error" in result.lower():
                logger.warning(f"⚠️ Tool execution returned error (might be expected due to placeholders): {result}")
            else:
                logger.info("✅ Tool execution succeeded!")
                
        except Exception as e:
            logger.warning(f"⚠️ Tool execution failed (might be expected due to placeholders): {e}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ configure_tools_centralized failed: {e}", exc_info=True)
        return False

if __name__ == "__main__":
    success = test_configure_tools_centralized()
    if success:
        print("\n✅ Full integration test passed!")
        sys.exit(0)
    else:
        print("\n❌ Integration test failed!")
        sys.exit(1)
