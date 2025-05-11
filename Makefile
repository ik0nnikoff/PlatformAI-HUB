run:
	uv run uvicorn hub.agent_manager.main:app --reload --port 8001 --host 0.0.0.0

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
	uv run hub.agent_runner.runner \
		--agent-id $(AGENT_ID) \
		--config-url http://localhost:8001/agents/$(AGENT_ID)/config \
		--redis-url ${REDIS_URL:-redis://localhost:6379}

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
	echo "Attempting to stop agent $(AGENT_ID) via API (Force=$(FORCE))..."; \
	curl -X POST "http://localhost:8000/agents/$(AGENT_ID)/stop$${FORCE_PARAM}" -H "accept: application/json" || echo "Failed to send stop request to manager API."

# Stop all agent runners by querying the manager API and stopping each one
# Requires curl and jq
stop_all_runners:
	@echo "Stopping all running agents via manager API..."
	@AGENT_IDS=$$(curl -s -X GET "http://localhost:8000/agents" -H "accept: application/json" | jq -r '.[] | select(.status=="running" or .status=="starting" or .status=="initializing") | .agent_id'); \
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
# Usage: make run_telegram_bot AGENT_ID=my_agent_abc
run_telegram_bot:
ifndef AGENT_ID
	$(error AGENT_ID is not set. Usage: make run_telegram_bot AGENT_ID=<your_agent_id>)
endif
	uv run hub/integrations/telegram_bot.py --agent-id $(AGENT_ID)