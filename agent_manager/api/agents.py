import logging
import os
from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
from ..redis_client import get_redis
from ..db import get_db
from ..models import AgentConfigInput, AgentConfigOutput, AgentStatus, AgentListItem, IntegrationStatus, IntegrationType, AgentConfigStructure
from .. import crud
from ..import process_manager
import json
import asyncio

logger = logging.getLogger(__name__)
router = APIRouter()

SOURCES_API_BASE_URL = os.getenv("SOURCES_API_BASE_URL") # Получаем URL из .env

# --- Agent Management Endpoints ---

@router.post(
    "/",
    response_model=AgentConfigOutput,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new agent configuration",
    tags=["Agents"]
)
async def create_agent(
    agent_config: AgentConfigInput,
    r: redis.Redis = Depends(get_redis), # Keep Redis for status
    db: AsyncSession = Depends(get_db) # Add DB session
):
    """
    Creates a new agent configuration and automatically starts the agent and its integrations.
    - **name**: User-defined name for the agent.
    - **description**: Optional description.
    - **userId**: Identifier for the owner (placeholder).
    - **config**: The actual agent configuration structure (e.g., model, tools).
    """
    # TODO: Validate userId against authenticated user
    # TODO: Consider more robust agent ID generation (e.g., UUID)
    agent_id = f"agent_{agent_config.name.lower().replace(' ', '_')}_{os.urandom(4).hex()}"
    try:
        # Use DB CRUD
        db_agent = await crud.db_create_agent_config(db, agent_config, agent_id)

        # Set initial status in Redis (keep status in Redis for now)
        status_key = f"agent_status:{agent_id}"
        await r.hset(status_key, mapping={"status": "stopped"}) # Start as stopped before attempting start

        # --- Start Agent Process ---
        logger.info(f"Agent {agent_id} created. Attempting to start process...")
        try:
            await process_manager.start_agent_process(agent_id, r)
            logger.info(f"Successfully initiated start for agent process {agent_id}.")
        except Exception as start_err:
            # Log error but don't fail the creation response
            logger.error(f"Failed to auto-start agent process {agent_id} after creation: {start_err}", exc_info=True)
            # Status should reflect the error (e.g., error_start_failed)

        # --- Start Integrations ---
        try:
            config_data = db_agent.config_json or {}
            simple_config = config_data.get("simple", {})
            settings_data = simple_config.get("settings", {})
            integrations_config = settings_data.get("integrations", [])

            if integrations_config:
                logger.info(f"Found {len(integrations_config)} integrations for agent {agent_id}. Attempting to start...")
                for integration in integrations_config:
                    integration_type_str = integration.get("type")
                    integration_settings = integration.get("settings", {})
                    try:
                        integration_type_enum = IntegrationType(integration_type_str)
                        logger.info(f"Attempting to start {integration_type_enum.value} integration for {agent_id}...")
                        await process_manager.start_integration_process(
                            agent_id=agent_id,
                            integration_type=integration_type_enum,
                            r=r,
                            integration_settings=integration_settings
                        )
                        logger.info(f"Successfully initiated start for {integration_type_enum.value} integration for {agent_id}.")
                    except ValueError:
                        logger.error(f"Unsupported integration type '{integration_type_str}' found for agent {agent_id}.")
                    except Exception as int_start_err:
                        # Log error but don't fail the creation response
                        logger.error(f"Failed to auto-start {integration_type_str} integration for {agent_id}: {int_start_err}", exc_info=True)
            else:
                logger.info(f"No integrations configured for agent {agent_id} to auto-start.")

        except Exception as config_err:
            logger.error(f"Error reading integrations config for auto-start (agent {agent_id}): {config_err}", exc_info=True)
        # --- End Start Integrations ---


        # Convert DB model to Pydantic output model
        # Need to parse config_json back into the Pydantic structure
        config_structure = AgentConfigStructure.model_validate(db_agent.config_json)
        return AgentConfigOutput(
            id=db_agent.id,
            name=db_agent.name,
            description=db_agent.description,
            userId=db_agent.user_id,
            config=config_structure,
            created_at=db_agent.created_at,
            updated_at=db_agent.updated_at
        )
    except Exception as e:
        logger.error(f"Failed to create agent config for {agent_id}: {e}", exc_info=True)
        # Check if it's a DB integrity error (e.g., duplicate ID, though unlikely with hex)
        # Or just raise a generic 500
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to save agent configuration.")


