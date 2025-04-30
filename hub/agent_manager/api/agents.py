import logging
import os
# --- ИЗМЕНЕНИЕ: Убедимся, что httpx и asyncio импортированы ---
from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Dict, Any # Добавляем Dict, Any
from pydantic import ValidationError
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession
import httpx # Убедимся, что импортирован
import asyncio # Убедимся, что импортирован
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
from ..redis_client import get_redis
from ..db import get_db
from ..models import AgentConfigInput, AgentConfigOutput, AgentStatus, AgentListItem, IntegrationStatus, IntegrationType, AgentConfigStructure, UserOutput
from .. import crud
from ..import process_manager
import json
# --- ИЗМЕНЕНИЕ: Убираем asyncio, т.к. он уже импортирован выше ---
# import asyncio
# --- КОНЕЦ ИЗМЕНЕНИЯ ---

# --- ИЗМЕНЕНИЕ: Добавляем импорты для чатов ---
from ..models import ChatMessageOutput, ChatListItemOutput
# --- КОНЕЦ ИЗМЕНЕНИЯ ---

logger = logging.getLogger(__name__)
router = APIRouter()

SOURCES_API_BASE_URL = os.getenv("SOURCES_API_BASE_URL") # Получаем URL из .env

# --- НОВОЕ: Helper функция для разрешения Knowledge Base IDs ---
async def _resolve_knowledge_base_ids(config_json: Dict[str, Any], client: httpx.AsyncClient, agent_id_for_log: str = "N/A"):
    """
    Находит инструменты knowledgeBase в конфигурации, запрашивает source IDs
    из внешнего API и заменяет knowledgeBaseIds на source IDs (строки).
    Модифицирует переданный словарь config_json.
    """
    if not SOURCES_API_BASE_URL:
        logger.warning(f"Agent {agent_id_for_log}: SOURCES_API_BASE_URL not set. Skipping knowledge base ID resolution.")
        return

    try:
        tools = config_json.get("simple", {}).get("settings", {}).get("tools", [])
        for tool in tools:
            if tool.get("type") == "knowledgeBase":
                settings = tool.get("settings", {})
                knowledge_base_ids = settings.get("knowledgeBaseIds")
                if knowledge_base_ids and isinstance(knowledge_base_ids, list):
                    logger.info(f"Agent {agent_id_for_log}: Found knowledgeBase tool with IDs: {knowledge_base_ids}. Fetching source IDs...")
                    source_ids_raw = []
                    fetch_tasks = []

                    for kb_id in knowledge_base_ids:
                        api_url = f"{SOURCES_API_BASE_URL}/admin-sources/?datastoreId={kb_id}&status=sync"
                        fetch_tasks.append(client.get(api_url))

                    responses = await asyncio.gather(*fetch_tasks, return_exceptions=True)

                    for i, response in enumerate(responses):
                        kb_id = knowledge_base_ids[i]
                        if isinstance(response, httpx.Response):
                            if response.status_code == 200:
                                try:
                                    sources_data = response.json()
                                    if isinstance(sources_data, list):
                                        found_ids = [source.get("id") for source in sources_data if source.get("id") is not None]
                                        source_ids_raw.extend(found_ids)
                                        logger.debug(f"Agent {agent_id_for_log}: Fetched {len(found_ids)} source IDs (raw) for KB ID {kb_id}.")
                                    else:
                                        logger.warning(f"Agent {agent_id_for_log}: Unexpected response format from sources API for KB ID {kb_id}. Expected list, got {type(sources_data)}.")
                                except json.JSONDecodeError:
                                    logger.error(f"Agent {agent_id_for_log}: Failed to decode JSON response from sources API for KB ID {kb_id}.")
                            else:
                                logger.error(f"Agent {agent_id_for_log}: Error fetching sources for KB ID {kb_id}. Status: {response.status_code}, Response: {response.text[:200]}")
                        elif isinstance(response, Exception):
                            logger.error(f"Agent {agent_id_for_log}: Exception fetching sources for KB ID {kb_id}: {response}")

                    source_ids_str = [str(sid) for sid in source_ids_raw]

                    if source_ids_str:
                        logger.info(f"Agent {agent_id_for_log}: Replacing knowledgeBaseIds {knowledge_base_ids} with source IDs (as strings) {source_ids_str}")
                        settings["knowledgeBaseIds"] = source_ids_str
                    else:
                        logger.warning(f"Agent {agent_id_for_log}: No source IDs found for knowledgeBaseIds {knowledge_base_ids}. Keeping original IDs or potentially empty list.")
                        # settings["knowledgeBaseIds"] = [] # Раскомментируйте, если нужно очищать список при неудаче

    except Exception as e:
        logger.error(f"Agent {agent_id_for_log}: Error processing tools to resolve source IDs: {e}", exc_info=True)
        # Не прерываем выполнение, но логируем ошибку. Конфигурация может быть частично изменена.
