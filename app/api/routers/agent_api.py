import logging
import os
from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
import httpx
import asyncio
import json

from app.core.dependencies import get_db
from app.api.schemas.agent_schemas import AgentConfigInput, AgentConfigOutput, AgentStatus, AgentListItem, AgentConfigStructure
from app.db.crud import agent_crud, user_crud
from app.services.process_manager import ProcessManager
from app.api.schemas.user_schemas import UserOutput
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Agents"])

# --- ProcessManager Dependency ---
async def get_process_manager() -> ProcessManager:
    pm = ProcessManager()
    return pm

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
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db)
):
    agent_id = f"agent_{agent_config.name.lower().replace(' ', '_')}_{os.urandom(4).hex()}"

    # Разрешение knowledgeBaseIds перед сохранением
    async with httpx.AsyncClient(timeout=10.0) as client:
        await _resolve_knowledge_base_ids(agent_config.config_json, client, agent_id_for_log=agent_id)

    try:
        db_agent = await agent_crud.db_create_agent_config(db, agent_config, agent_id)
        
        # Initialize ProcessManager's Redis connection before use
        await pm.setup_manager()

        logger.info(f"Agent {agent_id} created. Attempting to start process...")
        try:
            # Используем ProcessManager для запуска
            await pm.start_agent_process(agent_id)
            logger.info(f"Successfully initiated start for agent process {agent_id}.")
        except Exception as start_err:
            logger.error(f"Failed to auto-start agent process {agent_id} after creation: {start_err}", exc_info=True)
            # Статус в Redis должен отражать ошибку, если start_agent_process ее установил
        finally:
            await pm.cleanup_manager() # Clean up PM's Redis connection

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
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db)
):
    db_agents = await agent_crud.db_get_all_agents(db, skip=skip, limit=limit)
    agents_list = []
    try:
        await pm.setup_manager()
        for db_agent in db_agents:
            # Используем ProcessManager для получения статуса
            status_info = await pm.get_agent_status(db_agent.id)
            agents_list.append(AgentListItem(
                id=db_agent.id,
                name=db_agent.name,
                description=db_agent.description,
                status=status_info.get("status", "unknown") # get status from dict
            ))
    finally:
        await pm.cleanup_manager()
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
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db)
):
    try:
        await pm.setup_manager()
        # Проверяем статус агента *перед* обновлением
        initial_status_info = await pm.get_agent_status(agent_id)
        is_running = initial_status_info.get("status") in ["running", "starting", "initializing"]

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
                    # Используем ProcessManager для перезапуска
                    await pm.restart_agent_process(agent_id)
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
    finally:
        await pm.cleanup_manager() # Ensure cleanup

@router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an agent"
)
async def delete_agent(
    agent_id: str,
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db)
):
    try:
        await pm.setup_manager()
        db_agent_exists = await agent_crud.db_get_agent_config(db, agent_id)
        if not db_agent_exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

        try:
            # Используем ProcessManager для остановки
            await pm.stop_agent_process(agent_id, force=True) # Принудительная остановка при удалении
            logger.info(f"Stop command issued for agent {agent_id} before deletion.")
        except Exception as stop_e:
            logger.error(f"Error issuing stop command for agent {agent_id} during deletion: {stop_e}", exc_info=True)
            # Продолжаем удаление, даже если остановка не удалась

        try:
            deleted_db = await agent_crud.db_delete_agent_config(db, agent_id)
            if not deleted_db:
                logger.warning(f"Agent {agent_id} existed but db_delete_agent_config returned False.")
        except Exception as db_del_e:
            logger.error(f"Error deleting agent {agent_id} from DB: {db_del_e}", exc_info=True)

        # Очистка статуса в Redis
        status_key_to_delete = pm.agent_status_key_template.format(agent_id)
        await pm._delete_status_key_from_redis(status_key_to_delete) # Using internal for now
        logger.info(f"Explicitly deleted Redis status key for agent {agent_id}")

        # Также нужно удалить все связанные авторизации пользователей
        try:
            await agent_crud.db_delete_all_authorizations_for_agent(db, agent_id)
            logger.info(f"Deleted all user authorizations for agent {agent_id}.")
        except Exception as auth_del_e:
            logger.error(f"Error deleting user authorizations for agent {agent_id}: {auth_del_e}", exc_info=True)
    finally:
        await pm.cleanup_manager()

    return None # Для статуса 204 тело ответа должно быть пустым

