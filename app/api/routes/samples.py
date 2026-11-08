from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.routes.magazines import PDF_DIRECTORY

router = APIRouter()
SAMPLE_FILES = {"basic": "193-basic-en.pdf", "medical": "193-medical-en.pdf"}


@router.get("/om193/{section}")
def free_sample(section: str):
    filename = SAMPLE_FILES.get(section)
    if filename is None:
        raise HTTPException(404, "Sample section not found")
    path = PDF_DIRECTORY / filename
    if not path.is_file():
        raise HTTPException(404, "Sample is not available")
    return FileResponse(path, media_type="application/pdf", filename=f"OM193-{section}-en.pdf", content_disposition_type="inline",
                        headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"})
