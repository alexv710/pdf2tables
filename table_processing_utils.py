import trp
import trp.t_tables
import pandas as pd
from enum import Enum
from trp import trp2


class HeaderFooterType(Enum):
    NONE = 0
    NARROW = 0.5
    NORMAL = 1
    LARGE = 1.5

def table_to_dataframe(table: trp.Table) -> pd.DataFrame:
    cell_map = {}
    max_row = 0
    max_col = 0

    for row in table.rows:
        for cell in row.cells:
            row_idx = cell.rowIndex - 1  # 0-based index
            col_idx = cell.columnIndex - 1
            cell_content = None
            if hasattr(cell, "_custom") and cell._custom:
                cell_content = cell._custom.get("HumanCorrection", cell._custom.get("aiCorrection"))
            if cell_content is None:
                cell_content = cell.mergedText if cell._isChildOfMergedCell else cell._text.strip()

            cell_map[(row_idx, col_idx)] = cell_content

            if row_idx > max_row:
                max_row = row_idx
            if col_idx > max_col:
                max_col = col_idx

    # Initialize blank data grid
    data = [["" for _ in range(max_col + 1)] for _ in range(max_row + 1)]

    # Fill data grid
    for (row_idx, col_idx), content in cell_map.items():
        data[row_idx][col_idx] = content

    df = pd.DataFrame(data)

    # Handle headers
    header_rows = len(table.header)
    if header_rows > 0:
        headers = df.iloc[:header_rows]
        combined_headers = headers.apply(lambda x: " ".join(filter(None, x)), axis=0)
        df = df.iloc[header_rows:]
        df.columns = combined_headers
        df.reset_index(drop=True, inplace=True)
    return df


def export_tables(document: dict) -> dict[str, str]:
    # Convert the document to a TRP Document object
    doc: trp.Document = trp.Document(document)
    output_list = []
    for page_idx, page in enumerate(doc.pages, start=1):
        block = next(iter(page._blocks))
        page_idx = block.get("Page", page_idx)
        for table in page.tables:
            df = table_to_dataframe(table)
            output_list.append(df)
    return output_list



def merge_tables_doc(
    document: trp.Document, table_array_ids: list[list[str]]
) -> dict[str, dict[str, pd.DataFrame | float]]:
    """
    Merges tables based on the table array ids and returns the updated TDocument

    Args:
        t_document: TDocument object
        table_array_ids: List of table id groups to merge

    Returns:
        t2.TDocument: Updated TDocument object
    """

    id_table_map: dict[str, dict[str, pd.DataFrame | float | int]] = {
        table._id: {
            "table": table_to_dataframe(table),
            "page": int(table.block["Page"]),
            "top": float(table.block["Geometry"]["BoundingBox"]["Top"]),
        }
        for page in document.pages
        for table in page.tables
    }

    for table_ids in table_array_ids:
        parent_table_id = table_ids[0]
        if len(table_ids) < 2 or not parent_table_id:
            continue
        parent_table = id_table_map.get(parent_table_id)
        if not isinstance(parent_table, dict) and not parent_table.get("table"):
            raise ValueError("parent table is invalid")

        table_ids.pop(0)
        n_cols = parent_table["table"].shape[1]

        parent_cols = parent_table["table"].columns.tolist()
        child_tables_to_merge = []

        for table_id in table_ids:
            child = id_table_map.pop(table_id)
            if child["table"].shape[1] < n_cols:
                for i in range(child.shape[1], n_cols):
                    child["table"][f"extra_{i}"] = pd.NA
                child["table"] = child["table"].reindex(columns=parent_cols)
            elif child["table"].shape[1] > n_cols:
                child["table"] = child["table"].iloc[:, :n_cols]
                child["table"].columns = parent_cols
            else:
                child["table"].columns = parent_cols
            child_tables_to_merge.append(child["table"])

        # Concatenate the parent's table with the adjusted child tables
        id_table_map[parent_table_id]["table"] = pd.concat(
            [parent_table["table"]] + child_tables_to_merge, ignore_index=True
        )

    return list(id_table_map.values())


def validate_objects_between_tables(
    page1: trp.Page,
    page1_table: trp.Table,
    page2: trp.Page,
    page2_table: trp.Table,
    header_footer_type: HeaderFooterType,
):
    """
    Check if there is any lines between the first and second table except in the Footer and Header area

    Args:
        page1: TBlock
        page1_table: TBlock
        page2: TBlock
        page2_table: TBlock
        header_footer_type: HeaderFooterType
    """

    page_page1 = next(iter(page1._blocks), None)
    page_page2 = next(iter(page2._blocks), None)
    if not page_page1 or not page_page2:
        return False
    page_page1 = page_page1["Page"]
    page_page2 = page_page2["Page"]
    if abs(page_page1 - page_page2) != 1:
        return False

    header_footer_height = header_footer_type.value / 10
    table1_end_y = page1_table.geometry.boundingBox.top + page1_table.geometry.boundingBox.height

    if any(1 - header_footer_height > line.geometry.polygon[2].y > table1_end_y for line in page1.lines):
        return False
    table2_start_y = page2_table.geometry.boundingBox.top
    if any(header_footer_height < line.geometry.boundingBox.top < table2_start_y for line in page2.lines):
        return False
    return True