@router.get(
    "/",
    response_model=List[AgentListItem],
    summary="List all agents",
    tags=["Agents"]
)
async def list_agents(
    skip: int = 0,
    limit: int = 100,
    r: redis.Redis = Depends(get_redis), # Keep Redis for status
    db: AsyncSession = Depends(get_db) # Add DB session
):
    """Retrieves a list of all configured agents with their basic info and status."""
    # Use DB CRUD
    db_agents = await crud.db_get_all_agents(db, skip=skip, limit=limit)
    agents_list = []
    for db_agent in db_agents:
        # Fetch status from Redis
        status_info = await process_manager.get_agent_status(db_agent.id, r)
        agents_list.append(AgentListItem(
            id=db_agent.id,
            name=db_agent.name,
            description=db_agent.description,
            status=status_info.status # Use status from Redis
        ))
    return agents_list

@router.put(
    "/{agent_id}",
    response_model=AgentConfigOutput,
    summary="Update an agent configuration",
    tags=["Agents"]
)
async def update_agent(
    agent_id: str,
    agent_config: AgentConfigInput,
    r: redis.Redis = Depends(get_redis), # Для проверки статуса и публикации
    db: AsyncSession = Depends(get_db)
):
    """
    Updates an existing agent's configuration.
    - **agent_id**: The ID of the agent to update.
    - **agent_config**: The new configuration data matching AgentConfigInput schema.

    **Note:** If the agent is currently running, its process will be signaled to
    perform an internal restart via Redis Pub/Sub to apply the new configuration.
    This involves a brief interruption and potential loss of conversation state.
    """
    # Проверяем статус агента *перед* обновлением
    initial_status_info = await process_manager.get_agent_status(agent_id, r)
    is_running = initial_status_info.status in ["running", "starting", "initializing"] # Считаем эти статусы "запущенными"

    try:
        # Используем DB CRUD для обновления
        updated_db_agent = await crud.db_update_agent_config(db, agent_id, agent_config)

        if updated_db_agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

        # Если агент был запущен, отправляем сигнал на внутренний перезапуск
        if is_running:
            control_channel = f"agent_control:{agent_id}"
            # Меняем команду на restart
            restart_command = json.dumps({"command": "restart"})
            try:
                # Отправляем сообщение клиентам WebSocket о предстоящем перезапуске (опционально)
                # await websocket_api.manager.broadcast_to_agent(agent_id, json.dumps({"type": "status", "message": "Agent configuration updated. Restarting soon..."}))

                await r.publish(control_channel, restart_command)
                logger.info(f"Published restart command to {control_channel} for agent {agent_id} after config update.")
            except Exception as pub_e:
                logger.error(f"Failed to publish restart command to {control_channel} for agent {agent_id}: {pub_e}")
                # Не прерываем основной процесс обновления, но логируем ошибку

        # Преобразуем обновленную DB модель в Pydantic output модель
        config_structure = AgentConfigStructure.model_validate(updated_db_agent.config_json)
        return AgentConfigOutput(
            id=updated_db_agent.id,
            name=updated_db_agent.name,
            description=updated_db_agent.description,
            userId=updated_db_agent.user_id,
            config=config_structure,
            created_at=updated_db_agent.created_at,
            updated_at=updated_db_agent.updated_at # SQLAlchemy onupdate должен обновить это
        )
    except HTTPException as http_exc:
        raise http_exc # Перевыбрасываем HTTP исключения (например, 404)
    except Exception as e:
        logger.error(f"Failed to update agent config for {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update agent configuration.")


