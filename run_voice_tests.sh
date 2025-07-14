#!/bin/bash

# Скрипт для быстрого запуска тестирования голосового workflow
# Quick Voice Workflow Testing Launcher

echo "🎙️ Voice Workflow Testing Launcher"
echo "=================================="

# Проверяем Python окружение
echo "🐍 Checking Python environment..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 not found. Please install Python 3.8+"
    exit 1
fi

# Проверяем зависимости
echo "📦 Checking dependencies..."
python3 -c "import aiohttp, asyncio" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚠️  Missing dependencies. Installing..."
    pip3 install aiohttp asyncio
fi

# Проверяем, запущен ли сервер
echo "🌐 Checking if server is running..."
SERVER_URL="http://localhost:8001"
curl -s "$SERVER_URL/health" > /dev/null
if [ $? -ne 0 ]; then
    echo "❌ Server is not running at $SERVER_URL"
    echo "   Please start the server first with: make run"
    exit 1
fi

echo "✅ Server is running"

# Меню выбора типа тестов
echo ""
echo "🎯 Select testing mode:"
echo "1) Quick Test (workflow only) - ~30 seconds"
echo "2) Standard Test (workflow + integration) - ~2 minutes"
echo "3) Full Test Suite (all tests) - ~5 minutes"
echo "4) Performance Test Only - ~3 minutes"
echo "5) Custom Selection"

read -p "Enter your choice (1-5): " choice

case $choice in
    1)
        echo "🚀 Running Quick Test..."
        python3 test_voice_master.py --tests workflow
        ;;
    2)
        echo "🚀 Running Standard Test..."
        python3 test_voice_master.py --tests workflow integration
        ;;
    3)
        echo "🚀 Running Full Test Suite..."
        python3 test_voice_master.py --tests all
        ;;
    4)
        echo "🚀 Running Performance Test..."
        python3 test_voice_master.py --tests performance
        ;;
    5)
        echo "Available test types: workflow, integration, performance"
        read -p "Enter test types (space-separated): " custom_tests
        echo "🚀 Running Custom Tests: $custom_tests"
        python3 test_voice_master.py --tests $custom_tests
        ;;
    *)
        echo "❌ Invalid choice. Running Quick Test by default."
        python3 test_voice_master.py --tests workflow
        ;;
esac

exit_code=$?

echo ""
if [ $exit_code -eq 0 ]; then
    echo "✅ Testing completed successfully!"
    echo "📁 Check the generated JSON files for detailed results."
else
    echo "❌ Testing failed. Please check the logs for details."
fi

echo ""
echo "📋 Generated files:"
ls -la voice_*test*results*.json 2>/dev/null || echo "   No result files found"

echo ""
echo "📖 To analyze results:"
echo "   - Check voice_testing_master_results_*.json for complete report"
echo "   - Check individual test result files for details"
echo "   - Review voice_testing_master.log for execution logs"

exit $exit_code
