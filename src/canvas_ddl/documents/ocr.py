"""Optional local OCR adapter used only while ingesting document pages/images."""
from dataclasses import dataclass
import logging


class OcrUnavailable(RuntimeError):
    pass


class OcrFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class OcrPageResult:
    text: str
    line_confidences: tuple[float, ...]
    engine: str = "PP-OCRv6_small"


class PaddleOcrPageReader:
    """Render one PDF page and recognize it with local PP-OCRv6 Small."""

    def __init__(self, *, device="cpu", dpi=150):
        self.device = device
        self.dpi = dpi
        self._pipeline = None

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        try:
            from paddleocr import PaddleOCR
        except (ImportError, OSError) as error:
            raise OcrUnavailable("PP-OCRv6 runtime is not installed.") from error
        try:
            self._pipeline = PaddleOCR(
                text_detection_model_name="PP-OCRv6_small_det",
                text_recognition_model_name="PP-OCRv6_small_rec",
                text_recognition_batch_size=1,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device=self.device,
            )
        except Exception as error:
            raise OcrUnavailable("PP-OCRv6 Small could not be initialized locally.") from error
        return self._pipeline

    def recognize_pdf_page(self, pdf_content: bytes, page_index: int) -> OcrPageResult:
        try:
            import pypdfium2 as pdfium
            import numpy as np
        except (ImportError, OSError) as error:
            raise OcrUnavailable("The local PDF OCR renderer is not installed.") from error
        try:
            document = pdfium.PdfDocument(pdf_content)
            try:
                page = document[page_index]
                try:
                    bitmap = page.render(scale=self.dpi / 72, rev_byteorder=True)
                    try:
                        image = np.asarray(bitmap.to_pil().convert("RGB"))
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            finally:
                document.close()
            return self._recognize_array(image)
        except OcrUnavailable:
            raise
        except Exception as error:
            raise OcrFailed("PP-OCRv6 Small could not recognize this PDF page.") from error

    def recognize_image(self, image_content: bytes) -> OcrPageResult:
        try:
            from PIL import Image
            from io import BytesIO
            import numpy as np
            with Image.open(BytesIO(image_content)) as source:
                image = np.asarray(source.convert("RGB"))
            return self._recognize_array(image)
        except OcrUnavailable:
            raise
        except Exception as error:
            raise OcrFailed("PP-OCRv6 Small could not recognize this image.") from error

    def _recognize_array(self, image) -> OcrPageResult:
        try:
            logging.getLogger("paddlex").setLevel(logging.WARNING)
            results = tuple(self._load().predict(image))
            if len(results) != 1:
                raise ValueError("unexpected OCR result count")
            payload = results[0].json
            if callable(payload):
                payload = payload()
            data = payload.get("res", payload)
            texts, scores = data.get("rec_texts", ()), data.get("rec_scores", ())
            if len(texts) != len(scores):
                raise ValueError("OCR text/score mismatch")
            clean_texts, clean_scores = [], []
            for text, score in zip(texts, scores, strict=True):
                line = str(text).strip()
                confidence = float(score)
                if not line:
                    continue
                if not 0 <= confidence <= 1:
                    raise ValueError("invalid OCR confidence")
                clean_texts.append(line)
                clean_scores.append(confidence)
            return OcrPageResult("\n".join(clean_texts), tuple(clean_scores))
        except OcrUnavailable:
            raise
        except Exception as error:
            raise OcrFailed("PP-OCRv6 Small could not recognize this image.") from error
