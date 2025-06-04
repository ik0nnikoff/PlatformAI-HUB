#!/bin/bash
set -e

echo "Starting PlatformAI-DOC application..."

# Функция для ожидания доступности базы данных
wait_for_database() {
    echo "Waiting for database to be ready..."
    local max_attempts=30
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        echo "Attempt $attempt of $max_attempts..."
        
        # Проверяем подключение к базе данных
        if uv run python -c "
import asyncio
import asyncpg
import os
import sys
from urllib.parse import urlparse

async def check_db():
    try:
        db_url = os.getenv('DATABASE_URL', 'postgresql+asyncpg://admin:password@localhost:5432/PlatformAI-DOC')
        # Парсим URL и убираем +asyncpg
        parsed = urlparse(db_url.replace('+asyncpg', ''))
        
        conn = await asyncpg.connect(
            host=parsed.hostname,
            port=parsed.port,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path[1:]  # убираем первый слеш
        )
        await conn.close()
        return True
    except Exception as e:
        print(f'Database connection failed: {e}')
        return False

if asyncio.run(check_db()):
    sys.exit(0)
else:
    sys.exit(1)
        "; then
            echo "Database is ready!"
            break
        fi

        if [ $attempt -eq $max_attempts ]; then
            echo "ERROR: Database is not available after $max_attempts attempts"
            exit 1
        fi
        
        attempt=$((attempt + 1))
        sleep 2
    done
}

# Функция для проверки и применения миграций
apply_migrations() {
    echo "Checking database migrations..."
    
    # Проверяем, инициализирован ли Alembic
    if ! uv run alembic current 2>/dev/null; then
        echo "Initializing Alembic..."
        # Если не инициализирован, то инициализируем
        uv run alembic stamp head 2>/dev/null || true
    fi
    
    # Проверяем, есть ли незапущенные миграции
    echo "Applying database migrations..."
    uv run alembic upgrade head
    
    if [ $? -eq 0 ]; then
        echo "Migrations applied successfully!"
    else
        echo "ERROR: Failed to apply migrations"
        exit 1
    fi
}

# Основная логика запуска
main() {
    # Ждем готовности базы данных
    wait_for_database
    
    # Применяем миграции
    apply_migrations
    
    # Запускаем приложение
    echo "Starting application server..."
    exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8001
}

# Запускаем главную функцию
main "$@"
