#!/usr/bin/env python3
"""
Test script to verify API headers normalization works correctly.
Tests both dict and list formats for headers.
"""

import sys
import os

# Add the app directory to Python path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from app.agent_runner.common.tools_registry import make_api_request
import logging

def setup_logging():
    """Setup basic logging for the test."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)

def test_headers_dict_format(logger):
    """Test headers in dictionary format."""
    logger.info("Testing headers in dictionary format...")
    
    api_config = {
        "apiUrl": "https://httpbin.org/get",
        "method": "GET",
        "headers": {
            "Authorization": "Bearer test-token",
            "Content-Type": "application/json",
            "User-Agent": "PlatformAI-Agent/1.0"
        },
        "params": [],
        "name": "test_dict_headers"
    }
    
    try:
        result = make_api_request(api_config=api_config, log_adapter=logger)
        if "error" in result.lower():
            logger.error(f"❌ Dict headers test failed: {result}")
            return False
        else:
            logger.info("✅ Dict headers test passed!")
            return True
    except Exception as e:
        logger.error(f"❌ Dict headers test failed with exception: {e}")
        return False

def test_headers_list_format(logger):
    """Test headers in list format."""
    logger.info("Testing headers in list format...")
    
    api_config = {
        "apiUrl": "https://httpbin.org/get",
        "method": "GET",
        "headers": [
            {"key": "Authorization", "value": "Bearer test-token"},
            {"key": "Content-Type", "value": "application/json"},
            {"key": "User-Agent", "value": "PlatformAI-Agent/1.0"}
        ],
        "params": [],
        "name": "test_list_headers"
    }
    
    try:
        result = make_api_request(api_config=api_config, log_adapter=logger)
        if "error" in result.lower():
            logger.error(f"❌ List headers test failed: {result}")
            return False
        else:
            logger.info("✅ List headers test passed!")
            return True
    except Exception as e:
        logger.error(f"❌ List headers test failed with exception: {e}")
        return False

def test_headers_invalid_format(logger):
    """Test headers in invalid format (should handle gracefully)."""
    logger.info("Testing headers in invalid format...")
    
    api_config = {
        "apiUrl": "https://httpbin.org/get",
        "method": "GET",
        "headers": "invalid_string",  # Invalid format
        "params": [],
        "name": "test_invalid_headers"
    }
    
    try:
        result = make_api_request(api_config=api_config, log_adapter=logger)
        # Should still work, just without headers
        if "error" in result.lower():
            logger.warning(f"⚠️ Invalid headers test returned error (expected): {result}")
        else:
            logger.info("✅ Invalid headers test handled gracefully!")
        return True
    except Exception as e:
        logger.error(f"❌ Invalid headers test failed with exception: {e}")
        return False

def main():
    """Main test function."""
    logger = setup_logging()
    logger.info("Starting API headers normalization tests...")
    
    tests_passed = 0
    total_tests = 3
    
    # Test 1: Dict format headers
    if test_headers_dict_format(logger):
        tests_passed += 1
    
    # Test 2: List format headers
    if test_headers_list_format(logger):
        tests_passed += 1
    
    # Test 3: Invalid format headers
    if test_headers_invalid_format(logger):
        tests_passed += 1
    
    logger.info(f"Tests completed: {tests_passed}/{total_tests} passed")
    
    if tests_passed == total_tests:
        logger.info("🎉 All headers normalization tests passed!")
        return 0
    else:
        logger.error("❌ Some tests failed!")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
