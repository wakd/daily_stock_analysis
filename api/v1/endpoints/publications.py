"""Read a published analysis run, including records stored before versioned publications."""

from fastapi import APIRouter, HTTPException, Query

from src.config import get_config
from src.services.report_publications import latest_publications, read_report, recent_history

router = APIRouter()


@router.get("")
def recent(limit: int = Query(30, ge=1, le=100)):
    return {"items": latest_publications(limit), "history": recent_history(limit)}


@router.get("/{run_id}")
def report(run_id: str):
    data = read_report(run_id, get_config().terminal_chart_url)
    if data is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    return data
