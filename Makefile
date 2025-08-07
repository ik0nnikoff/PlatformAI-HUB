MANAGER_PORT ?= 8001

run:
	uv run uvicorn app.main:app --reload --port $(MANAGER_PORT) --host 0.0.0.0

# Команда для применения миграций Alembic
migrate:
	alembic upgrade head

# Команда для создания новой ревизии Alembic (автогенерация)
makemigrations:
	alembic revision --autogenerate -m "$(m)" # Пример: make makemigrations m="Add new field"

# Команда для отката последней миграции
downgrade:
	alembic downgrade -1

# Команда для просмотра истории миграций
history:
	alembic history --verbose

# Команда для просмотра текущей ревизии
current:
	alembic current

# Example of running a specific agent runner manually (for testing)
# Usage: make run_agent AGENT_ID=my_agent_abc
run_agent:
ifndef AGENT_ID
	$(error AGENT_ID is not set. Usage: make run_agent AGENT_ID=<your_agent_id>)
endif
	uv run app.agent_runner.runner_main \
		--agent-id $(AGENT_ID) \
		--manager-url http://localhost:$(MANAGER_PORT) \
		--redis-url ${REDIS_URL:-redis://localhost:6379}
		# Add --database-url if runner_main ever needs direct DB access for init
		# --database-url ${DATABASE_URL:-postgresql+asyncpg://admin:password@localhost:5432/platformAI}

# Stop a specific agent runner via the manager API
# Usage: make stop_agent AGENT_ID=my_agent_abc [FORCE=true]
stop_agent:
ifndef AGENT_ID
	$(error AGENT_ID is not set. Usage: make stop_agent AGENT_ID=<your_agent_id>)
endif
	@FORCE_PARAM=""; \
	if [ "$(FORCE)" = "true" ]; then \
		FORCE_PARAM="?force=true"; \
	fi; \
	echo "Attempting to stop agent $(AGENT_ID) via API (Port: $(MANAGER_PORT), Force=$(FORCE))..."; \
	curl -X POST "http://localhost:$(MANAGER_PORT)/agents/$(AGENT_ID)/stop$${FORCE_PARAM}" -H "accept: application/json" || echo "Failed to send stop request to manager API."

# Stop all agent runners by querying the manager API and stopping each one
# Requires curl and jq
stop_all_runners:
	@echo "Stopping all running agents via manager API (Port: $(MANAGER_PORT))..."
	@AGENT_IDS=$$(curl -s -X GET "http://localhost:$(MANAGER_PORT)/agents" -H "accept: application/json" | jq -r '.[] | select(.status=="running" or .status=="starting" or .status=="initializing") | .agent_id'); \
	if [ -z "$$AGENT_IDS" ]; then \
		echo "No running agents found according to manager API."; \
	else \
		echo "Found running agents: $$AGENT_IDS"; \
		for id in $$AGENT_IDS; do \
			echo "Stopping agent $$id..."; \
			$(MAKE) stop_agent AGENT_ID=$$id; \
		done \
	fi
	# Old pkill method (commented out):
	# pkill -f "agent_runner/runner.py" || true

# Target to run the Telegram bot integration
# Usage: make run_telegram_bot AGENT_ID=my_agent_abc BOT_TOKEN=your_telegram_bot_token
run_telegram_bot:
ifndef AGENT_ID
	$(error AGENT_ID is not set. Usage: make run_telegram_bot AGENT_ID=<your_agent_id> BOT_TOKEN=<your_token>)
endif
ifndef BOT_TOKEN
	$(error BOT_TOKEN is not set. Usage: make run_telegram_bot AGENT_ID=<your_agent_id> BOT_TOKEN=<your_token>)
endif
	uv run app.integrations.telegram.telegram_bot_main \
		--agent-id $(AGENT_ID) \
		--redis-url ${REDIS_URL:-redis://localhost:6379} \
		--integration-settings \'{"botToken": "$(BOT_TOKEN)"}\'

clean:
	find /Users/jb/Projects/PlatformAI/PlatformAI-HUB -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true