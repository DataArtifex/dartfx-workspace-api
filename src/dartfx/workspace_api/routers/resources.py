"""Knowledge Base resource metadata inspection, updates, and RDF serialization export."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from dartfx.workspace.models import Action, Principal, ResourceDetail
from workspace_api.dependencies import (
    get_current_principal,
    get_db_session,
    map_domain_error,
    resolve_authorized_workspace,
)
from workspace_api.models import ResourceUpdateRequest

router = APIRouter(prefix="/workspaces/{ws_id}", tags=["resources"])

_RDF_MEDIA_TYPES: dict[str, str] = {
    "ttl": "text/turtle",
    "turtle": "text/turtle",
    "jsonld": "application/ld+json",
    "json-ld": "application/ld+json",
    "rdfxml": "application/rdf+xml",
    "xml": "application/rdf+xml",
    "n3": "text/n3",
    "nt": "application/n-triples",
}


def _rdf_media_type(fmt: str) -> str:
    return _RDF_MEDIA_TYPES.get(fmt.strip().lower(), "text/plain")


@router.get("/resources", response_model=list[ResourceDetail], summary="List all registered KB resources")
def list_kb_resources(
    ws_id: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> list[ResourceDetail]:
    """Return all file resources registered in the workspace Knowledge Base."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    try:
        ws = auth_ws.workspace
        kb_files = ws.kb.get_all_files()
        details: list[ResourceDetail] = []
        for item in kb_files:
            attrs = ws.kb.get_resource_attributes(UUID(item["uuid"]))
            details.append(
                ResourceDetail(
                    uuid=item["uuid"],
                    path=item["path"],
                    size_bytes=item["size_bytes"],
                    blake3_hash=item["blake3_hash"],
                    type=item["type"],
                    file_format=item["file_format"],
                    mime_type=item.get("mime_type"),
                    created_at=item["created_at"],
                    updated_at=item["updated_at"],
                    attributes=attrs,
                )
            )
        return details
    except Exception as exc:
        raise map_domain_error(exc) from exc


@router.get("/resources/{uuid}/rdf", summary="Export single resource RDF metadata")
def export_resource_rdf(
    ws_id: str,
    uuid: str,
    format: str = Query(default="turtle"),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> Response:
    """Serialize a registered resource's RDF triples in Turtle, JSON-LD, RDF/XML, or N3."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    try:
        serialized = auth_ws.workspace.export_rdf(uuid_val=uuid, rdf_format=format)
        return Response(content=serialized, media_type=_rdf_media_type(format))
    except Exception as exc:
        raise map_domain_error(exc) from exc


@router.get(
    "/resources/{identifier:path}",
    response_model=ResourceDetail,
    summary="Inspect Knowledge Base metadata by UUID or relative path",
)
def get_resource_detail(
    ws_id: str,
    identifier: str,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> ResourceDetail:
    """Retrieve complete FAIR metadata and format-specific attributes for a registered resource."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    try:
        return auth_ws.workspace.get_resource_detail(identifier)
    except Exception as exc:
        raise map_domain_error(exc) from exc


@router.patch(
    "/resources/{uuid}",
    response_model=ResourceDetail,
    summary="Update resource classification or custom attributes",
)
def update_resource_metadata(
    ws_id: str,
    uuid: str,
    payload: ResourceUpdateRequest,
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> ResourceDetail:
    """Manually reclassify file type/format or merge custom DARTFX attributes on a KB resource."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.WRITE, session, principal)
    try:
        return auth_ws.workspace.update_resource_metadata(
            uuid,
            file_type=payload.file_type,
            file_format=payload.file_format,
            attributes=payload.attributes,
        )
    except Exception as exc:
        raise map_domain_error(exc) from exc


@router.get("/rdf", summary="Export entire workspace RDF Knowledge Base graph")
def export_workspace_rdf(
    ws_id: str,
    format: str = Query(default="turtle"),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db_session),
) -> Response:
    """Serialize all triples in the workspace Knowledge Base in Turtle, JSON-LD, RDF/XML, or N3."""
    auth_ws = resolve_authorized_workspace(ws_id, Action.READ, session, principal)
    try:
        serialized = auth_ws.workspace.export_rdf(rdf_format=format)
        return Response(content=serialized, media_type=_rdf_media_type(format))
    except Exception as exc:
        raise map_domain_error(exc) from exc
