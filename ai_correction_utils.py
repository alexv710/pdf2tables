from pydantic import BaseModel, Field
import uuid
import base64
import pymupdf
from trp.trp2 import TBlock, TDocument, TextractBlockTypes
import math
from IPython.display import display, HTML

class CorrectedResponse(BaseModel):
    """Corrected response""" 
    text: str

class CellTextImagePairs(BaseModel):
    id: str = Field(description="The ID of the cell", default_factory=lambda: str(uuid.uuid4())[:4])
    cell_id: str = Field(description="The ID of the cell in the document")
    text: str = Field(description="The text content of the cell")
    image: str = Field(description="The base64 encoded image of the cell")
    confidence: float = Field(description="The confidence score of the OCR text extraction", default=100.0)



def get_text_for_cell(cell_block: TBlock, t_document: TDocument) -> str:
    """
    Retrieves the concatenated text of a cell.

    Args:
        cell_block (t2.TBlock): The cell block.
        t_document (t2.TDocument): The document containing the cell.

    Returns:
        str: The concatenated text.
    """

    if cell_block.custom and cell_block.custom.get("ContentText", None):
        return cell_block.custom.get("ContentText", None)

    text = ''
    if cell_block.relationships:
        for rel in cell_block.relationships:
            if rel.type == 'CHILD':
                for child_id in rel.ids:
                    child_block = t_document.get_block_by_id(child_id)
                    if child_block.block_type == 'WORD':
                        text += child_block.text + ' '
    return text.strip()

def get_content_confidence(t_document: TDocument, cell: TBlock) -> list[int]:
    
    if cell.custom and cell.custom.get("ContentConfidence"):
        return cell.custom.get("ContentConfidence")
    
    child_relationships = cell.get_relationships_for_type("CHILD")
    child_ids = child_relationships.ids if child_relationships else []
    confidence = []
    for child_id in child_ids:
        child_block = t_document.get_block_by_id(child_id)
        if child_block.block_type == 'WORD':
            confidence.append(child_block.confidence)
    return confidence

def get_rotation_angle(x0: float, y0: float, x1: float, y1: float) -> float:

    delta_x = x1 - x0   
    delta_y = y1 - y0

    angle_rad = math.atan2(delta_y, delta_x)
    angle_deg = math.degrees(angle_rad)

    return angle_deg


def add_cell_text_correction(cell_block: TBlock, correction: str) -> TBlock:

    if not correction:
        return cell_block
    
    if not cell_block.custom:
        cell_block.custom = {}
    
    cell_block.custom["CorrectedText"] = correction
    cell_block.custom["Corrected"] = True

    return cell_block

def filter_cells(t_document: TDocument,
                 cells: list[TBlock],
                 confidence_threshold: float = 80) -> list[dict]:
    """
    Return cells whose *lowest* content‑confidence score is less than or equal
    to the provided threshold.
    This is useful to filter out cells that are already sufficiently confident
    """
    filtered_cells: list[dict] = []

    for cell in cells:
        cell_confidences = get_content_confidence(t_document, cell)
        lowest_confidence = min(cell_confidences, default=100)

        if lowest_confidence <= confidence_threshold:
            filtered_cells.append(cell)

    return filtered_cells

async def get_document_corrections(t_document: TDocument,
                                   corrector,
                                   document: bytes,
                                   confidence_threshold: float = 80) -> dict[CorrectedResponse]:
    """
    Retrieve every (text, image) cell pair in the document, send them to the
    provided `corrector`, and return only the successful corrections.

    Any item that raises an exception during correction is omitted from the
    result map.
    """
    # Build the collection of cell‑based inputs
    text_image_pairs = get_document_text_image_pairs(
        t_document, document, confidence_threshold=confidence_threshold
    )

    batch_payload = {
        pair._id: {
            "text": pair.text,
            "confidence": pair.confidence,
            "image_data": base64.b64encode(pair.image).decode("utf-8"),
        }
        for pair in text_image_pairs
    }

    # Run corrections
    corrected_batch = await corrector.batch_correct(batch_payload)

    # Keep only successful corrections
    successful_corrections = {
        cell_id: response
        for cell_id, response in corrected_batch.items()
        if not isinstance(response, Exception)
    }

    return successful_corrections


