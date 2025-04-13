qdrant:
	uv run qdrant-worker.py
bot:
	uv run last_bot/bot.py
agent:
	uv run last_bot/agent.py
docker:
	docker compose -f docker-PAI/docker-compose.deps.yml --env-file docker-PAI/docker.env up -d
worker:
	uv run worker.py
worker_qdrant:
	uv run qdrant_sync_worker.py
workers:
	uv run worker.py & \
	uv run qdrant_sync_worker.py & \
	wait
stop_workers:
	pkill -f "uv run worker.py" || true
	pkill -f "uv run qdrant_sync_worker.py" || true