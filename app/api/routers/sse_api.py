import logging
import json
import asyncio
from typing import Any, AsyncGenerator, Dict, Optional

from redis.exceptions import ConnectionError as RedisConnectionError
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base.redis_manager import RedisClientManager
from app.db.session import get_db_session
from app.db.crud import agent_crud

logger = logging.getLogger(__name__)
router = APIRouter()
redis = RedisClientManager()

# --- SSE Stream Logic ---
async def sse_stream(agent_id: str, thread_id: str) -> AsyncGenerator[str, None]:
    """
    Генератор для отправки данных через SSE, фильтруя по thread_id.
    Включает механизм keep-alive.
    """
    await redis.setup()
    redis_cli = redis._redis_client
    pubsub = None
    keep_alive_interval = 30.0  # секунды
    last_event_time = asyncio.get_event_loop().time()

    try:
        output_channel = f"agent:{agent_id}:output"
        pubsub = redis_cli.pubsub()
        await pubsub.subscribe(output_channel)

        yield "event: connected\ndata: {\"message\": \"SSE connection established\"}\n\n" # Send structured connected event
        last_event_time = asyncio.get_event_loop().time()

        while True:
            if not redis.is_redis_client_available:
                logger.warning(f"SSE stream for agent {agent_id}, thread {thread_id}: Redis connection lost. Breaking loop.")
                yield f"event: error\ndata: {json.dumps({'error': 'Redis connection lost. Stream terminated.'})}\n\n"
                break

            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            current_time = asyncio.get_event_loop().time()

            if message and message.get("type") == "message":
                try:
                    data = json.loads(message["data"])
                    if data.get("chat_id") == thread_id:
                        yield f"data: {json.dumps(data)}\n\n"
                        last_event_time = current_time
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON received in SSE stream for agent {agent_id}, thread {thread_id}: {message['data']}")
                except Exception as e:
                    logger.error(f"Error processing message in SSE stream for agent {agent_id}, thread {thread_id}: {e}", exc_info=True)
            else:  # message is None (timeout pubsub.get_message)
                if current_time - last_event_time >= keep_alive_interval:
                    yield "event: heartbeat\ndata: {\"type\": \"heartbeat\"}\n\n" # Send structured heartbeat
                    last_event_time = current_time
            
            await asyncio.sleep(0.01) # Short sleep to prevent tight loop if no messages and no keep-alive needed

    except asyncio.CancelledError:
        logger.info(f"SSE connection for agent {agent_id}, thread {thread_id} closed by client.")
        raise
    except RedisConnectionError as e:
        logger.error(f"Redis connection error in SSE stream for agent {agent_id}, thread {thread_id}: {e}")
        try:
            yield f"event: error\ndata: {json.dumps({'error': 'Redis connection error during stream setup.'})}\n\n"
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Unexpected error in SSE stream for agent {agent_id}, thread {thread_id}: {e}", exc_info=True)
        try:
            yield f"event: error\ndata: {json.dumps({'error': 'Internal stream error.'})}\n\n"
        except Exception:
            pass
    finally:
        if pubsub:
            try:
                if redis.is_redis_client_available: # Check connection before trying to unsubscribe
                    await pubsub.unsubscribe(output_channel)
                await pubsub.aclose() # Close pubsub object itself
                logger.info(f"SSE stream for agent {agent_id}, thread {thread_id}: Unsubscribed and closed pubsub.")
            except Exception as e_pubsub_close:
                logger.error(f"SSE stream for agent {agent_id}, thread {thread_id}: Error closing pubsub: {e_pubsub_close}")
        logger.info(f"SSE stream for agent {agent_id}, thread {thread_id} finished.")


@router.get("/{agent_id}/sse", tags=["SSE"], summary="Connect to agent event stream")
async def agent_sse(
    agent_id: str,
    thread_id: str = Query(..., description="Unique identifier for the chat thread"),
    db: AsyncSession = Depends(get_db_session)
):
    """
    Endpoint for SSE (Server-Sent Events) with filtering by thread_id.
    Streams events related to a specific agent and chat thread.
    """
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=404, detail=f"Agent with id '{agent_id}' not found")
    
    logger.info(f"SSE connection requested for agent {agent_id}, thread {thread_id}")
    return StreamingResponse(sse_stream(agent_id, thread_id), media_type="text/event-stream")


# --- Send Message to Agent Endpoint (related to SSE/real-time interaction) ---
@router.post("/{agent_id}/messages", tags=["SSE"], summary="Send a message to an agent")
async def send_message_to_agent(
    agent_id: str,
    message_data: Dict[str, Any], 
    db: AsyncSession = Depends(get_db_session)
):
    """
    Endpoint to send a message to an agent's input channel.
    The message will be processed by the agent and potentially broadcast via SSE.
    
    Required fields in `message_data`:
    - `thread_id`: str
    - `message`: str

    Optional fields in `message_data`:
    - `channel`: str (defaults to 'api')
    - `user_data`: dict (any additional user-specific data)
    """
    await redis.setup()
    redis_cli = redis._redis_client
    thread_id = message_data.get("thread_id")
    message_content = message_data.get("message") # Renamed from 'message' to avoid conflict
    channel = message_data.get("channel", "api") # Default channel from message_data or 'api'

    if not isinstance(thread_id, str) or not thread_id:
        raise HTTPException(status_code=400, detail="Invalid or missing 'thread_id' in request body. Must be a non-empty string.")
    if not isinstance(message_content, str) or not message_content: # Check for non-empty message
        raise HTTPException(status_code=400, detail="Invalid or missing 'message' in request body. Must be a non-empty string.")
    if not isinstance(channel, str) or not channel:
        raise HTTPException(status_code=400, detail="Invalid or missing 'channel'. Must be a non-empty string.")

    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=404, detail=f"Agent with id '{agent_id}' not found")

    payload = {
        "text": message_content,
        "chat_id": thread_id,
        "channel": channel,
        "user_data": message_data.get("user_data", {}) # Ensure user_data is a dict
    }

    input_channel = f"agent:{agent_id}:input"
    try:
        await redis_cli.publish(input_channel, json.dumps(payload))
        logger.info(f"Message published to {input_channel} for agent {agent_id}, thread {thread_id}")
        return {"status": "success", "message": "Message sent to agent."}
    except RedisConnectionError as e:
        logger.error(f"Redis connection error when publishing message to {input_channel}: {e}")
        raise HTTPException(status_code=503, detail="Could not connect to Redis to send message.")
    except Exception as e:
        logger.error(f"Failed to publish message to {input_channel} for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to send message to agent.")

