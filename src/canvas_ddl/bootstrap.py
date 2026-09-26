from .config import load_config
from .canvas.client import CanvasClient
from .deadlines.service import DeadlineService
from .documents.registry import OfficialDocumentRegistry
from .documents.repository import DocumentRepository
from .documents.ingestion import DocumentIngestionService
from .documents.preparation import DocumentPreparationService
from .documents.refresh import DocumentLibraryRefresher
from datetime import datetime, timezone
from .documents.parser import DocumentParser
from .documents.ocr import PaddleOcrPageReader


def build_deadline_service(env_file=None):
    config = load_config(env_file)
    client = CanvasClient(config.base_url, config.token)
    clock = lambda: datetime.now(timezone.utc)
    ocr = PaddleOcrPageReader(device=config.ocr_device, dpi=config.ocr_dpi) if config.ocr_enabled else None
    ingestion = DocumentIngestionService(OfficialDocumentRegistry(config.document_registry, canvas_origin=config.base_url,
                                                                 official_hosts=config.document_hosts),
                                         DocumentRepository(config.document_store), timezone=config.timezone,
                                         base_url=config.base_url, clock=clock,
                                         parser=DocumentParser(ocr=ocr), ocr_min_confidence=config.ocr_min_confidence,
                                         require_semantic_review=True)
    preparation = DocumentPreparationService(client, ingestion.registry, allowed_hosts=config.document_hosts)
    service = DeadlineService(client, base_url=config.base_url, timezone_name=config.timezone,
                              allowed_ids=config.course_ids, exam_keywords=config.exam_keywords, document_ingestion=ingestion, clock=clock,
                              document_preparation=preparation,
                              document_refresher=DocumentLibraryRefresher(preparation, ingestion, clock=clock))
    return service, config