@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an agent",
    tags=["Agents"]
)
async def delete_agent(
    agent_id: str,
    r: redis.Redis = Depends(get_redis), # Keep Redis for status and process management
    db: AsyncSession = Depends(get_db) # Add DB session
):
    """
    Deletes an agent configuration from the database and cleans up Redis status.
    Sends a shutdown command to the agent process if it's running.
    """
    # 1. Check if agent exists in DB first
    db_agent_exists = await crud.db_get_agent_config(db, agent_id)
    if not db_agent_exists:
         # If not in DB, maybe clean up Redis status just in case and return 404
         await r.delete(f"agent_status:{agent_id}") # Clean up potentially orphaned status
         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    # 2. Send shutdown command if agent process is running
    try:
        status_info = await process_manager.get_agent_status(agent_id, r)
        # Allow deletion if stopped/error/lost/stopping/not_found etc.
        if status_info.status in ["running", "starting", "initializing"]:
            logger.warning(f"Attempting to delete agent {agent_id} while it is {status_info.status}. Sending shutdown command.")
            control_channel = f"agent_control:{agent_id}"
            shutdown_command = json.dumps({"command": "shutdown"}) # Send shutdown, not restart
            await r.publish(control_channel, shutdown_command)
            # Wait a short time for the agent to potentially stop
            await asyncio.sleep(2) # Give it a moment
            # Re-check status (optional, deletion proceeds anyway)
            status_info = await process_manager.get_agent_status(agent_id, r)
            if status_info.status not in ["stopped", "stopping", "error_process_lost", "not_found"]: # Добавим not_found
                 logger.warning(f"Agent {agent_id} status is still {status_info.status} after shutdown command. Proceeding with deletion.")

    except Exception as e:
         # Handle potential errors from get_agent_status or publish if they raise exceptions
         logger.error(f"Error checking/signaling agent {agent_id} during deletion: {e}", exc_info=True)
         # Proceed with deletion anyway, but log the error

    # 3. Delete configuration from DB
    try:
        deleted_db = await crud.db_delete_agent_config(db, agent_id)
        if not deleted_db:
             logger.warning(f"Agent {agent_id} existed but failed to delete from DB.")
             raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete agent from database.")
    except Exception as e:
         logger.error(f"Error deleting agent {agent_id} from DB: {e}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error during agent deletion.")

    # 4. Clean up Redis status key
    await r.delete(f"agent_status:{agent_id}")
    logger.info(f"Cleaned up Redis status for deleted agent {agent_id}")

    return None # Return 204 No Content