def get_page_text_image_pairs(t_page: TBlock,
                              t_document: TDocument,
                              document_page: pymupdf.Page,
                              confidence_threshold: float = 80) -> list[CellTextImagePairs]:
    """
    Return (text, image) pairs for a single page, after filtering cells by confidence.
    """
    cells = t_document.get_blocks_by_type(TextractBlockTypes.CELL, t_page)
    filtered_cells = filter_cells(
        t_document, cells, confidence_threshold=confidence_threshold
    )

    return get_text_image_pairs_page(filtered_cells, t_document, document_page)

def get_document_text_image_pairs(t_document: TDocument,
                                  document: bytes,
                                  confidence_threshold: float = 80) -> list[CellTextImagePairs]:
    """
    Iterate through the PDF and aggregate (text, image) pairs for every page.
    """
    pdf = pymupdf.Document(stream=document)

    all_pairs: list[CellTextImagePairs] = []
    for t_page, pdf_page in zip(t_document.pages, pdf.pages()):
        page_pairs = get_page_text_image_pairs(
            t_page, t_document, pdf_page, confidence_threshold=confidence_threshold
        )
        all_pairs.extend(page_pairs)

    return all_pairs

def get_text_image_pairs_page(cells: list[TBlock],
                              t_document: TDocument,
                              document_page: pymupdf.Page) -> list[CellTextImagePairs]:
    """
    Build `CellTextImagePairs` objects for the provided cells on the given page.
    """
    pairs: list[CellTextImagePairs] = []

    for cell in cells:
        text = get_text_for_cell(cell, t_document)
        image = get_cropped_image(cell, document_page)
        confidence = get_content_confidence(t_document=t_document, cell=cell)

        pairs.append(CellTextImagePairs(cell_id=cell.id, 
                                        text=text, 
                                        image=image, 
                                        confidence=min(confidence)))

    return pairs

def get_cropped_image(cell: TBlock,
                      document_page: pymupdf.Page,
                      dpi: int = 300) -> bytes:
    """
    Crop the portion of the PDF page corresponding to `cell` and return it as
    JPEG bytes.

    Textract returns bounding‑box coordinates in percentages; we convert them
    to absolute coordinates before rendering the pixmap.
    """
    # ---------- Convert Textract bbox (percentages) → PDF points ----------
    bbox = cell.geometry.bounding_box
    page_width, page_height = document_page.rect.width, document_page.rect.height

    x0 = bbox.left * page_width
    y0 = bbox.top * page_height
    x1 = x0 + bbox.width * page_width
    y1 = y0 + bbox.height * page_height

    # ---------- Determine rotation ----------
    angle = get_rotation_angle(
        cell.geometry.polygon[0].x, cell.geometry.polygon[0].y,
        cell.geometry.polygon[1].x, cell.geometry.polygon[1].y
    )

    # ---------- Render pixmap ----------
    matrix = pymupdf.Matrix(dpi / 72, dpi / 72) * pymupdf.Matrix(-angle)
    clip_rect = pymupdf.IRect(x0=x0, y0=y0, x1=x1, y1=y1)
    pixmap: pymupdf.Pixmap = document_page.get_pixmap(matrix=matrix, clip=clip_rect)

    return base64.b64encode(pixmap.tobytes(output="jpeg", jpg_quality=98)).decode("utf-8")


def batched(iterable, n=1):
    """
    Batches an iterable.
    """
    length = len(iterable)
    for ndx in range(0, length, n):
        yield iterable[ndx : min(ndx + n, length)]


def convert_image_to_base64(image_bytes):
    return base64.b64encode(image_bytes).decode('utf-8')


