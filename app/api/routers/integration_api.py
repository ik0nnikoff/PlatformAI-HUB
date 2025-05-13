import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_redis_client
from app.db.crud import agent_crud
from app.services import process_manager_service
from app.api.schemas.common_schemas import IntegrationType
from app.api.schemas.agent_schemas import IntegrationStatus # Предполагается, что IntegrationStatus здесь

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/agents/{agent_id}/integrations", # Общий префикс для этих маршрутов
    tags=["Integrations"] # Новый тег для Swagger UI
)

@router.get("/{integration_type}/status", response_model=IntegrationStatus)
async def get_integration_status_api(
    agent_id: str,
    integration_type: IntegrationType,
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db)
):
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")
    return await process_manager_service.get_integration_status(agent_id, integration_type, r)

@router.post("/{integration_type}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_integration_api(
    agent_id: str,
    integration_type: IntegrationType,
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db)
):
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    config_data = db_agent.config_json or {}
    simple_config = config_data.get("simple", {})
    settings_data = simple_config.get("settings", {})
    integrations_config = settings_data.get("integrations", [])
    integration_settings = {}
    for integration in integrations_config:
        if integration.get("type") == integration_type.value:
            integration_settings = integration.get("settings", {})
            break
    try:
        success = await process_manager_service.start_integration_process(
            agent_id,
            integration_type,
            r,
            integration_settings=integration_settings
        )
        if not success:
             status_info = await process_manager_service.get_integration_status(agent_id, integration_type, r)
             detail = f"Failed to start {integration_type.value} integration (status: {status_info.status})."
             raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
    except FileNotFoundError:
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"{integration_type.value} integration script not found.")
    except ValueError as e:
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"{integration_type.value} integration process failed to launch.")
    except Exception as e:
         logger.error(f"Unexpected error starting integration {integration_type.value} for {agent_id}: {e}", exc_info=True)
         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred during integration start.")
    return {"message": f"{integration_type.value} integration start initiated"}

@router.post("/{integration_type}/stop", status_code=status.HTTP_202_ACCEPTED)
async def stop_integration_api(
    agent_id: str,
    integration_type: IntegrationType,
    force: bool = Query(False, description="Force stop using SIGKILL instead of SIGTERM"),
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db)
):
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        status_key = process_manager_service._get_integration_status_key(agent_id, integration_type) # type: ignore
        await r.delete(status_key)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        success = await process_manager_service.stop_integration_process(agent_id, integration_type, r, force=force)
        if not success:
            status_info = await process_manager_service.get_integration_status(agent_id, integration_type, r)
            detail = f"{integration_type.value} integration stop initiated, but encountered an error or did not terminate gracefully (status: {status_info.status})."
            logger.error(f"Stop integration {integration_type.value} for {agent_id} reported failure. Status: {status_info.status}")
            return {"message": detail}
    except Exception as e:
         logger.error(f"Unexpected error stopping integration {integration_type.value} for {agent_id}: {e}", exc_info=True)
         return {"message": f"{integration_type.value} integration stop initiated, but an unexpected error occurred: {e}"}
    return {"message": f"{integration_type.value} integration stop initiated"}

@router.post("/{integration_type}/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart_integration_api(
    agent_id: str,
    integration_type: IntegrationType,
    r: redis.Redis = Depends(get_redis_client),
    db: AsyncSession = Depends(get_db)
):
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    config_data = db_agent.config_json or {}
    simple_config = config_data.get("simple", {})
    settings_data = simple_config.get("settings", {})
    integrations_config = settings_data.get("integrations", [])
    integration_settings = {}
    for integration in integrations_config:
        if integration.get("type") == integration_type.value:
            integration_settings = integration.get("settings", {})
            break
    try:
        success = await process_manager_service.restart_integration_process(
            agent_id,
            integration_type,
            r,
            integration_settings=integration_settings
        )
        if not success:
            status_info = await process_manager_service.get_integration_status(agent_id, integration_type, r)
            detail = f"{integration_type.value} integration restart initiated, but failed during stop or start (current status: {status_info.status}). Check logs."
            logger.error(f"Restart integration {integration_type.value} for {agent_id} reported failure. Status: {status_info.status}")
            return {"message": detail}
    except Exception as e:
        logger.error(f"Unexpected error during integration restart for {agent_id}/{integration_type.value}: {e}", exc_info=True)
        return {"message": f"{integration_type.value} integration restart initiated, but an unexpected error occurred: {e}"}
    return {"message": f"{integration_type.value} integration restart initiated"}