def table_relationship_detection_function_(
    t_doc: trp.Document, header_footer_type: HeaderFooterType, accuracy_percentage: float
) -> list[list[str]]:
    """
    Detects whether tables are mergeable based on the following criteria:
        1. Validate objects between tables
        2. Compare table column numbers
        3. Compare table headers
        4. Compare table dimensions

    Args:
        t_doc: TDocument object
        header_footer_type: HeaderFooterType An object determining how large the header and footer are
        accuracy_percentage: float

    Returns:
        List[List[str]]: List of table id groups to merge from bottom up
    """
    page_compare_proc = 0
    # table_ids_to_merge = {}
    table_ids_merge_list = []

    for current_page in t_doc.pages:
        if page_compare_proc >= len(t_doc.pages) - 1:
            break
        if len(current_page.tables) == 0:
            page_compare_proc += 1
            continue
        current_page_table = current_page.tables[len(current_page.tables) - 1]
        next_page = t_doc.pages[page_compare_proc + 1]

        if len(next_page.tables) == 0:
            page_compare_proc += 1
            continue
        next_page_table = next_page.tables[0]

        result_1 = validate_objects_between_tables(
            current_page, current_page_table, next_page, next_page_table, header_footer_type
        )
        if result_1:
            result_2_1 = trp.t_tables.__compare_table_column_numbers(current_page_table, next_page_table)
            result_2_2 = trp.t_tables.__compare_table_headers(current_page_table, next_page_table)

            if result_2_1 or result_2_2:
                result3 = trp.t_tables.__compare_table_dimensions(
                    current_page_table, next_page_table, accuracy_percentage
                )
                if result3:
                    if table_ids_merge_list:
                        if any(merge_pairs[-1] == current_page_table.id for merge_pairs in table_ids_merge_list):
                            table_ids_merge_list[len(table_ids_merge_list) - 1].append(next_page_table.id)
                        else:
                            table_ids_merge_list.append([current_page_table.id, next_page_table.id])
                    else:
                        table_ids_merge_list.append([current_page_table.id, next_page_table.id])
        page_compare_proc += 1

    return table_ids_merge_list


def get_table_merge_ids_doc(
    document: trp.Document,
    header_footer_type: HeaderFooterType = HeaderFooterType.NORMAL,
    accuracy_percentage: float = 0.95,
) -> list[list[str]]:
    """
    Get a list of table id groups to merge which are merged together
    Args:
        t_document: TDocument object

    Returns:
        List[List[str]]: List of table id groups to merge
    """
    table_merge_ids = table_relationship_detection_function_(document, header_footer_type, accuracy_percentage)
    return table_merge_ids[::-1]


def sort_blocks_by_position(blocks: list[dict]) -> list[dict]:

    def get_top_position(block: dict) -> float:
        geo = block.get("Geometry")
        if geo:
            bbox = geo.get("BoundingBox")
            if bbox:
                return bbox.get("Top")
        return 1.0

    def get_block_page(block: dict) -> int:
        page = block.get("Page")
        return page if page else 0

    return sorted(blocks, key=lambda block: (get_block_page(block), get_top_position(block)))



def merge_document_tables_doc(
    document: dict, header_footer_type: HeaderFooterType = HeaderFooterType.NORMAL, accuracy_percentage: float = 0.95
) -> list[pd.DataFrame]:
    document["Blocks"] = sort_blocks_by_position(document["Blocks"])
    doc = trp.Document(document)
    tables_merge_ids: list[list[str]] = get_table_merge_ids_doc(
        doc, header_footer_type=header_footer_type, accuracy_percentage=accuracy_percentage
    )
    tables = merge_tables_doc(doc, tables_merge_ids)
    return tables



def remove_table_blocks(doc: dict, confidence_threshold=90):

    document: trp2.TDocument = trp2.TDocumentSchema().load(doc)
    page_blocks = document.filter_blocks_by_type(document.blocks, 
                                                 textract_block_type=[trp2.TextractBlockTypes.TABLE])
    
    delete_blocks = []

    for page_block in page_blocks:

        if page_block.confidence < confidence_threshold:
            delete_blocks.append(page_block.id)

    document.delete_blocks(delete_blocks)

    return trp2.TDocumentSchema().dump(document)
    