@router.get(
    "/{agent_id}/config",
    response_model=AgentConfigOutput, # Return raw config dict for runner
    summary="Get raw agent configuration",
    tags=["Agents", "Internal"]
)
async def get_agent_config_for_runner(
    agent_id: str,
    r: redis.Redis = Depends(get_redis), # Keep Redis for status check? Not strictly needed here
    db: AsyncSession = Depends(get_db) # Add DB session
):
    """
    Internal endpoint for the agent runner process to fetch its configuration.
    Returns the raw configuration dictionary stored in the database.
    If knowledgeBase tools are present, replaces knowledgeBaseIds with source IDs fetched from an external API.
    """
    # Use DB CRUD
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if db_agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    # --- Начало модификации: Получение source_ids ---
    config_json_mutable = db_agent.config_json.copy() # Создаем изменяемую копию

    if not SOURCES_API_BASE_URL:
        logger.warning("SOURCES_API_BASE_URL environment variable is not set. Cannot fetch source IDs for knowledge bases.")
    else:
        try:
            tools = config_json_mutable.get("simple", {}).get("settings", {}).get("tools", [])
            async with httpx.AsyncClient(timeout=10.0) as client: # Используем httpx для асинхронных запросов
                for tool in tools:
                    if tool.get("type") == "knowledgeBase":
                        settings = tool.get("settings", {})
                        knowledge_base_ids = settings.get("knowledgeBaseIds")
                        if knowledge_base_ids and isinstance(knowledge_base_ids, list):
                            logger.info(f"Agent {agent_id}: Found knowledgeBase tool with IDs: {knowledge_base_ids}. Fetching source IDs...")
                            source_ids_raw = [] # Храним исходные ID (могут быть int)
                            fetch_tasks = []

                            # Создаем задачи для получения источников для каждого ID базы знаний
                            for kb_id in knowledge_base_ids:
                                api_url = f"{SOURCES_API_BASE_URL}/admin-sources/?datastoreId={kb_id}&status=sync"
                                fetch_tasks.append(client.get(api_url))

                            # Выполняем запросы параллельно
                            responses = await asyncio.gather(*fetch_tasks, return_exceptions=True)

                            for i, response in enumerate(responses):
                                kb_id = knowledge_base_ids[i]
                                if isinstance(response, httpx.Response):
                                    if response.status_code == 200:
                                        try:
                                            sources_data = response.json()
                                            if isinstance(sources_data, list):
                                                # Получаем ID, проверяем что они не None
                                                found_ids = [source.get("id") for source in sources_data if source.get("id") is not None]
                                                source_ids_raw.extend(found_ids) # Добавляем в список исходных ID
                                                logger.debug(f"Agent {agent_id}: Fetched {len(found_ids)} source IDs (raw) for KB ID {kb_id}.")
                                            else:
                                                logger.warning(f"Agent {agent_id}: Unexpected response format from sources API for KB ID {kb_id}. Expected list, got {type(sources_data)}.")
                                        except json.JSONDecodeError:
                                            logger.error(f"Agent {agent_id}: Failed to decode JSON response from sources API for KB ID {kb_id}.")
                                    else:
                                        logger.error(f"Agent {agent_id}: Error fetching sources for KB ID {kb_id}. Status: {response.status_code}, Response: {response.text[:200]}")
                                elif isinstance(response, Exception):
                                    logger.error(f"Agent {agent_id}: Exception fetching sources for KB ID {kb_id}: {response}")

                            # --- ИСПРАВЛЕНИЕ: Преобразуем все ID в строки ---
                            source_ids_str = [str(sid) for sid in source_ids_raw]

                            if source_ids_str:
                                logger.info(f"Agent {agent_id}: Replacing knowledgeBaseIds {knowledge_base_ids} with source IDs (as strings) {source_ids_str}")
                                settings["knowledgeBaseIds"] = source_ids_str # Заменяем список ID строками
                            else:
                                logger.warning(f"Agent {agent_id}: No source IDs found for knowledgeBaseIds {knowledge_base_ids}. Keeping original IDs or potentially empty list.")
                                # Решите, оставить ли старые ID или сделать список пустым, если ничего не найдено
                                # settings["knowledgeBaseIds"] = [] # Раскомментируйте, если нужно очищать список при неудаче

        except Exception as e:
            logger.error(f"Agent {agent_id}: Error processing tools to fetch source IDs: {e}", exc_info=True)
            # В случае ошибки обработки, возвращаем исходную конфигурацию (config_json_mutable может быть частично изменен)
            # Можно вернуть db_agent.config_json, если нужна гарантия неизменности при ошибке

    # --- Конец модификации ---


    # Return the raw JSON/Dict stored in the DB
    # Add agent_id, name etc. to the returned dict for the runner?
    # The runner currently expects the structure from AgentConfigOutput.model_dump()
    # Let's mimic that for now, using the potentially modified config_json_mutable.
    try:
        # Используем модифицированную конфигурацию
        config_structure = AgentConfigStructure.model_validate(config_json_mutable)
        output_model = AgentConfigOutput(
            id=db_agent.id,
            name=db_agent.name,
            description=db_agent.description,
            userId=db_agent.user_id,
            config=config_structure,
            created_at=db_agent.created_at,
            updated_at=db_agent.updated_at
        )
    except Exception as validation_err:
         logger.error(f"Agent {agent_id}: Error validating modified config structure: {validation_err}", exc_info=True)
         # Если валидация не удалась, возвращаем исходную структуру
         config_structure_original = AgentConfigStructure.model_validate(db_agent.config_json)
         output_model = AgentConfigOutput(
             id=db_agent.id,
             name=db_agent.name,
             description=db_agent.description,
             userId=db_agent.user_id,
             config=config_structure_original,
             created_at=db_agent.created_at,
             updated_at=db_agent.updated_at
         )


    # Логируем конфигурацию перед отправкой, используя model_dump_json()
    try:
        # Используем model_dump_json() для корректной сериализации datetime в строку JSON
        config_json_for_log = output_model.model_dump_json()
        # logger.info(f"Returning config for runner {agent_id}: {config_json_for_log}")
    except Exception as log_err:
        # Логируем ошибку сериализации для лога, но не прерываем основной ответ
        logger.error(f"Error serializing config to JSON for logging (agent {agent_id}): {log_err}")

    # Возвращаем словарь, FastAPI сам его корректно сериализует в JSON ответ
    return output_model.model_dump()

