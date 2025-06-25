from textractor.entities.document import Document
from PIL import Image
import pymupdf
import io
from typing import Union, IO, List
from textractor.utils.pdf_utils import rasterize_pdf

def _rasterize_pdf_from_stream(stream: IO[bytes], dpi: int = 200) -> List[Image.Image]:
    """Convert a PDF byte‑stream into one PIL image per page."""

    pdf = pymupdf.open(stream=stream, filetype="pdf")
    try:
        pixmaps = [page.get_pixmap(dpi=dpi) for page in pdf.pages()]
        return [Image.frombytes("RGB", (p.width, p.height), p.samples) for p in pixmaps]
    finally:
        pdf.close()


def build_image_document(
    textract_json_path: str,
    source: Union[str, IO[bytes]],
    *,
    dpi: int = 200,
) -> Document:
    """Return a :class:`trp.Document` whose pages contain their images.

    Parameters
    ----------
    textract_json_path
        Filepath to the Textract JSON ("*-analysis.json").
    source
        * If *str*: a path pointing to either a PDF (**.pdf**) or a single‑page
          image (**.png**, **.jpg**/**.jpeg**).
        * If *IO[bytes]*: a binary file‑like object containing a PDF.
    dpi
        Resolution used when rasterising PDF pages.
    """

    # ------------------------------------------------------------------
    # Load page images from *source*
    # ------------------------------------------------------------------
    if isinstance(source, str):
        lower = source.lower()
        if lower.endswith((".png", ".jpg", ".jpeg")):
            images = [Image.open(source)]
        elif lower.endswith(".pdf"):
            images = rasterize_pdf(source)
        else:
            raise ValueError("Unsupported file type. Supported: .pdf, .png, .jpg")
    elif isinstance(source, io.IOBase):
        images = _rasterize_pdf_from_stream(source, dpi)
    else:
        raise TypeError("`source` must be a path‑like string or a binary IO object")

    # ------------------------------------------------------------------
    # Open Textract JSON & inject images
    # ------------------------------------------------------------------
    document = Document.open(textract_json_path)
    for page, img in zip(document._pages, images):
        page.image = img

    return document
