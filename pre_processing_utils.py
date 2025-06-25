import cv2
import fitz
import numpy as np
import PIL.Image
import io
from typing import Callable, Iterator, Tuple

RESOLUTION = 300


def to_pixmap(page: fitz.Page) -> fitz.Pixmap:
    return page.get_pixmap(dpi=RESOLUTION, alpha=False, colorspace=fitz.csGRAY)


def to_image(pixmap: fitz.Pixmap) -> np.ndarray:
    return np.array(PIL.Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples))


# Black magic: https://discuss.python.org/t/getting-generator-return-values-with-natural-for-loop-syntax/59556/24
def loopval(iterable):
    yield lambda: result
    result = yield from iterable


def check_for_table(image: np.ndarray) -> bool:
    """
    Filters out the noise and checks if an image contains a table by detecting horizontal and vertical lines.
    Any page that has boxes will be considered a table-containing page.
    """

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    opening = cv2.morphologyEx(image, cv2.MORPH_OPEN, kernel, iterations=2)

    grad_x = cv2.Sobel(opening, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(opening, cv2.CV_32F, 0, 1, ksize=3)

    abs_grad_x = cv2.convertScaleAbs(grad_x)
    abs_grad_y = cv2.convertScaleAbs(grad_y)

    edges = cv2.addWeighted(abs_grad_x, 0.5, abs_grad_y, 0.5, 0)
    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
    kernel_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))

    horizontal_lines = cv2.morphologyEx(edges, cv2.MORPH_OPEN, kernel_h)
    vertical_lines = cv2.morphologyEx(edges, cv2.MORPH_OPEN, kernel_v)

    lines = cv2.add(horizontal_lines, vertical_lines)
    contours, _ = cv2.findContours(lines, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    if contours:
        areas = [cv2.contourArea(c) for c in contours]
        max_area = max(areas) if areas else 0
        return max_area > 66.66 * RESOLUTION
    return False


def image_iterator(pdf: fitz.Document) -> Iterator[PIL.Image.Image] | dict[int, int]:
    """
    Yields the images of the pages in the PDF. Indicates whether the page contains a table or not.
    Returns a tuple of the page indices of the tables and text.
    """
    table_indices = []
    text_indices = []
    for i, page in enumerate(pdf, start=1):
        page_image = to_image(to_pixmap(page))

        if check_for_table(page_image):
            table_indices.append(i)
            yield page_image, True
        else:
            text_indices.append(i)
            yield page_image, False

    return table_indices, text_indices

def add_to_pdf(pdf: fitz.Document, image: np.ndarray) -> fitz.Document:
    """
    Adds an image to a PDF as a new page.
    """
    pil_image = PIL.Image.fromarray(image)
    img_buffer = io.BytesIO()
    pil_image.save(img_buffer, format="PNG")
    img_bytes = img_buffer.getvalue()
    img_buffer.close()

    # Scale image to original dimensions
    new_page = pdf.new_page(width=pil_image.width / RESOLUTION * 72, height=pil_image.height / RESOLUTION * 72)
    new_page.insert_image(new_page.rect, stream=img_bytes)
    return pdf

def get_table_indices(
    pdf: fitz.Document,
) -> Tuple[fitz.Document, fitz.Document, list[list], list[int]]:
    """
    Sorts the pages of the PDF into two separate PDFs, one for tables and one for text.
    Also returns the page indexes of the tables and text.
    """

    table_pdf = fitz.open()
    text_pdf = fitz.open()
    page_iterator = loopval(image_iterator(pdf))
    indices = next(page_iterator)
    for page, table_bool in page_iterator:
        if table_bool:
            table_pdf = add_to_pdf(table_pdf, page)
        else:
            text_pdf = add_to_pdf(text_pdf, page)

    table_indices, text_indices = indices()
    return table_pdf, text_pdf, table_indices, text_indices