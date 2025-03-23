qdrant:
	uv run qdrant-worker.py
bot:
	uv run bot.py
agent:
	uv run agent.py
docker:
	docker compose -f docker-PAI/docker-compose.deps.yml --env-file docker-PAI/docker.env up -d