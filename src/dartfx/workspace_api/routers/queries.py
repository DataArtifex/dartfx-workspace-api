"""Graph (SPARQL) and analytical tabular SQL query endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from dartfx.workspace.models import Action, Principal, SparqlResult, TabularQueryResult
from workspace_api.dependencies import (
    get_current_principal,
    get_db_session,
    map_domain_error,
    resolve_authorized_workspace,
)
from workspace_api.models import SparqlQueryRequest, TabularQueryRequest

router = APIRouter(prefix="/workspaces/{ws_id}", tags=["queries"])


@router.post("/sparql", response_model=SparqlResult, summary="Execute read-only SPARQL query on workspace KB")
def query_workspace_sparql(
    ws_id: str,
    payload: SparqlQueryRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> SparqlResult:
    """Run a read-only SELECT, ASK, CONSTRUCT, or DESCRIBE SPARQL query against the workspace RDF graph."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    try:
        return auth_ws.workspace.query_sparql(payload.query)
    except Exception as exc:
        raise map_domain_error(exc) from exc


@router.post("/query", response_model=TabularQueryResult, summary="Execute read-only SQL query on tabular files")
def query_workspace_tabular(
    ws_id: str,
    payload: TabularQueryRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> TabularQueryResult:
    """Run a read-only SELECT/WITH SQL query across CSV, TSV, and Parquet datasets in the workspace."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    try:
        return auth_ws.workspace.query_tabular(payload.sql, max_rows=payload.max_rows)
    except Exception as exc:
        raise map_domain_error(exc) from exc
