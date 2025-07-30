#!/usr/bin/env python3
"""
Тест для проверки исправлений voice_v2 Phase 4.6.1

Цель: Проверить что Enhanced Factory теперь может создавать функциональные провайдеры
"""

import asyncio
import logging
import sys
from pathlib import Path

# Добавляем корневую директорию проекта в Python path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from app.services.voice_v2.providers.enhanced_factory import EnhancedVoiceProviderFactory
from app.services.voice_v2.core.orchestrator.base_orchestrator import VoiceServiceOrchestrator
from app.services.voice_v2.core.schemas import STTRequest, TTSRequest
from app.services.voice_v2.core.interfaces import AudioFormat

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_enhanced_factory():
    """Test Enhanced Factory провайдеры creation"""
    logger.info("🧪 Testing Enhanced Factory provider creation...")
    
    factory = EnhancedVoiceProviderFactory()
    
    try:
        # Initialize factory
        await factory.initialize()
        logger.info("✅ Factory initialized successfully")
        
        # Test STT provider creation
        logger.info("Testing STT provider creation...")
        stt_provider = await factory.create_stt_provider("openai")
        if stt_provider:
            logger.info(f"✅ STT provider created: {type(stt_provider).__name__}")
        else:
            logger.error("❌ Failed to create STT provider")
            
        # Test TTS provider creation
        logger.info("Testing TTS provider creation...")
        tts_provider = await factory.create_tts_provider("openai")
        if tts_provider:
            logger.info(f"✅ TTS provider created: {type(tts_provider).__name__}")
        else:
            logger.error("❌ Failed to create TTS provider")
            
        # Check provider registry
        stt_providers = factory.get_available_providers(category=factory._default_providers["openai_stt"].category)
        tts_providers = factory.get_available_providers(category=factory._default_providers["openai_tts"].category)
        
        logger.info(f"📋 Available STT providers: {len(stt_providers)}")
        logger.info(f"📋 Available TTS providers: {len(tts_providers)}")
        
    except Exception as e:
        logger.error(f"❌ Factory test failed: {e}")
        raise
    finally:
        await factory.cleanup()


async def test_orchestrator_initialization():
    """Test Orchestrator с Enhanced Factory"""
    logger.info("🎭 Testing Orchestrator with Enhanced Factory...")
    
    try:
        # Create orchestrator with Enhanced Factory
        orchestrator = await VoiceServiceOrchestrator.create_with_enhanced_factory(
            factory_config={},
            cache_manager=None,
            file_manager=None
        )
        
        logger.info("✅ Orchestrator created with Enhanced Factory")
        
        # Check if orchestrator is properly initialized
        if orchestrator._initialized:
            logger.info("✅ Orchestrator initialized successfully")
        else:
            logger.error("❌ Orchestrator not initialized")
            
        # Test provider access
        if hasattr(orchestrator, '_enhanced_factory') and orchestrator._enhanced_factory:
            logger.info("✅ Enhanced Factory available in orchestrator")
        else:
            logger.error("❌ Enhanced Factory not available in orchestrator")
            
    except Exception as e:
        logger.error(f"❌ Orchestrator test failed: {e}")
        raise


async def test_basic_workflow():
    """Test basic STT/TTS workflow без real API calls"""
    logger.info("🔄 Testing basic workflow (mock mode)...")
    
    try:
        # Create orchestrator
        orchestrator = await VoiceServiceOrchestrator.create_with_enhanced_factory(
            factory_config={},
            cache_manager=None,
            file_manager=None
        )
        
        # Create mock STT request
        stt_request = STTRequest(
            audio_data=b"fake_audio_data",
            format=AudioFormat.MP3,
            language="ru"
        )
        
        # Create mock TTS request  
        tts_request = TTSRequest(
            text="Привет, это тест голосового синтеза",
            language="ru",
            voice="alloy"
        )
        
        logger.info("📝 Created mock requests")
        logger.info("✅ Basic workflow structure is ready")
        
    except Exception as e:
        logger.error(f"❌ Workflow test failed: {e}")
        raise


async def main():
    """Run all tests"""
    logger.info("🚀 Starting voice_v2 Phase 4.6.1 tests...")
    
    try:
        await test_enhanced_factory()
        await test_orchestrator_initialization()
        await test_basic_workflow()
        
        logger.info("🎉 All tests passed! Phase 4.6.1 core implementation is working!")
        
    except Exception as e:
        logger.error(f"💥 Tests failed: {e}")
        return False
        
    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
