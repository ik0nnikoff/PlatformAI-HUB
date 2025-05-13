import logging
import os
from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Dict, Any, Optional
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
import asyncio
import json

from app.core.dependencies import get_db, get_redis_client
from app.api.schemas.agent_schemas import AgentConfigInput, AgentConfigOutput, AgentStatus, AgentListItem, AgentConfigStructure # IntegrationStatus убран
from app.db.crud import agent_crud, user_crud # user_crud может понадобиться для ownerId
from app.services import process_manager_service # Будет создан позже
# IntegrationType и эндпоинты интеграций удалены
from app.api.schemas.user_schemas import UserOutput # <--- Добавлено
from app.core.config import settings # Для доступа к SOURCES_API_BASE_URL

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Agents"]) # Добавляем префикс и теги на уровне роутера

# SOURCES_API_BASE_URL теперь берется из settings
# --- Helper функция для разрешения Knowledge Base IDs (взята из старой версии и адаптирована) ---
async def _resolve_knowledge_base_ids(config_json: Dict[str, Any], client: httpx.AsyncClient, agent_id_for_log: str = "N/A"):
    """
    Находит инструменты knowledgeBase в конфигурации, запрашивает source IDs
    из внешнего API и заменяет knowledgeBaseIds на source IDs (строки).
    Модифицирует переданный словарь config_json.
    """
    if not settings.SOURCES_API_BASE_URL:
        logger.warning(f"Agent {agent_id_for_log}: SOURCES_API_BASE_URL not set. Skipping knowledge base ID resolution.")
        return

    try:
        tools = config_json.get("simple", {}).get("settings", {}).get("tools", [])
        for tool in tools:
            if tool.get("type") == "knowledgeBase":
                kb_settings = tool.get("settings", {})
                knowledge_base_ids = kb_settings.get("knowledgeBaseIds")
                if knowledge_base_ids and isinstance(knowledge_base_ids, list):
                    logger.info(f"Agent {agent_id_for_log}: Found knowledgeBase tool with IDs: {knowledge_base_ids}. Fetching source IDs...")
                    source_ids_raw = []
                    fetch_tasks = []

                    for kb_id in knowledge_base_ids:
                        api_url = f"{settings.SOURCES_API_BASE_URL}/admin-sources/?datastoreId={kb_id}&status=sync"
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
                        kb_settings["knowledgeBaseIds"] = source_ids_str
                    else:
                        logger.warning(f"Agent {agent_id_for_log}: No source IDs found for knowledgeBaseIds {knowledge_base_ids}. Keeping original IDs or potentially empty list.")
                        # kb_settings["knowledgeBaseIds"] = [] # Раскомментируйте, если нужно очищать список при неудаче

    except Exception as e:
        logger.error(f"Agent {agent_id_for_log}: Error processing tools to resolve source IDs: {e}", exc_info=True)

@router.post(
    "/",
    response_model=AgentConfigOutput,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new agent configuration"
    # Тег "Agents" уже указан в APIRouter
)
async def create_agent(
    agent_config: AgentConfigInput,
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db)
):
    agent_id = f"agent_{agent_config.name.lower().replace(' ', '_')}_{os.urandom(4).hex()}"

    # Разрешение knowledgeBaseIds перед сохранением
    async with httpx.AsyncClient(timeout=10.0) as client:
        await _resolve_knowledge_base_ids(agent_config.config_json, client, agent_id_for_log=agent_id)

    try:
        db_agent = await agent_crud.db_create_agent_config(db, agent_config, agent_id)
        status_key = f"agent_status:{agent_id}"
        await r.hset(status_key, mapping={"status": "stopped"}) # Изначальный статус - остановлен

        logger.info(f"Agent {agent_id} created. Attempting to start process...")
        try:
            # Используем process_manager_service для запуска
            await process_manager_service.start_agent_process(agent_id, r)
            logger.info(f"Successfully initiated start for agent process {agent_id}.")
        except Exception as start_err:
            logger.error(f"Failed to auto-start agent process {agent_id} after creation: {start_err}", exc_info=True)
            # Статус в Redis должен отражать ошибку, если start_agent_process ее установил

        # Логика авто-запуска интеграций удалена, предполагается, что это управляется иначе

        config_structure = AgentConfigStructure.model_validate(db_agent.config_json)
        return AgentConfigOutput(
            id=db_agent.id,
            name=db_agent.name,
            description=db_agent.description,
            ownerId=db_agent.owner_id,
            config=config_structure,
            created_at=db_agent.created_at,
            updated_at=db_agent.updated_at
        )
    except Exception as e:
        logger.error(f"Failed to create agent config for {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to save agent configuration.")