def display_ocr_image_pair(ocr_image_pairs):
    caption_image_pair_html = """
    <div style="width: 100%; text-align: center; font-weight: bold; margin-bottom: 10px;">
        <div style="display: flex; justify-content: space-between;">
            <div style="width: 20%; text-align: center;">Predicted Text</div>
            <div style="width: 30%; text-align: center;">Confidence</div>
            <div style="width: 50%; text-align: center;">Image</div>
        </div>
    </div>

    <div style="height: 500px; overflow-y: scroll; border: 1px solid #ccc; padding: 10px;">
    """
    for pair in ocr_image_pairs:
        conf_value = pair.confidence

        caption_image_pair_html += (
            '<div style="display: flex; align-items: center; margin-bottom: 20px;">'
            f'<div style="width: 20%; margin-right: 10px; text-align: center;">{pair.text}</div>'
            f'<div style="width: 30%; margin-right: 10px; text-align: center;">{conf_value:.2f}%</div>'
            f'<div style="width: 50%; text-align: center;"><img src="data:image/jpg;base64,{pair.image}" style="width: 100%;" /></div>'
            '</div>'
        )

    caption_image_pair_html += '</div>'
    display(HTML(caption_image_pair_html))


def merge_corrections(t_document: TDocument, corrections: dict[str, CorrectedResponse]) -> TDocument:
    for id, correction in corrections.items():
        cell_block = t_document.find_block_by_id(id)
        if cell_block:
            if not cell_block.custom:
                cell_block.custom = {"TextCorrection": correction.text}
            else:
                cell_block.custom.update({"TextCorrection": correction.corrected_text})   
    return t_document


import difflib
import base64
from IPython.display import display, HTML
def convert_image_to_base64(image_bytes):
    return base64.b64encode(image_bytes).decode('utf-8')

def highlight_differences(original, corrected):
    matcher = difflib.SequenceMatcher(None, original, corrected)
    
    original_highlighted = ""
    corrected_highlighted = ""
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'replace':
            # Highlight replaced text
            original_highlighted += f"<span style='color: red;'>{original[i1:i2]}</span>"
            corrected_highlighted += f"<span style='color: green;'>{corrected[j1:j2]}</span>"
        elif tag == 'delete':
            # Highlight deleted text
            original_highlighted += f"<span style='color: red;'>{original[i1:i2]}</span>"
        elif tag == 'insert':
            # Highlight inserted text
            corrected_highlighted += f"<span style='color: green;'>{corrected[j1:j2]}</span>"
        elif tag == 'equal':
            # No changes, so keep the text as is
            original_highlighted += f"{original[i1:i2]}"
            corrected_highlighted += f"{corrected[j1:j2]}"
    
    return original_highlighted, corrected_highlighted


def visualize_differences_with_image(prediction_image_pairs, corrected_response: dict[str]):
    html_output = '''
    <div style="height: 500px; overflow-y: scroll; border: 1px solid #ccc; padding: 10px;">
    '''
    
    for id, response in corrected_response.items():
        
        prediction_image_pair = prediction_image_pairs[id]
        original_text = prediction_image_pair.text
        corrected_text = response.text
        
        # Get the highlighted text for original and corrected
        original_highlighted, corrected_highlighted = highlight_differences(original_text, corrected_text)
        
        image_base64 = prediction_image_pair.image
        
        # Create the HTML for displaying original, corrected text, and image side by side
        html_output += f"""
        <div style="display: flex; align-items: center; margin-bottom: 20px; border-bottom: 1px solid #ccc; padding-bottom: 20px;">
            <div style="width: 50%;">
                <div><strong>Original:</strong> {original_highlighted}</div>
                <div><strong>Corrected:</strong> {corrected_highlighted}</div>
            </div>
            <div style="width: 50%; text-align: center; padding-left: 20px;">
                <img src="data:image/jpg;base64,{image_base64}" style="max-width: 100%; height: auto;" />
            </div>
        </div>
        """
    
    html_output += "</div>"
    
    # Display the final HTML
    display(HTML(html_output))