# --- КОНЕЦ НОВОГО ---


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
    Creates a new agent configuration. Resolves knowledgeBaseIds before saving.
    Automatically starts the agent and its integrations.
    """
    # TODO: Validate userId against authenticated user
    agent_id = f"agent_{agent_config.name.lower().replace(' ', '_')}_{os.urandom(4).hex()}"

    # --- ИЗМЕНЕНИЕ: Вызов _resolve_knowledge_base_ids ---
    async with httpx.AsyncClient(timeout=10.0) as client:
        await _resolve_knowledge_base_ids(agent_config.config_json, client, agent_id_for_log=agent_id)
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    try:
        # Use DB CRUD с потенциально измененным agent_config.config_json
        db_agent = await crud.db_create_agent_config(db, agent_config, agent_id)

        # ... (остальная логика запуска процессов без изменений) ...
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
            # --- ИЗМЕНЕНИЕ: Используем owner_id ---
            ownerId=db_agent.owner_id,
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---
            config=config_structure,
            created_at=db_agent.created_at,
            updated_at=db_agent.updated_at
        )
    except Exception as e:
        logger.error(f"Failed to create agent config for {agent_id}: {e}", exc_info=True)
        # Check if it's a DB integrity error (e.g., duplicate ID, though unlikely with hex)
        # Or just raise a generic 500
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to save agent configuration.")


# ... (list_agents без изменений) ...
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
    Updates an existing agent's configuration. Resolves knowledgeBaseIds before saving.
    Signals running agent process to restart internally via Redis Pub/Sub.
    """
    # Проверяем статус агента *перед* обновлением
    initial_status_info = await process_manager.get_agent_status(agent_id, r)
    is_running = initial_status_info.status in ["running", "starting", "initializing"] # Считаем эти статусы "запущенными"

    # --- ИЗМЕНЕНИЕ: Вызов _resolve_knowledge_base_ids ---
    async with httpx.AsyncClient(timeout=10.0) as client:
        await _resolve_knowledge_base_ids(agent_config.config_json, client, agent_id_for_log=agent_id)
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    try:
        # Используем DB CRUD для обновления с потенциально измененным agent_config.config_json
        updated_db_agent = await crud.db_update_agent_config(db, agent_id, agent_config)

        if updated_db_agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

        # ... (остальная логика перезапуска без изменений) ...
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
            # --- ИЗМЕНЕНИЕ: Используем owner_id ---
            ownerId=updated_db_agent.owner_id,
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---
            config=config_structure,
            created_at=updated_db_agent.created_at,
            updated_at=updated_db_agent.updated_at # SQLAlchemy onupdate должен обновить это
        )
    except HTTPException as http_exc:
        raise http_exc # Перевыбрасываем HTTP исключения (например, 404)
    except Exception as e:
        logger.error(f"Failed to update agent config for {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update agent configuration.")