@router.post(
    "/{agent_id}/start",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an agent process"
)
async def start_agent_api(
    agent_id: str,
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db)
):
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        await pm.setup_manager()
        # Используем ProcessManager
        success = await pm.start_agent_process(agent_id)
        if not success:
            status_info = await pm.get_agent_status(agent_id)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Agent start initiated but failed (status: {status_info.get('status', 'unknown')}). Check logs.")
    except FileNotFoundError:
        logger.error(f"Agent start failed for {agent_id}: Runner script not found.", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Agent runner script not found.")
    except ValueError as e: 
        logger.error(f"Agent start failed for {agent_id} due to value error: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e: 
        logger.error(f"Agent start failed for {agent_id} due to runtime error: {e}", exc_info=True)
        status_info = await pm.get_agent_status(agent_id)
        detail = f"Agent process failed to launch (status: {status_info.get('status', 'unknown')}). Check logs."
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
    except Exception as e:
        logger.error(f"Unexpected error starting agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred during agent start. Check logs.")
    finally:
        await pm.cleanup_manager() # Ensure cleanup

    return {"message": "Agent start initiated"}

@router.post(
    "/{agent_id}/stop",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Stop an agent process"
)
async def stop_agent_api(
    agent_id: str,
    force: bool = Query(False, description="Force stop using SIGKILL if SIGTERM fails."),
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db) 
):
    try:
        await pm.setup_manager()
        db_agent = await agent_crud.db_get_agent_config(db, agent_id)
        if not db_agent:
            status_key_to_delete = pm.agent_status_key_template.format(agent_id)
            await pm._delete_status_key_from_redis(status_key_to_delete) # Using internal for now
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

        try:
            # Используем ProcessManager
            success = await pm.stop_agent_process(agent_id, force=force)
            if not success:
                status_info = await pm.get_agent_status(agent_id)
                detail = f"Agent stop initiated, but encountered an error or agent did not terminate as expected (status: {status_info.get('status', 'unknown')}). Check logs."
                logger.error(f"Stop agent {agent_id} reported failure. Status: {status_info.get('status', 'unknown')}")
                return {"message": detail} # Return 202 with details
        except Exception as e:
            logger.error(f"Unexpected error stopping agent {agent_id}: {e}", exc_info=True)
            return {"message": f"Agent stop initiated, but an unexpected error occurred: {e}. Check logs."} # Return 202 with details
    finally:
        await pm.cleanup_manager() # Ensure cleanup
    
    return {"message": "Agent stop initiated"}

@router.post(
    "/{agent_id}/restart",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Restart an agent process"
)
async def restart_agent_api(
    agent_id: str,
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db) 
):
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        await pm.setup_manager()
        # Используем ProcessManager
        success = await pm.restart_agent_process(agent_id)
        if not success:
            status_info = await pm.get_agent_status(agent_id)
            detail = f"Agent restart initiated, but failed during stop or start (current status: {status_info.get('status', 'unknown')}). Check logs for details."
            logger.error(f"Restart agent {agent_id} reported failure. Status: {status_info.get('status', 'unknown')}")
            return {"message": detail} 
    except Exception as e:
        logger.error(f"Unexpected error during agent restart for {agent_id}: {e}", exc_info=True)
        return {"message": f"Agent restart initiated, but an unexpected error occurred: {e}. Check logs."} 
    finally:
        await pm.cleanup_manager()

    return {"message": "Agent restart initiated"}

@router.get(
    "/{agent_id}/status",
    response_model=AgentStatus,
    summary="Get agent status"
)
async def get_agent_status_api(
    agent_id: str,
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db)
):
    try:
        await pm.setup_manager()
        # Используем ProcessManager
        status_info_dict = await pm.get_agent_status(agent_id) # Returns a dict

        # Convert dict to AgentStatus Pydantic model
        status_obj = AgentStatus(
            agent_id=status_info_dict.get("agent_id", agent_id),
            status=status_info_dict.get("status", "unknown"),
            pid=status_info_dict.get("pid"),
            last_active=status_info_dict.get("last_active"),
            runtime=status_info_dict.get("runtime"),
            container_name=status_info_dict.get("container_name"),
            error_detail=status_info_dict.get("error_detail")
        )

        if status_obj.status == "not_found":
            db_agent = await agent_crud.db_get_agent_config(db, agent_id)
            if db_agent:
                return AgentStatus(agent_id=agent_id, status="stopped") 
            else:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
        return status_obj
    finally:
        await pm.cleanup_manager()

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