@router.post(
    "/{agent_id}/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an agent process",
    tags=["Agents"]
)
async def start_agent(
    agent_id: str,
    r: redis.Redis = Depends(get_redis), # Keep Redis for status/process management
    db: AsyncSession = Depends(get_db) # Add DB session to check config existence
):
    """Initiates the start of the agent runner process."""
    # Check if config exists in DB before trying to start
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        # Use process_manager.start_agent_process
        success = await process_manager.start_agent_process(agent_id, r)
        # start_agent_process now returns True/False or raises specific exceptions
        # The function already updates Redis status on error, so we check that
        if not success:
             # This case might not be reached if exceptions are raised, but handle defensively
             status_info = await process_manager.get_agent_status(agent_id, r)
             raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Agent start initiated but failed (status: {status_info.status}).")

    except FileNotFoundError as e:
         logger.error(f"Agent start failed for {agent_id}: Runner script not found.", exc_info=True)
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Agent runner script not found.")
    except ValueError as e: # Raised if config check fails (if implemented in process_manager)
         logger.error(f"Agent start failed for {agent_id}: {e}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e: # Raised on other launch errors
         logger.error(f"Agent start failed for {agent_id}: {e}", exc_info=True)
         # Check Redis status for more specific error if available
         status_info = await process_manager.get_agent_status(agent_id, r)
         detail = f"Agent process failed to launch (status: {status_info.status})."
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
    except Exception as e:
         logger.error(f"Unexpected error starting agent {agent_id}: {e}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred during agent start.")

    return {"message": "Agent start initiated"}


@router.post(
    "/{agent_id}/stop",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Stop an agent process",
    tags=["Agents"]
)
async def stop_agent(
    agent_id: str,
    force: bool = Query(False, description="Force stop using SIGKILL if SIGTERM fails."),
    r: redis.Redis = Depends(get_redis), # Keep Redis for status/process management
    db: AsyncSession = Depends(get_db) # Add DB session to check config existence
):
    """Initiates the stop of the agent runner process (SIGTERM by default)."""
     # Check if config exists in DB (optional, but good practice)
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        # If not in DB, clean up Redis status and return appropriate message/status
        await r.delete(f"agent_status:{agent_id}")
        # Return 404 or 202 with message? Let's use 404 as the primary resource is gone.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        # Use process_manager.stop_agent_process
        success = await process_manager.stop_agent_process(agent_id, r, force=force)
        if not success:
            status_info = await process_manager.get_agent_status(agent_id, r) # Re-fetch status
            detail = f"Agent stop initiated, but encountered an error or agent did not terminate gracefully (status: {status_info.status})."
            logger.error(f"Stop agent {agent_id} reported failure. Status: {status_info.status}")
            # Return 202 Accepted but with error detail in message
            return {"message": detail}
    except Exception as e:
         logger.error(f"Unexpected error stopping agent {agent_id}: {e}", exc_info=True)
         # Return 202 but indicate error
         return {"message": f"Agent stop initiated, but an unexpected error occurred: {e}"}

    return {"message": "Agent stop initiated"}


@router.post(
    "/{agent_id}/restart",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restart an agent process",
    tags=["Agents"]
)
async def restart_agent(
    agent_id: str,
    r: redis.Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db)
):
    """Initiates the restart of the agent runner process (stop --force then start)."""
    # Check if config exists
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        success = await process_manager.restart_agent_process(agent_id, r)
        if not success:
            # Fetch status to provide more context
            status_info = await process_manager.get_agent_status(agent_id, r)
            detail = f"Agent restart initiated, but failed during stop or start (current status: {status_info.status}). Check logs for details."
            logger.error(f"Restart agent {agent_id} reported failure. Status: {status_info.status}")
            # Return 202 but indicate failure in message
            return {"message": detail}
    except Exception as e:
        # Catch potential exceptions from restart_agent_process itself
        logger.error(f"Unexpected error during agent restart for {agent_id}: {e}", exc_info=True)
        # Return 202 but indicate error
        return {"message": f"Agent restart initiated, but an unexpected error occurred: {e}"}

    return {"message": "Agent restart initiated"}