# ... (delete_agent без изменений) ...
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
    # --- ИЗМЕНЕНИЕ: Убираем зависимость от Redis, она больше не нужна ---
    # r: redis.Redis = Depends(get_redis),
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    db: AsyncSession = Depends(get_db) # Add DB session
):
    """
    Internal endpoint for the agent runner process to fetch its configuration.
    Returns the configuration dictionary stored in the database.
    """
    # Use DB CRUD
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if db_agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    # --- ИЗМЕНЕНИЕ: Удаляем блок разрешения knowledgeBaseIds ---
    # --- Начало удаления ---
    # config_json_mutable = db_agent.config_json.copy() # Создаем изменяемую копию
    #
    # if not SOURCES_API_BASE_URL:
    #     logger.warning("SOURCES_API_BASE_URL environment variable is not set. Cannot fetch source IDs for knowledge bases.")
    # else:
    #     try:
    #         # ... (вся логика получения source_ids удалена) ...
    #     except Exception as e:
    #         logger.error(f"Agent {agent_id}: Error processing tools to fetch source IDs: {e}", exc_info=True)
    # --- Конец удаления ---
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---


    # Return the raw JSON/Dict stored in the DB
    # The runner currently expects the structure from AgentConfigOutput.model_dump()
    # Let's mimic that for now, using the potentially modified config_json_mutable.
    try:
        # --- ИЗМЕНЕНИЕ: Используем исходную конфигурацию из БД ---
        config_structure = AgentConfigStructure.model_validate(db_agent.config_json)
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        output_model = AgentConfigOutput(
            id=db_agent.id,
            name=db_agent.name,
            description=db_agent.description,
            # --- ИЗМЕНЕНИЕ: Используем owner_id ---
            ownerId=db_agent.owner_id,
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---
            config=config_structure,
            created_at=db_agent.created_at,
            updated_at=db_agent.updated_at
        )
    # --- ИЗМЕНЕНИЕ: Возвращаем блок except для обработки ошибок валидации ---
    except ValidationError as validation_err:
         logger.error(f"Agent {agent_id}: Error validating config structure from DB: {validation_err}", exc_info=True)
         # Если валидация не удалась, это проблема данных в БД
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Invalid configuration structure found in database for agent {agent_id}.")
    except Exception as e:
        # Ловим другие возможные ошибки при создании output_model
        logger.error(f"Agent {agent_id}: Unexpected error creating output model from DB data: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Internal error processing configuration for agent {agent_id}.")
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---


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

# ... (остальные эндпоинты start, stop, restart, status, integrations, chats, users без изменений) ...
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
        # --- ИЗМЕНЕНИЕ: Передаем настройки интеграции из БД ---
        config_data = db_agent.config_json or {}
        simple_config = config_data.get("simple", {})
        settings_data = simple_config.get("settings", {})
        integrations_config = settings_data.get("integrations", [])
        integration_settings = {}
        for integration in integrations_config:
            if integration.get("type") == integration_type.value:
                integration_settings = integration.get("settings", {})
                break
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        success = await process_manager.start_integration_process(
            agent_id,
            integration_type,
            r,
            integration_settings=integration_settings # Передаем настройки
        )
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
        # --- ИЗМЕНЕНИЕ: Передаем настройки интеграции из БД ---
        config_data = db_agent.config_json or {}
        simple_config = config_data.get("simple", {})
        settings_data = simple_config.get("settings", {})
        integrations_config = settings_data.get("integrations", [])
        integration_settings = {}
        for integration in integrations_config:
            if integration.get("type") == integration_type.value:
                integration_settings = integration.get("settings", {})
                break
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        success = await process_manager.restart_integration_process(
            agent_id,
            integration_type,
            r,
            integration_settings=integration_settings # Передаем настройки
        )
        if not success:
            status_info = await process_manager.get_integration_status(agent_id, integration_type, r)
            detail = f"{integration_type.value} integration restart initiated, but failed during stop or start (current status: {status_info.status}). Check logs."
            logger.error(f"Restart integration {integration_type.value} for {agent_id} reported failure. Status: {status_info.status}")
            return {"message": detail}
    except Exception as e:
        logger.error(f"Unexpected error during integration restart for {agent_id}/{integration_type.value}: {e}", exc_info=True)
        return {"message": f"{integration_type.value} integration restart initiated, but an unexpected error occurred: {e}"}

    return {"message": f"{integration_type.value} integration restart initiated"}