@router.get(
    "/",
    response_model=List[AgentListItem],
    summary="List all agents"
)
async def list_agents(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db)
):
    db_agents = await agent_crud.db_get_all_agents(db, skip=skip, limit=limit)
    agents_list = []
    for db_agent in db_agents:
        # Используем process_manager_service для получения статуса
        status_info = await process_manager_service.get_agent_status(db_agent.id, r)
        agents_list.append(AgentListItem(
            id=db_agent.id,
            name=db_agent.name,
            description=db_agent.description,
            status=status_info.status
        ))
    return agents_list

@router.get(
    "/{agent_id}/config",
    response_model=AgentConfigOutput,
    summary="Get raw agent configuration for runner",
)
async def get_agent_config_for_runner(
    agent_id: str,
    db: AsyncSession = Depends(get_db)
):
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if db_agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")
    
    # Разрешение knowledgeBaseIds здесь не требуется, т.к. runner должен получать "сырую" конфигурацию,
    # а разрешение должно происходить при сохранении (POST/PUT).
    # Однако, если runner ожидает уже разрешенные ID, это нужно будет пересмотреть.
    # Пока оставляем как есть, предполагая, что runner работает с ID, которые были сохранены.

    try:
        config_structure = AgentConfigStructure.model_validate(db_agent.config_json)
        return AgentConfigOutput(
            id=db_agent.id,
            name=db_agent.name,
            description=db_agent.description,
            ownerId=db_agent.owner_id,
            config=config_structure,
            created_at=db_agent.created_at,
            updated_at=db_agent.updated_at
        )
    except Exception as e:
        logger.error(f"Agent {agent_id}: Error validating/processing config structure from DB: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Invalid or problematic configuration structure found in database for agent {agent_id}.")

@router.put(
    "/{agent_id}",
    response_model=AgentConfigOutput,
    summary="Update an agent configuration"
)
async def update_agent(
    agent_id: str,
    agent_config: AgentConfigInput,
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db)
):
    # Проверяем статус агента *перед* обновлением
    initial_status_info = await process_manager_service.get_agent_status(agent_id, r)
    is_running = initial_status_info.status in ["running", "starting", "initializing"]

    # Разрешение knowledgeBaseIds перед обновлением
    async with httpx.AsyncClient(timeout=10.0) as client:
        await _resolve_knowledge_base_ids(agent_config.config_json, client, agent_id_for_log=agent_id)

    try:
        updated_db_agent = await agent_crud.db_update_agent_config(db, agent_id, agent_config)
        if updated_db_agent is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

        if is_running:
            logger.info(f"Agent {agent_id} was running. Attempting to restart it after config update.")
            try:
                # Используем process_manager_service для перезапуска
                # restart_agent_process сам остановит и запустит агент
                await process_manager_service.restart_agent_process(agent_id, r)
                logger.info(f"Restart command issued for agent {agent_id} after config update.")
            except Exception as restart_e:
                logger.error(f"Failed to issue restart command for agent {agent_id} after update: {restart_e}", exc_info=True)
                # Не прерываем основной процесс обновления, но логируем ошибку

        config_structure = AgentConfigStructure.model_validate(updated_db_agent.config_json)
        return AgentConfigOutput(
            id=updated_db_agent.id,
            name=updated_db_agent.name,
            description=updated_db_agent.description,
            ownerId=updated_db_agent.owner_id,
            config=config_structure,
            created_at=updated_db_agent.created_at,
            updated_at=updated_db_agent.updated_at
        )
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"Failed to update agent config for {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update agent configuration.")

@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an agent"
)
async def delete_agent(
    agent_id: str,
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db)
):
    db_agent_exists = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent_exists:
        # Если конфига нет, но статус есть в Redis, его тоже стоит почистить
        await r.delete(f"agent_status:{agent_id}")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        # Используем process_manager_service для остановки
        await process_manager_service.stop_agent_process(agent_id, r, force=True) # Принудительная остановка при удалении
        logger.info(f"Stop command issued for agent {agent_id} before deletion.")
    except Exception as stop_e:
        logger.error(f"Error issuing stop command for agent {agent_id} during deletion: {stop_e}", exc_info=True)
        # Продолжаем удаление, даже если остановка не удалась

    try:
        deleted_db = await agent_crud.db_delete_agent_config(db, agent_id)
        if not deleted_db:
            # Эта ситуация не должна возникать, если db_agent_exists был найден
            logger.warning(f"Agent {agent_id} existed but db_delete_agent_config returned False.")
            # Тем не менее, продолжим чистку Redis
    except Exception as db_del_e:
        logger.error(f"Error deleting agent {agent_id} from DB: {db_del_e}", exc_info=True)
        # Не выбрасываем ошибку здесь, чтобы попытаться очистить Redis

    # Очистка статуса в Redis
    await r.delete(f"agent_status:{agent_id}")
    logger.info(f"Cleaned up Redis status for deleted agent {agent_id}")

    # Также нужно удалить все связанные авторизации пользователей
    try:
        await agent_crud.db_delete_all_authorizations_for_agent(db, agent_id)
        logger.info(f"Deleted all user authorizations for agent {agent_id}.")
    except Exception as auth_del_e:
        logger.error(f"Error deleting user authorizations for agent {agent_id}: {auth_del_e}", exc_info=True)

    return None # Для статуса 204 тело ответа должно быть пустым