@router.get(
    "/{agent_id}/status",
    response_model=AgentStatus,
    summary="Get agent status",
    tags=["Agents"]
)
async def get_agent_status_api(
    agent_id: str,
    r: redis.Redis = Depends(get_redis) # Status is still primarily in Redis
):
    """Retrieves the current status of the agent process from Redis."""
    # No DB interaction needed here, status is in Redis
    status_info = await process_manager.get_agent_status(agent_id, r)
    if status_info.status == "not_found":
         # Check DB for config existence to differentiate between truly not found vs just stopped
         db_agent = await crud.db_get_agent_config(await get_db().__anext__(), agent_id) # Need DB session
         if db_agent:
             # Config exists, but no status key -> treat as stopped
             return AgentStatus(agent_id=agent_id, status="stopped")
         else:
             raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return status_info

# --- Integration Management Endpoints ---
# These endpoints primarily interact with process_manager, which uses Redis for status.
# They might need DB access only to verify the agent config exists before starting/stopping integrations.

@router.get("/{agent_id}/integrations/{integration_type}/status", response_model=IntegrationStatus)
async def get_integration_status_api(
    agent_id: str,
    integration_type: IntegrationType, # Use the Enum for path validation
    r: redis.Redis = Depends(get_redis) # Status is in Redis
    # db: AsyncSession = Depends(get_db) # <--- Удаляем зависимость DB отсюда
):
    """Get the status of a specific integration (e.g., telegram) for an agent."""
    # Check if agent config exists in DB first
    try: # Добавляем try-finally для гарантии закрытия сессии
        db_session = await get_db().__anext__() # <--- Получаем сессию вручную
        if not db_session:
             logger.error(f"Failed to get DB session for integration status check (agent: {agent_id})")
             raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not get database session.")

        # logger.info(f"Checking DB for agent_id: '{agent_id}' in get_integration_status_api") # Лог для отладки
        db_agent = await crud.db_get_agent_config(db_session, agent_id) # <--- Используем полученную сессию
        if not db_agent:
            logger.warning(f"Agent config NOT FOUND in DB for agent_id: '{agent_id}' in get_integration_status_api") # Лог для отладки
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

        # logger.info(f"Agent config found for agent_id: '{agent_id}'. Fetching integration status.") # Лог для отладки
        # Fetch status from Redis
        return await process_manager.get_integration_status(agent_id, integration_type, r)
    except StopAsyncIteration:
         logger.error(f"DB session generator finished unexpectedly for integration status check (agent: {agent_id})")
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database session error.")
    except Exception as e:
         logger.error(f"Error during integration status check for agent {agent_id}: {e}", exc_info=True)
         # Перехватываем HTTPException, чтобы не перекрыть 404
         if isinstance(e, HTTPException):
             raise e
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error during status check.")
    # finally:
        # FastAPI должен автоматически обработать закрытие генератора get_db(),
        # поэтому явный вызов __aclose__ здесь не требуется и может вызвать проблемы.
        # if 'db_session' in locals() and db_session:
        #     try:
        #         await get_db().__aclose__(None) # Попытка закрыть, если нужно, но обычно не требуется
        #     except Exception as close_err:
        #         logger.warning(f"Error trying to manually close DB session: {close_err}")


