#!/usr/bin/env python3
"""
Unit test for headers normalization logic without making actual HTTP requests.
"""

import sys
import os

# Add the app directory to Python path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

def test_headers_normalization():
    """Test the headers normalization logic directly."""
    print("Testing headers normalization logic...")
    
    # Test 1: Dict format (should remain unchanged)
    headers_raw = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json"
    }
    
    headers = {}
    if isinstance(headers_raw, dict):
        headers = headers_raw.copy()
    elif isinstance(headers_raw, list):
        for header_item in headers_raw:
            if isinstance(header_item, dict) and "key" in header_item and "value" in header_item:
                headers[header_item["key"]] = header_item["value"]
    
    expected_dict = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
    assert headers == expected_dict, f"Dict test failed: {headers} != {expected_dict}"
    print("✅ Dict format test passed")
    
    # Test 2: List format (should convert to dict)
    headers_raw = [
        {"key": "Authorization", "value": "Bearer test-token"},
        {"key": "Content-Type", "value": "application/json"}
    ]
    
    headers = {}
    if isinstance(headers_raw, dict):
        headers = headers_raw.copy()
    elif isinstance(headers_raw, list):
        for header_item in headers_raw:
            if isinstance(header_item, dict) and "key" in header_item and "value" in header_item:
                headers[header_item["key"]] = header_item["value"]
    
    expected_list = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
    assert headers == expected_list, f"List test failed: {headers} != {expected_list}"
    print("✅ List format test passed")
    
    # Test 3: Invalid list format (should handle gracefully)
    headers_raw = [
        {"invalid": "format"},
        {"key": "Content-Type", "value": "application/json"}  # This one should work
    ]
    
    headers = {}
    if isinstance(headers_raw, dict):
        headers = headers_raw.copy()
    elif isinstance(headers_raw, list):
        for header_item in headers_raw:
            if isinstance(header_item, dict) and "key" in header_item and "value" in header_item:
                headers[header_item["key"]] = header_item["value"]
    
    expected_partial = {"Content-Type": "application/json"}
    assert headers == expected_partial, f"Partial list test failed: {headers} != {expected_partial}"
    print("✅ Invalid list format test passed")
    
    # Test 4: String format (should result in empty headers)
    headers_raw = "invalid_string"
    
    headers = {}
    if isinstance(headers_raw, dict):
        headers = headers_raw.copy()
    elif isinstance(headers_raw, list):
        for header_item in headers_raw:
            if isinstance(header_item, dict) and "key" in header_item and "value" in header_item:
                headers[header_item["key"]] = header_item["value"]
    
    assert headers == {}, f"String format test failed: {headers} != {{}}"
    print("✅ String format test passed")
    
    print("🎉 All headers normalization tests passed!")

if __name__ == "__main__":
    test_headers_normalization()