# --- НОВОЕ: Эндпоинты для истории чатов ---

@router.get(
    "/{agent_id}/chats",
    response_model=List[ChatListItemOutput],
    summary="List chat threads for an agent",
    tags=["Chats"] # Добавляем тег Chats
)
async def list_agent_chats(
    agent_id: str,
    skip: int = Query(0, ge=0, description="Number of threads to skip"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of threads to return"),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves a list of unique chat threads for the specified agent,
    ordered by the timestamp of the last message in descending order.
    Includes details of the last message for each thread.
    """
    # Проверяем, существует ли агент
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        chats = await crud.db_get_agent_chats(db, agent_id, skip=skip, limit=limit)
        return chats
    except Exception as e:
        logger.error(f"Error fetching chat list for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve chat list.")


@router.get(
    "/{agent_id}/chats/{thread_id}",
    response_model=List[ChatMessageOutput],
    summary="Get chat history for a specific thread",
    tags=["Chats"] # Добавляем тег Chats
)
async def get_chat_history(
    agent_id: str,
    thread_id: str,
    skip: int = Query(0, ge=0, description="Number of messages to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of messages to return"),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves the chat message history for a specific agent and thread ID,
    ordered by timestamp in ascending order.
    """
    # Проверяем, существует ли агент (опционально, но полезно)
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        history = await crud.db_get_chat_history(db, agent_id, thread_id, skip=skip, limit=limit)
        # Проверяем, есть ли история для данного thread_id. Если нет, можно вернуть 404 или пустой список.
        # FastAPI автоматически вернет пустой список, если history пуст, что обычно является ожидаемым поведением.
        # if not history and skip == 0: # Проверка, что история вообще существует для этого треда
        #     raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat thread not found or is empty")
        return history
    except Exception as e:
        logger.error(f"Error fetching chat history for agent {agent_id}, thread {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve chat history.")

# --- НОВОЕ: Эндпоинт для удаления треда ---
@router.delete(
    "/{agent_id}/chats/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a chat thread",
    tags=["Chats"] # Добавляем тег Chats
)
async def delete_chat_thread(
    agent_id: str,
    thread_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Deletes all messages associated with a specific agent and thread ID.
    Returns 204 No Content on successful deletion, even if the thread didn't exist.
    Returns 404 if the agent itself does not exist.
    """
    # 1. Проверяем, существует ли агент
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    # 2. Вызываем CRUD-функцию для удаления
    try:
        await crud.db_delete_chat_thread(db, agent_id, thread_id)
        # Не возвращаем тело ответа при статусе 204
        return None
    except Exception as e:
        # Логирование ошибки происходит внутри CRUD функции
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete chat thread.")

# --- КОНЕЦ НОВОГО ---

# --- НОВОЕ: Эндпоинт для получения пользователей агента ---
@router.get(
    "/{agent_id}/users",
    response_model=List[UserOutput],
    summary="List users associated with an agent",
    tags=["Agents", "Users"] # Добавляем тег Users
)
async def list_agent_users(
    agent_id: str,
    skip: int = Query(0, ge=0, description="Number of users to skip"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of users to return"),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves a list of users who have interacted with or are authorized for
    the specified agent.
    """
    # Проверяем, существует ли агент
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        users = await crud.db_get_users_for_agent(db, agent_id, skip=skip, limit=limit)
        # FastAPI автоматически преобразует список UserDB в список UserOutput
        return users
    except Exception as e:
        # Логирование происходит в CRUD
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve user list for agent.")
# --- КОНЕЦ НОВОГО ---

