qdrant:
	uv run qdrant-worker.py
bot:
	uv run last_bot/bot.py
agent:
	uv run last_bot/agent.py
docker:
	docker compose -f docker-PAI/docker-compose.deps.yml --env-file docker-PAI/docker.env up -d