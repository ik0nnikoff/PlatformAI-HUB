import logging
import json
import asyncio
from typing import Optional # Removed Dict, List as they are not directly used in this file's annotations after the move

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, status as fastapi_status
from starlette.websockets import WebSocketState
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis
import redis as redis_base # For exceptions

from app.core.dependencies import get_redis_client, get_db
from app.db.crud import agent_crud # Corrected: agent_crud was used, not user_crud directly here
# Updated imports for ConnectionManager and central_redis_listener
from app.services.websocket_manager import ConnectionManager, central_redis_listener

logger = logging.getLogger(__name__)
router = APIRouter()

# Глобальный экземпляр менеджера
manager = ConnectionManager()

# central_redis_listener is now imported from app.services.websocket_manager
# The definition has been removed from this file.

# ConnectionManager class definition has been removed from this file.

@router.websocket("/ws/agents/{agent_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    agent_id: str,
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db)
):
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        logger.warning(f"WebSocket connection attempt for non-existent agent (DB check): {agent_id}")
        # It's important to accept the connection before sending a message and closing
        await websocket.accept() 
        await websocket.send_text(json.dumps({"type": "error", "content": f"Agent '{agent_id}' not found."}))
        await websocket.close(code=fastapi_status.WS_1008_POLICY_VIOLATION)
        return

    # Accept should happen before any manager interaction if agent exists
    # await websocket.accept() # Moved up for the error case, already accepted if we reach here and db_agent exists.
    # If not accepted during error, accept it now.
    if websocket.client_state != WebSocketState.CONNECTED:
        await websocket.accept()
        
    logger.info(f"WebSocket connection accepted for agent {agent_id}")

    initial_thread_id: Optional[str] = None
    manager_connected = False

    try:
        while True:
            data = await websocket.receive_text()
            
            try:
                payload = json.loads(data)
                received_thread_id = payload.get("thread_id")
                message_to_agent = payload.get("message", payload.get("content"))
                received_channel = payload.get("channel", "websocket")

                if not received_thread_id:
                    await websocket.send_text(json.dumps({"type": "error", "content": "Missing 'thread_id' in message."}))
                    continue
                if message_to_agent is None:
                    await websocket.send_text(json.dumps({"type": "error", "content": "Missing 'message' or 'content' field in message."}))
                    continue

            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "content": "Invalid JSON received."}))
                continue
            except Exception as parse_err:
                logger.error(f"Error parsing WebSocket message for {agent_id}: {parse_err}", exc_info=True)
                await websocket.send_text(json.dumps({"type": "error", "content": "Error parsing message."}))
                continue

            if not manager_connected:
                initial_thread_id = received_thread_id
                setattr(websocket, "_thread_id_for_cleanup", initial_thread_id)
                # Pass the imported central_redis_listener to the manager
                await manager.connect(websocket, agent_id, initial_thread_id, r, central_redis_listener)
                manager_connected = True
                logger.info(f"WebSocket registered with manager for agent {agent_id}, thread {initial_thread_id}")
            
            elif received_thread_id != initial_thread_id:
                logger.warning(f"WebSocket for agent {agent_id} sent message with different thread_id ({received_thread_id}). Expected {initial_thread_id}. Ignoring.")
                await websocket.send_text(json.dumps({"type": "error", "content": f"Thread ID mismatch. Connection bound to {initial_thread_id}."}))
                continue

            input_channel = f"agent:{agent_id}:input"
            agent_payload = {
                "message": message_to_agent,
                "thread_id": received_thread_id,
                "channel": received_channel,
                "user_data": payload.get("user_data", {})
            }
            try:
                await r.publish(input_channel, json.dumps(agent_payload))
            except redis_base.exceptions.ConnectionError as e:
                logger.error(f"Redis connection error publishing WS message to {input_channel}: {e}")
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "error", "content": "Failed to send message to agent (Redis connection)"}))
            except Exception as e:
                logger.error(f"Error publishing WS message to {input_channel}: {e}", exc_info=True)
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_text(json.dumps({"type": "error", "content": "Failed to send message to agent"}))

    except WebSocketDisconnect:
        logger.info(f"WebSocket client for agent {agent_id} (thread: {initial_thread_id or 'unknown'}) disconnected.")
    except Exception as e:
        if not isinstance(e, asyncio.CancelledError):
             logger.error(f"Unexpected error in WebSocket endpoint for agent {agent_id} (thread: {initial_thread_id or 'unknown'}): {e}", exc_info=True)
        if websocket.client_state == WebSocketState.CONNECTED:
            # Attempt to close gracefully if an unexpected error occurs
            try:
                await websocket.close(code=fastapi_status.WS_1011_INTERNAL_ERROR)
            except RuntimeError as re:
                logger.warning(f"RuntimeError during WebSocket close for agent {agent_id}, thread {initial_thread_id or 'unknown'}: {re} - likely already closed.")
            except Exception as close_e:
                logger.error(f"Exception during WebSocket close for agent {agent_id}, thread {initial_thread_id or 'unknown'}: {close_e}")
    finally:
        logger.info(f"Cleaning up WebSocket connection for agent {agent_id} (thread: {initial_thread_id or 'unknown'})")
        if manager_connected and initial_thread_id: # Ensure manager was connected and thread_id is known
            thread_id_to_disconnect = getattr(websocket, "_thread_id_for_cleanup", initial_thread_id) # Fallback to initial_thread_id if attr not set
            if thread_id_to_disconnect: # Should always be true if initial_thread_id was set
                await manager.disconnect(websocket, agent_id, thread_id_to_disconnect)
            else:
                logger.warning(f"Could not determine thread_id for cleanup for agent {agent_id}. WebSocket might not have been fully registered.")

# Removed TODOs that are now addressed by moving ConnectionManager and listener
