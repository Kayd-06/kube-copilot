"""HTTP API for safe actions and the audit log."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.actions.models import (
    ActionDetail,
    ActionRecord,
    ActionsConfigOut,
    ActionsListResponse,
    AuditResponse,
    DecisionBody,
    ProposeResponse,
)
from app.actions.service import ActionError, ActionService
from app.deps import get_action_service
from app.k8s_client import K8sUnavailableError, PodNotFoundError
from app.llm_explainer import LLMError

router = APIRouter(tags=["actions"])


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, ActionError):
        return HTTPException(status_code=exc.status_code, detail=str(exc))
    if isinstance(exc, PodNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, K8sUnavailableError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, LLMError):
        return HTTPException(status_code=502, detail=str(exc))
    raise exc


@router.get("/actions/config", response_model=ActionsConfigOut)
def actions_config(service: ActionService = Depends(get_action_service)) -> ActionsConfigOut:
    return service.config_view()


@router.post("/pods/{namespace}/{name}/actions/propose", response_model=ProposeResponse)
def propose_actions(
    namespace: str,
    name: str,
    body: DecisionBody | None = Body(default=None),
    service: ActionService = Depends(get_action_service),
) -> ProposeResponse:
    try:
        return service.propose(namespace, name, actor=f"human:{body.actor}" if body else "system")
    except (ActionError, PodNotFoundError, K8sUnavailableError, LLMError) as exc:
        raise _http(exc) from exc


@router.get("/actions", response_model=ActionsListResponse)
def list_actions(
    status: str | None = Query(default=None),
    namespace: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    service: ActionService = Depends(get_action_service),
) -> ActionsListResponse:
    items = service.store.list(status=status, namespace=namespace, limit=limit)
    return ActionsListResponse(actions=items, count=len(items))


@router.get("/actions/{action_id}", response_model=ActionDetail)
def get_action(action_id: str, service: ActionService = Depends(get_action_service)) -> ActionDetail:
    try:
        return service.detail(action_id)
    except ActionError as exc:
        raise _http(exc) from exc


@router.post("/actions/{action_id}/approve", response_model=ActionRecord)
def approve_action(
    action_id: str, body: DecisionBody, service: ActionService = Depends(get_action_service)
) -> ActionRecord:
    try:
        return service.approve(action_id, body.actor, body.comment)
    except (ActionError, K8sUnavailableError) as exc:
        raise _http(exc) from exc


@router.post("/actions/{action_id}/reject", response_model=ActionRecord)
def reject_action(
    action_id: str, body: DecisionBody, service: ActionService = Depends(get_action_service)
) -> ActionRecord:
    try:
        return service.reject(action_id, body.actor, body.comment)
    except ActionError as exc:
        raise _http(exc) from exc


@router.post("/actions/{action_id}/revert", response_model=ActionRecord)
def revert_action(
    action_id: str, body: DecisionBody, service: ActionService = Depends(get_action_service)
) -> ActionRecord:
    """Creates a *new* proposal that undoes the action. It still needs its own approval."""
    try:
        return service.propose_revert(action_id, body.actor)
    except (ActionError, K8sUnavailableError) as exc:
        raise _http(exc) from exc


@router.get("/audit", response_model=AuditResponse)
def audit_log(
    action_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    service: ActionService = Depends(get_action_service),
) -> AuditResponse:
    events = service.store.audit_events(action_id=action_id, limit=limit)
    return AuditResponse(events=events, count=len(events))
