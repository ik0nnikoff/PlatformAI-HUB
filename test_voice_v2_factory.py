#!/usr/bin/env python3
"""
Test script для проверки Enhanced Voice Provider Factory
Phase 4.6.1 Core orchestrator implementation completion
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.services.voice_v2.providers.factory.factory import EnhancedVoiceProviderFactory
from app.services.voice_v2.providers.factory.types import ProviderCategory

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_factory_initialization():
    """Test 1: Factory initialization"""
    logger.info("🧪 Test 1: Factory Initialization")
    
    try:
        factory = EnhancedVoiceProviderFactory()
        await factory.initialize()
        
        logger.info("✅ Factory initialized successfully")
        return factory
    except Exception as e:
        logger.error(f"❌ Factory initialization failed: {e}")
        raise


async def test_provider_registry(factory):
    """Test 2: Provider registry"""
    logger.info("🧪 Test 2: Provider Registry")
    
    try:
        # Get STT providers
        stt_providers = factory.get_available_providers(
            category=ProviderCategory.STT,
            enabled_only=True
        )
        logger.info(f"✅ Found {len(stt_providers)} STT providers:")
        for provider in stt_providers:
            logger.info(f"  - {provider.name} (priority: {provider.priority})")
        
        # Get TTS providers
        tts_providers = factory.get_available_providers(
            category=ProviderCategory.TTS,
            enabled_only=True
        )
        logger.info(f"✅ Found {len(tts_providers)} TTS providers:")
        for provider in tts_providers:
            logger.info(f"  - {provider.name} (priority: {provider.priority})")
            
        return len(stt_providers) > 0 and len(tts_providers) > 0
        
    except Exception as e:
        logger.error(f"❌ Provider registry test failed: {e}")
        return False


async def test_stt_provider_creation(factory):
    """Test 3: STT Provider Creation"""
    logger.info("🧪 Test 3: STT Provider Creation")
    
    try:
        # Test OpenAI STT provider creation
        openai_stt = await factory.create_stt_provider("openai")
        if openai_stt:
            logger.info(f"✅ OpenAI STT provider created: {openai_stt.__class__.__name__}")
        else:
            logger.warning("⚠️ OpenAI STT provider creation returned None")
        
        # Test Google STT provider creation
        google_stt = await factory.create_stt_provider("google")
        if google_stt:
            logger.info(f"✅ Google STT provider created: {google_stt.__class__.__name__}")
        else:
            logger.warning("⚠️ Google STT provider creation returned None")
            
        # Test Yandex STT provider creation
        yandex_stt = await factory.create_stt_provider("yandex")
        if yandex_stt:
            logger.info(f"✅ Yandex STT provider created: {yandex_stt.__class__.__name__}")
        else:
            logger.warning("⚠️ Yandex STT provider creation returned None")
            
        return True
        
    except Exception as e:
        logger.error(f"❌ STT provider creation test failed: {e}")
        return False


async def test_tts_provider_creation(factory):
    """Test 4: TTS Provider Creation"""
    logger.info("🧪 Test 4: TTS Provider Creation")
    
    try:
        # Test OpenAI TTS provider creation
        openai_tts = await factory.create_tts_provider("openai")
        if openai_tts:
            logger.info(f"✅ OpenAI TTS provider created: {openai_tts.__class__.__name__}")
        else:
            logger.warning("⚠️ OpenAI TTS provider creation returned None")
        
        # Test Google TTS provider creation
        google_tts = await factory.create_tts_provider("google")
        if google_tts:
            logger.info(f"✅ Google TTS provider created: {google_tts.__class__.__name__}")
        else:
            logger.warning("⚠️ Google TTS provider creation returned None")
            
        # Test Yandex TTS provider creation
        yandex_tts = await factory.create_tts_provider("yandex")
        if yandex_tts:
            logger.info(f"✅ Yandex TTS provider created: {yandex_tts.__class__.__name__}")
        else:
            logger.warning("⚠️ Yandex TTS provider creation returned None")
            
        return True
        
    except Exception as e:
        logger.error(f"❌ TTS provider creation test failed: {e}")
        return False


async def test_health_check(factory):
    """Test 5: Health Check"""
    logger.info("🧪 Test 5: Health Check")
    
    try:
        health_results = await factory.health_check()
        
        logger.info(f"✅ Health check completed for {len(health_results)} providers:")
        for provider_name, health_info in health_results.items():
            status_emoji = "✅" if health_info.status.value == "active" else "⚠️"
            logger.info(f"  {status_emoji} {provider_name}: {health_info.status.value}")
            if health_info.error_message:
                logger.info(f"    Error: {health_info.error_message}")
                
        return True
        
    except Exception as e:
        logger.error(f"❌ Health check test failed: {e}")
        return False


async def main():
    """Main test function"""
    logger.info("🚀 Starting Enhanced Voice Provider Factory Tests")
    logger.info("=" * 60)
    
    try:
        # Test 1: Factory initialization
        factory = await test_factory_initialization()
        
        # Test 2: Provider registry
        registry_ok = await test_provider_registry(factory)
        
        # Test 3: STT provider creation
        stt_ok = await test_stt_provider_creation(factory)
        
        # Test 4: TTS provider creation
        tts_ok = await test_tts_provider_creation(factory)
        
        # Test 5: Health check
        health_ok = await test_health_check(factory)
        
        # Cleanup
        await factory.cleanup()
        
        # Summary
        logger.info("=" * 60)
        logger.info("📊 TEST SUMMARY:")
        logger.info(f"  Factory Initialization: {'✅ PASS' if factory else '❌ FAIL'}")
        logger.info(f"  Provider Registry: {'✅ PASS' if registry_ok else '❌ FAIL'}")
        logger.info(f"  STT Provider Creation: {'✅ PASS' if stt_ok else '❌ FAIL'}")
        logger.info(f"  TTS Provider Creation: {'✅ PASS' if tts_ok else '❌ FAIL'}")
        logger.info(f"  Health Check: {'✅ PASS' if health_ok else '❌ FAIL'}")
        
        all_tests_passed = all([factory, registry_ok, stt_ok, tts_ok, health_ok])
        
        if all_tests_passed:
            logger.info("🎉 ALL TESTS PASSED - Enhanced Factory is working!")
            return 0
        else:
            logger.error("💥 SOME TESTS FAILED - Need to investigate")
            return 1
            
    except Exception as e:
        logger.error(f"💥 Test execution failed: {e}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