@router.post(
    "/{agent_id}/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an agent process"
)
async def start_agent_api(
    agent_id: str,
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db)
):
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        # Используем process_manager_service
        success = await process_manager_service.start_agent_process(agent_id, r)
        if not success:
            # process_manager_service должен сам обновить статус в Redis при ошибке
            status_info = await process_manager_service.get_agent_status(agent_id, r)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Agent start initiated but failed (status: {status_info.status}). Check logs.")
    except FileNotFoundError:
        logger.error(f"Agent start failed for {agent_id}: Runner script not found.", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Agent runner script not found.")
    except ValueError as e: # Например, если process_manager_service вернет ошибку конфигурации
        logger.error(f"Agent start failed for {agent_id} due to value error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e: # Например, если процесс не удалось запустить
        logger.error(f"Agent start failed for {agent_id} due to runtime error: {e}", exc_info=True)
        status_info = await process_manager_service.get_agent_status(agent_id, r)
        detail = f"Agent process failed to launch (status: {status_info.status}). Check logs."
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
    except Exception as e:
        logger.error(f"Unexpected error starting agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred during agent start. Check logs.")

    return {"message": "Agent start initiated"}

@router.post(
    "/{agent_id}/stop",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Stop an agent process"
)
async def stop_agent_api(
    agent_id: str,
    force: bool = Query(False, description="Force stop using SIGKILL if SIGTERM fails."),
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db) # Добавлено для проверки существования агента
):
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        await r.delete(f"agent_status:{agent_id}") # Почистить Redis, если конфига нет
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        # Используем process_manager_service
        success = await process_manager_service.stop_agent_process(agent_id, r, force=force)
        if not success:
            status_info = await process_manager_service.get_agent_status(agent_id, r)
            detail = f"Agent stop initiated, but encountered an error or agent did not terminate as expected (status: {status_info.status}). Check logs."
            logger.error(f"Stop agent {agent_id} reported failure. Status: {status_info.status}")
            # Возвращаем 202, так как команда была принята, но результат не идеален
            return {"message": detail}
    except Exception as e:
        logger.error(f"Unexpected error stopping agent {agent_id}: {e}", exc_info=True)
        # Также возвращаем 202 с сообщением об ошибке
        return {"message": f"Agent stop initiated, but an unexpected error occurred: {e}. Check logs."}

    return {"message": "Agent stop initiated"}

@router.post(
    "/{agent_id}/restart",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restart an agent process"
)
async def restart_agent_api(
    agent_id: str,
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db) # Добавлено для проверки существования агента
):
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        # Используем process_manager_service
        success = await process_manager_service.restart_agent_process(agent_id, r)
        if not success:
            status_info = await process_manager_service.get_agent_status(agent_id, r)
            detail = f"Agent restart initiated, but failed during stop or start (current status: {status_info.status}). Check logs for details."
            logger.error(f"Restart agent {agent_id} reported failure. Status: {status_info.status}")
            return {"message": detail} # 202 с деталями
    except Exception as e:
        logger.error(f"Unexpected error during agent restart for {agent_id}: {e}", exc_info=True)
        return {"message": f"Agent restart initiated, but an unexpected error occurred: {e}. Check logs."} # 202 с деталями

    return {"message": "Agent restart initiated"}

@router.get(
    "/{agent_id}/status",
    response_model=AgentStatus,
    summary="Get agent status"
)
async def get_agent_status_api(
    agent_id: str,
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db)
):
    # Используем process_manager_service
    status_info = await process_manager_service.get_agent_status(agent_id, r)
    
    # Если process_manager_service говорит "not_found" (нет ключа в Redis),
    # но агент есть в БД, то это "stopped".
    # Если и в БД нет, то это настоящий 404.
    if status_info.status == "not_found":
        db_agent = await agent_crud.db_get_agent_config(db, agent_id)
        if db_agent:
            return AgentStatus(agent_id=agent_id, status="stopped") # Агент есть, но не запущен
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return status_info

@router.get(
    "/{agent_id}/users",
    response_model=List[UserOutput],
    summary="List authorized users for an agent",
)
async def list_agent_users_api(
    agent_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves a list of users who are authorized to interact with the specified agent.
    """
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    # Используем user_crud для получения авторизованных пользователей
    authorized_users_db = await user_crud.db_get_authorized_users_for_agent(db, agent_id)
    
    response_users = [UserOutput.model_validate(user_db) for user_db in authorized_users_db]
        
    return response_users

# Эндпоинты для интеграций были перенесены в app.api.routers.integration_api.py
# Убедимся, что здесь нет старых эндпоинтов, связанных с интеграциями.