@router.post("/{agent_id}/integrations/{integration_type}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_integration(
    agent_id: str,
    integration_type: IntegrationType,
    r: redis.Redis = Depends(get_redis), # Process manager uses Redis
    db: AsyncSession = Depends(get_db) # <--- Оставляем здесь зависимость, т.к. она работает в POST
):
    """Start a specific integration process (e.g., telegram bot) for an agent."""
    # Check if agent config exists in DB first
    # Используем сессию из зависимости, т.к. для POST-запросов она, видимо, работает
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    # Proceed with starting the integration process
    try:
        success = await process_manager.start_integration_process(agent_id, integration_type, r)
        if not success:
             # Check status for specific error
             status_info = await process_manager.get_integration_status(agent_id, integration_type, r)
             detail = f"Failed to start {integration_type.value} integration (status: {status_info.status})."
             raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
    except FileNotFoundError as e:
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"{integration_type.value} integration script not found.")
    except ValueError as e: # e.g., unsupported type
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e: # e.g., launch failed
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"{integration_type.value} integration process failed to launch.")
    except Exception as e:
         logger.error(f"Unexpected error starting integration {integration_type.value} for {agent_id}: {e}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred during integration start.")

    return {"message": f"{integration_type.value} integration start initiated"}


@router.post("/{agent_id}/integrations/{integration_type}/stop", status_code=status.HTTP_202_ACCEPTED)
async def stop_integration(
    agent_id: str,
    integration_type: IntegrationType,
    force: bool = Query(False, description="Force stop using SIGKILL instead of SIGTERM"),
    r: redis.Redis = Depends(get_redis), # Process manager uses Redis
    db: AsyncSession = Depends(get_db) # Add DB session to check config existence
):
    """Stop a specific integration process (e.g., telegram bot) for an agent."""
    # Check if agent config exists in DB first (optional, but good practice)
    # Используем сессию из зависимости
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        # Clean up Redis status and return 404
        status_key = process_manager._get_integration_status_key(agent_id, integration_type)
        await r.delete(status_key)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    # Proceed with stopping the integration process
    try:
        success = await process_manager.stop_integration_process(agent_id, integration_type, r, force=force)
        if not success:
            status_info = await process_manager.get_integration_status(agent_id, integration_type, r)
            detail = f"{integration_type.value} integration stop initiated, but encountered an error or did not terminate gracefully (status: {status_info.status})."
            logger.error(f"Stop integration {integration_type.value} for {agent_id} reported failure. Status: {status_info.status}")
            return {"message": detail}
    except Exception as e:
         logger.error(f"Unexpected error stopping integration {integration_type.value} for {agent_id}: {e}", exc_info=True)
         return {"message": f"{integration_type.value} integration stop initiated, but an unexpected error occurred: {e}"}

    return {"message": f"{integration_type.value} integration stop initiated"}


@router.post("/{agent_id}/integrations/{integration_type}/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart_integration(
    agent_id: str,
    integration_type: IntegrationType,
    r: redis.Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db)
):
    """Initiates the restart of a specific integration process (stop --force then start)."""
    # Check if agent config exists
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        success = await process_manager.restart_integration_process(agent_id, integration_type, r)
        if not success:
            status_info = await process_manager.get_integration_status(agent_id, integration_type, r)
            detail = f"{integration_type.value} integration restart initiated, but failed during stop or start (current status: {status_info.status}). Check logs."
            logger.error(f"Restart integration {integration_type.value} for {agent_id} reported failure. Status: {status_info.status}")
            return {"message": detail}
    except Exception as e:
        logger.error(f"Unexpected error during integration restart for {agent_id}/{integration_type.value}: {e}", exc_info=True)
        return {"message": f"{integration_type.value} integration restart initiated, but an unexpected error occurred: {e}"}

    return {"message": f"{integration_type.value} integration restart initiated"}

