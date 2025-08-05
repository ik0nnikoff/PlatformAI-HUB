import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.db.crud import agent_crud
from app.services.process_management import ProcessManager
from app.api.schemas.common_schemas import IntegrationType
from app.api.schemas.agent_schemas import IntegrationStatus

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/agents/{agent_id}/integrations",
    tags=["Integrations"]
)

async def get_process_manager() -> ProcessManager:
    pm = ProcessManager()
    return pm

@router.get("/{integration_type}/status", response_model=IntegrationStatus)
async def get_integration_status_api(
    agent_id: str,
    integration_type: IntegrationType,
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db)
):
    try:
        await pm.setup_manager()
        db_agent = await agent_crud.db_get_agent_config(db, agent_id)
        if not db_agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")
        
        status_info_dict = await pm.get_integration_status(agent_id, integration_type.value)
        
        status_obj = IntegrationStatus(
            agent_id=status_info_dict.get("agent_id", agent_id),
            type=IntegrationType(status_info_dict.get("type", integration_type.value)),
            status=status_info_dict.get("status", "unknown"),
            pid=status_info_dict.get("pid"),
            last_active=status_info_dict.get("last_active"),
            runtime=status_info_dict.get("runtime"),
            error_detail=status_info_dict.get("error_detail")
        )

        if status_obj.status == "not_found":
            pass

        return status_obj
    finally:
        await pm.cleanup_manager()

@router.post("/{integration_type}/start", status_code=status.HTTP_202_ACCEPTED)
async def start_integration_api(
    agent_id: str,
    integration_type: IntegrationType,
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db)
):
    try:
        await pm.setup_manager()
        db_agent = await agent_crud.db_get_agent_config(db, agent_id)
        if not db_agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

        config_data = db_agent.config_json or {}
        simple_config = config_data.get("simple", {})
        settings_data = simple_config.get("settings", {})
        integrations_config = settings_data.get("integrations", [])
        integration_settings_dict = None
        for integration in integrations_config:
            if integration.get("type") == integration_type.value:
                integration_settings_dict = integration.get("settings", {})
                break
        
        success = await pm.start_integration_process(agent_id, integration_type.value, integration_settings_dict)
        
        if not success:
            status_info = await pm.get_integration_status(agent_id, integration_type.value)
            detail = f"Failed to start {integration_type.value} integration (status: {status_info.get('status', 'unknown')})."
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"{integration_type.value} integration script not found.")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"{integration_type.value} integration process failed to launch. Error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error starting integration {integration_type.value} for {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred during integration start.")
    finally:
        await pm.cleanup_manager()
    return {"message": f"{integration_type.value} integration start initiated"}

@router.post("/{integration_type}/stop", status_code=status.HTTP_202_ACCEPTED)
async def stop_integration_api(
    agent_id: str,
    integration_type: IntegrationType,
    force: bool = Query(False, description="Force stop using SIGKILL if SIGTERM fails."),
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db)
):
    try:
        await pm.setup_manager()
        db_agent = await agent_crud.db_get_agent_config(db, agent_id)
        if not db_agent:
            status_key_to_delete = pm.integration_status_key_template.format(agent_id, integration_type.value)
            await pm._delete_status_key_from_redis(status_key_to_delete)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

        success = await pm.stop_integration_process(agent_id, integration_type.value, force=force)
        if not success:
            status_info = await pm.get_integration_status(agent_id, integration_type.value)
            detail = f"{integration_type.value} integration stop initiated, but encountered an error or did not terminate gracefully (status: {status_info.get('status', 'unknown')})."
            logger.error(f"Stop integration {integration_type.value} for {agent_id} reported failure. Status: {status_info.get('status', 'unknown')}")
            return {"message": detail}
    except Exception as e:
        logger.error(f"Unexpected error stopping integration {integration_type.value} for {agent_id}: {e}", exc_info=True)
        return {"message": f"{integration_type.value} integration stop initiated, but an unexpected error occurred: {e}"}
    finally:
        await pm.cleanup_manager()
    return {"message": f"{integration_type.value} integration stop initiated"}

@router.post("/{integration_type}/restart", status_code=status.HTTP_202_ACCEPTED)
async def restart_integration_api(
    agent_id: str,
    integration_type: IntegrationType,
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db)
):
    try:
        await pm.setup_manager()
        db_agent = await agent_crud.db_get_agent_config(db, agent_id)
        if not db_agent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

        config_data = db_agent.config_json or {}
        simple_config = config_data.get("simple", {})
        settings_data = simple_config.get("settings", {})
        integrations_config = settings_data.get("integrations", [])
        integration_settings_dict = None
        for integration in integrations_config:
            if integration.get("type") == integration_type.value:
                integration_settings_dict = integration.get("settings", {})
                break
        
        success = await pm.restart_integration_process(agent_id, integration_type.value, integration_settings_dict)
        if not success:
            status_info = await pm.get_integration_status(agent_id, integration_type.value)
            detail = f"{integration_type.value} integration restart initiated, but failed during stop or start (current status: {status_info.get('status', 'unknown')}). Check logs."
            logger.error(f"Restart integration {integration_type.value} for {agent_id} reported failure. Status: {status_info.get('status', 'unknown')}")
            return {"message": detail}
    except Exception as e:
        logger.error(f"Unexpected error during integration restart for {agent_id}/{integration_type.value}: {e}", exc_info=True)
        return {"message": f"{integration_type.value} integration restart initiated, but an unexpected error occurred: {e}"}
    finally:
        await pm.cleanup_manager()
    return {"message": f"{integration_type.value} integration restart initiated"}
