import os
import base64
import json
import io
import logging
import zipfile
from datetime import datetime
from collections import defaultdict

import pandas as pd
import dash
from dash import Dash, html, dcc, Input, Output, State, no_update, ALL
import dash_bootstrap_components as dbc
import pymupdf
from flask import Flask, send_from_directory
from trp import Document, Table
from trp import trp2
import dash_ag_grid as dag

class TDocumentCorrectText:
    """A data class to hold the details of a single text correction."""
    def __init__(self, cell_id: str, correction: str, **kwargs):
        self.cell_id = cell_id
        self.correction = correction

    def __repr__(self):
        return f"TDocumentCorrectText(cell_id='{self.cell_id}', correction='{self.correction}')"

class TDocumentCurrator:
    """Applies a list of curations to a Textract document object."""
    def __init__(self):
        self.curations = []

    def add(self, curation_object: TDocumentCorrectText):
        """Adds a curation task to the list."""
        self.curations.append(curation_object)

    def curate(self, doc: Document) -> Document:
        """
        Applies all stored curation tasks to the document.
        """
        for curation in self.curations:
            cell_block = doc.get_block_by_id(curation.cell_id)
            if cell_block:
                if cell_block.custom is None:
                    cell_block.custom = {}
                cell_block.custom['manual_correction'] = curation.correction
            else:
                print(f"  - WARNING: Could not find cell with ID {curation.cell_id} in the document.")
        return doc

# --- Environment and Path Setup ---
BASE_DATA_PATH = 'data'
JSON_DATA_PATH = os.path.join(BASE_DATA_PATH, 'corrected_textract_json')
PDF_DATA_PATH = os.path.join(BASE_DATA_PATH, 'processed_pdf')


# --- App Initialization ---
server = Flask(__name__)
app = Dash(
    __name__,
    server=server,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
)
app.title = "Manual Table Curation"


# --- Local File System Helper Functions ---
def read_local_file(file_path):
    try:
        with open(file_path, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        logging.error(f"File not found at: {file_path}")
        return None

def list_json_files_from_local():
    try:
        return [f for f in os.listdir(JSON_DATA_PATH) if f.endswith('.json')]
    except FileNotFoundError:
        logging.error(f"Data directory not found at: {JSON_DATA_PATH}")
        return []

def get_tables(json_filename: str):
    json_path = os.path.join(JSON_DATA_PATH, json_filename)
    try:
        json_content = read_local_file(json_path).decode('utf-8')
        textract_json = json.loads(json_content)
        return extract_table_data(textract_json)
    except Exception as e:
        logging.error(f"Error reading or parsing {json_path}: {e}")
        return []

# --- PDF Manipulation and Serving ---
@server.route('/pdf/<filename>')
def serve_pdf(filename):
    pdf_filename = filename.replace('.json', '.pdf')
    directory = os.path.abspath(PDF_DATA_PATH)
    return send_from_directory(directory, pdf_filename)

def open_local_pdf(selected_json_file):
    pdf_filename = selected_json_file.replace('.json', '.pdf')
    pdf_path = os.path.join(PDF_DATA_PATH, pdf_filename)
    pdf_content = read_local_file(pdf_path)
    if pdf_content:
        return pymupdf.open(stream=io.BytesIO(pdf_content), filetype="pdf")
    return None

def create_cell_png_from_bbox(page, bbox, zoom_factor=5.0, padding=10.0):
    """
    Zooms in on a bounding box area and returns a base64 encoded PNG data URL.
    """
    pdf_width, pdf_height = page.rect.width, page.rect.height
    x0 = bbox['left'] * pdf_width
    y0 = bbox['top'] * pdf_height
    x1 = x0 + (bbox['width'] * pdf_width)
    y1 = y0 + (bbox['height'] * pdf_height)

    # Define the crop area with padding
    crop_rect = pymupdf.Rect(x0 - padding, y0 - padding, x1 + padding, y1 + padding)
    
    # Ensure the crop area is within the page boundaries
    crop_rect.normalize()
    crop_rect.intersect(page.rect)
    
    if crop_rect.is_empty:
        return None

    # Get a pixmap (raster image) of the specified area with a zoom factor
    transform = pymupdf.Matrix(zoom_factor, zoom_factor)
    pix = page.get_pixmap(matrix=transform, clip=crop_rect)

    if not pix:
        return None

    img_bytes = pix.tobytes("png")
    encoded_image = base64.b64encode(img_bytes).decode('utf-8')
    
    return f"data:image/png;base64,{encoded_image}"

# --- Curation and Table Data Preparation ---
def find_title_for_table(table_obj: Table, doc_obj: Document) -> str:
    raw_table_block = table_obj.block
    title = "Title Not Found"
    if raw_table_block and 'Relationships' in raw_table_block:
        for rel in raw_table_block['Relationships']:
            if rel['Type'] == 'TABLE_TITLE':
                title_container_ids = rel.get('Ids', [])
                title_parts = []
                for container_id in title_container_ids:
                    title_container_block = doc_obj.getBlockById(container_id)
                    if title_container_block and 'Relationships' in title_container_block:
                        for child_rel in title_container_block['Relationships']:
                            if child_rel['Type'] == 'CHILD':
                                word_ids = child_rel.get('Ids', [])
                                for word_id in word_ids:
                                    word_block = doc_obj.getBlockById(word_id)
                                    if word_block and 'Text' in word_block:
                                        title_parts.append(word_block['Text'])
                if title_parts:
                    title = " ".join(title_parts)
                break 
    return title

def extract_table_data(textract_json):
    doc = Document(textract_json)
    if not doc.pages: return []
    all_tables_data = []
    for page in doc.pages:
        if not page.tables:
            continue
        for table in page.tables:
            table_title = find_title_for_table(table, doc)
            page_number_from_block = table.block.get('Page', 1)
            table_data = []
            headers = table.get_header_field_names()
            for row in table.rows_without_header:
                row_data = []
                for cell in row.cells:
                    original_text = cell.mergedText if cell.mergedText else ""
                    confidence = min([w.confidence for w in cell._content] or [100.0])
                    custom_data = getattr(cell, 'custom', None) or {}
                    manual_correction = custom_data.get('manual_correction')
                    ai_correction = custom_data.get('TextCorrection')
                    display_text = original_text
                    corrected_status = None
                    if manual_correction is not None:
                        display_text = manual_correction
                        corrected_status = "Human"
                    elif ai_correction is not None:
                        display_text = ai_correction
                        corrected_status = "Machine"
                    tooltip_parts = [
                        f"<b>Original:</b> {original_text}",
                        f"<b>Confidence:</b> {confidence:.2f}%"
                    ]
                    if manual_correction is not None:
                        tooltip_parts.append(f"<b>Manual:</b> {manual_correction}")
                    if ai_correction is not None:
                        tooltip_parts.append(f"<b>AI:</b> {ai_correction}")
                    tooltip_text = "<br>".join(tooltip_parts)
                    row_data.append({
                        'text': display_text,
                        'confidence': confidence,
                        'id': cell.id,
                        'bbox': {'left': cell.geometry.boundingBox.left, 'top': cell.geometry.boundingBox.top, 'width': cell.geometry.boundingBox.width, 'height': cell.geometry.boundingBox.height},
                        'corrected': corrected_status,
                        'tooltip': tooltip_text
                    })
                table_data.append(row_data)
            all_tables_data.append({
                "title": table_title, "headers": headers, "table_data": table_data,
                "page_number": page_number_from_block,
            })
    return all_tables_data

def prepare_columns(headers, table_data):
    if not headers or not headers[0]:
        if table_data and table_data[0]:
            num_columns = len(table_data[0])
            headers = [[f"Column {i + 1}" for i in range(num_columns)]]
        else:
            return []
    transposed_headers = list(map(list, zip(*headers)))
    final_headers = [" ".join(h) if len(set(h)) > 1 else h[0] for h in transposed_headers]
    return [{"headerName": h, "field": f"col{i}", "tooltipField": f"tooltip_col{i}", "cellStyle": {"styleConditions": [
        {"condition": f"params.data['corrected_col{i}'] === 'Human'", "style": {"backgroundColor": "#28a745"}},
        {"condition": f"params.data['corrected_col{i}'] === 'Machine'", "style": {"backgroundColor": "#256596"}},
        {"condition": f"params.data['confidence_col{i}'] <= 60", "style": {"backgroundColor": "rgba(255, 150, 150, 0.7)"}},
        {"condition": f"params.data['confidence_col{i}'] <= 80", "style": {"backgroundColor": "rgba(255, 200, 200, 0.7)"}},
    ]}} for i, h in enumerate(final_headers)]

def prepare_data_and_metadata(table_data):
    data, metadata = [], {}
    for row_idx, row in enumerate(table_data):
        row_dict = {}
        for i, cell in enumerate(row):
            col_id = f"col{i}"
            row_dict[col_id] = cell['text']
            row_dict[f"confidence_{col_id}"] = cell['confidence']
            row_dict[f"corrected_{col_id}"] = cell['corrected']
            row_dict[f"id_{col_id}"] = cell['id']
            row_dict[f"tooltip_{col_id}"] = cell['tooltip']
            metadata[f"{row_idx}_{i}"] = {'id': cell['id'], 'bbox': cell['bbox']}
        data.append(row_dict)
    return data, metadata

def prepare_table_data_and_metadata(tables, table_index):
    if not tables or table_index >= len(tables): 
        return [], [], {}, 1, "No Table Found"
    selected_table = tables[table_index]
    page_number = selected_table.get("page_number", 1)
    title = selected_table.get("title", "Title Not Found")
    columns = prepare_columns(selected_table.get('headers', []), selected_table.get('table_data', []))
    data, metadata = prepare_data_and_metadata(selected_table.get('table_data', []))
    return columns, data, metadata, page_number, title

def push_document_curation_to_local(curation_details):
    curator = TDocumentCurrator()
    curation_class = globals().get(curation_details['type'])
    curator.add(curation_class(**{k: v for k, v in curation_details.items() if k != 'type'}))
    selected_json = curation_details['selected_file']
    json_path = os.path.join(JSON_DATA_PATH, selected_json)
    json_content = read_local_file(json_path).decode('utf-8')
    doc = trp2.TDocumentSchema().loads(json_content)
    doc = curator.curate(doc)
    updated_json = trp2.TDocumentSchema().dumps(doc, indent=2)
    with open(json_path, 'w') as f:
        f.write(updated_json)
    print(f"Successfully saved curations to {json_path}")

# --- UI Component Creation Functions ---
def create_header():
    return dbc.NavbarSimple(brand="Manual Table Curation", color="primary", dark=True, className="mb-4")

def create_file_dropdown():
    return dbc.Card(dbc.CardBody([
        html.H4("Select a Document to Curate", className="card-title"),
        dbc.DropdownMenu(id="file-dropdown", label="Select a File...", children=[], color="secondary", className="w-100")
    ]), className="h-100")

def create_cell_zoom_view():
    """Creates a card to display a zoomed-in image of a clicked cell."""
    return dbc.Card(dbc.CardBody([
        html.H6("Cell Detail View", className="card-title text-muted"),
        html.Div(
            html.Img(id='cell-zoom-view', style={'maxWidth': '100%', 'height': 'auto'}),
            className="d-flex justify-content-center align-items-center h-100"
        )
    ]), className="h-100")

def create_table_navigation():
    return html.Div([
        html.H3(id='table-title', className="me-auto d-flex align-items-center"), 
        html.H5("Tables in File:", className="me-3"),
        html.Button("< Prev Table", id="previous-table", className="btn btn-secondary", style={"marginRight": "5px"}),
        html.Button("Next Table >", id="next-table", className="btn btn-secondary"),
    ], style={'display': 'flex', 'alignItems': 'center', 'width': '100%', 'padding': '10px 0'})

def create_table_view():
    return dag.AgGrid(
        id="main-table", 
        defaultColDef={"sortable": True, "resizable": True, "minWidth": 120, "editable": True, "tooltipComponent": "CustomTooltip", "filter": "agTextColumnFilter"},
        dashGridOptions={'tooltipShowDelay': 0},
        rowData=[], className="ag-theme-alpine-dark",
        style={"height": "70vh", "width": "100%"}, columnSize="sizeToFit"
    )

def create_save_button():
    return dbc.Button('Save Changes Locally', id='save-table-curations', color='success', className='w-100')

def create_download_button():
    return dbc.Button('Download All Tables (ZIP)', id='download-button', color='info', className='w-100')

def create_pdf_viewer():
    return html.Iframe(id='pdf-viewer', src="about:blank", style={"width": "100%", "height": "calc(80vh + 58px)"}) # Adjust height to align

def create_footer():
    return html.Footer(dbc.Container(html.P("Bumblekite Workshop Table Curation", className="text-center text-muted"), className="py-3 mt-4 border-top"))

# --- APP LAYOUT ---
app.layout = html.Div([
    dcc.Download(id="download-zip"),
    dcc.Store(id='table-metadata-store'),
    dcc.Store(id='current-table-index', data=0),
    dcc.Store(id='table-curations-store', data=[]),
    dcc.Store(id='refresh-trigger', data=''),
    dcc.Store(id='zoom-status', data=()),
    dcc.Store(id='selected-file-store'),
    dcc.Store(id='current-page-number-store'),

    create_header(),

    html.Main(className="container-fluid", children=[
        dbc.Row([
            dbc.Col(create_file_dropdown(), md=8),
            dbc.Col(create_cell_zoom_view(), md=4)
        ], className="mb-3"),
        
        html.Div(id='main-content', style={'display': 'none'}, children=[
            create_table_navigation(),
            dbc.Row([
                dbc.Col(className="col-md-6", children=[
                    create_table_view(),
                    dbc.Row([
                        dbc.Col(create_save_button(), width=6, className="mt-3"),
                        dbc.Col(create_download_button(), width=6, className="mt-3"),
                    ], className="g-2")
                ]),
                dbc.Col(create_pdf_viewer(), className="col-md-6"),
            ]),
        ]),
    ]),
    create_footer(),
])

# --- CALLBACKS ---
@app.callback(Output('main-content', 'style'), Input('selected-file-store', 'data'))
def show_main_content(selected_file):
    return {'display': 'block'} if selected_file else {'display': 'none'}

@app.callback(Output('file-dropdown', 'children'), Input('file-dropdown', 'id'))
def populate_file_dropdown(_):
    files = list_json_files_from_local()
    if not files:
        return dbc.DropdownMenuItem("No files found", disabled=True)
    return [dbc.DropdownMenuItem(file, id={'type': 'file-select-item', 'index': file}) for file in files]

@app.callback(Output('selected-file-store', 'data'), Output('file-dropdown', 'label'),
              Input({'type': 'file-select-item', 'index': ALL}, 'n_clicks'), prevent_initial_call=True)
def update_selected_file(n_clicks):
    if not any(n_clicks):
        return no_update, no_update
    file_id = dash.ctx.triggered_id['index']
    return file_id, file_id

@app.callback(
    Output('main-table', 'columnDefs'), Output('main-table', 'rowData'),
    Output('table-metadata-store', 'data'), Output('current-table-index', 'data'),
    Output('current-page-number-store', 'data'), Output('pdf-viewer', 'src'),
    Output('table-title', 'children'),
    Input('selected-file-store', 'data'), Input('next-table', 'n_clicks'),
    Input('previous-table', 'n_clicks'), Input('refresh-trigger', 'data'),
    State('current-table-index', 'data'), prevent_initial_call=True
)
def display_and_navigate_tables(selected_file, next_clicks, prev_clicks, refresh, current_table_index):
    ctx = dash.callback_context
    trigger_id = ctx.triggered_id if isinstance(ctx.triggered_id, str) else ctx.triggered_id.get('type', 'selected-file-store') if isinstance(ctx.triggered_id, dict) else 'selected-file-store'

    if not selected_file:
        return [no_update] * 7

    all_tables = get_tables(selected_file)
    total_tables = len(all_tables)

    if trigger_id == 'next-table':
        current_table_index += 1
    elif trigger_id == 'previous-table':
        current_table_index -= 1
    else:
        current_table_index = 0

    current_table_index = max(0, min(current_table_index, total_tables - 1 if total_tables > 0 else 0))
    cols, data, metadata, page_number, title = prepare_table_data_and_metadata(all_tables, current_table_index)
    
    # Always load the PDF when a file is selected.
    pdf_src = f'/pdf/{selected_file}' if trigger_id in ['selected-file-store', 'file-select-item', 'refresh-trigger'] else no_update

    display_title = [html.Span(title, className="me-3"), dbc.Badge(f"Page {page_number}", color="info", className="ms-1")]
    return cols, data, metadata, current_table_index, page_number, pdf_src, display_title

@app.callback(
    Output('cell-zoom-view', 'src'),
    Output('zoom-status', 'data'),
    Input('main-table', 'cellDoubleClicked'),
    State('table-metadata-store', 'data'),
    State('selected-file-store', 'data'),
    State('zoom-status', 'data'),
    State('current-page-number-store', 'data'),
    prevent_initial_call=True
)
def zoom_on_cell_double_click(active_cell, metadata, selected_file, zoom_status, page_number):
    if not active_cell or not metadata or not page_number:
        return no_update, no_update

    current_cell_id = (active_cell['colId'], active_cell['rowId'])
    if zoom_status == current_cell_id:
        return "", ()

    row_idx = active_cell['rowId']
    col_idx = active_cell['colId'].replace("col", "")
    metadata_key = f"{row_idx}_{col_idx}"
    bbox = metadata.get(metadata_key, {}).get('bbox')
    if not bbox: return no_update, no_update

    pdf_document = open_local_pdf(selected_file)
    if not pdf_document: return no_update, no_update

    if page_number > len(pdf_document):
        return no_update, no_update
    page = pdf_document.load_page(page_number - 1)
    
    png_data_url = create_cell_png_from_bbox(page, bbox)
    if png_data_url:
        return png_data_url, current_cell_id
    
    return no_update, no_update

@app.callback(
    Output('table-curations-store', 'data', allow_duplicate=True),
    Input('main-table', 'cellValueChanged'),
    State('table-curations-store', 'data'),
    State('selected-file-store', 'data'),
    prevent_initial_call=True
)
def store_table_curation(cellValueChanged, curations, selected_file):
    if not cellValueChanged or not selected_file: return no_update
    change = cellValueChanged[0]
    if change['value'] == change['oldValue']: return no_update
    if not curations: curations = []
    curations.append({
        'type': 'TDocumentCorrectText', 'selected_file': selected_file,
        'cell_id': change['data'][f'id_{change["colId"]}'],
        'timestamp': datetime.now().isoformat(), 'correction': change['value'],
    })
    return curations

@app.callback(
    Output('table-curations-store', 'data'),
    Output('refresh-trigger', 'data'),
    Input('save-table-curations', 'n_clicks'),
    State('table-curations-store', 'data'),
    prevent_initial_call=True
)
def push_curations(n_clicks, table_curations):
    if not n_clicks or not table_curations:
        return no_update, no_update
    grouped_by_file = defaultdict(list)
    for curation in table_curations:
        grouped_by_file[curation['selected_file']].append(curation)
    for file, curations_for_file in grouped_by_file.items():
        for curation in curations_for_file:
             push_document_curation_to_local(curation)
    return [], str(datetime.now())

@app.callback(
    Output("download-zip", "data"),
    Input("download-button", "n_clicks"),
    State("selected-file-store", "data"),
    prevent_initial_call=True,
)
def download_all_tables_as_zip(n_clicks, selected_file):
    if not n_clicks or not selected_file:
        return no_update
    all_tables = get_tables(selected_file)
    if not all_tables:
        return no_update
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'a', zipfile.ZIP_DEFLATED, False) as zip_file:
        for i, table_dict in enumerate(all_tables):
            headers = table_dict.get("headers", [])
            table_content = table_dict.get("table_data", [])
            if not table_content: continue
            if headers:
                transposed_headers = list(map(list, zip(*headers)))
                final_headers = [" ".join(h) if len(set(h)) > 1 else h[0] for h in transposed_headers]
            else:
                final_headers = []
            data_for_df = []
            for row in table_content:
                row_dict = {}
                for j, cell in enumerate(row):
                    header_name = final_headers[j] if j < len(final_headers) else f"Unnamed_Column_{j+1}"
                    row_dict[header_name] = cell['text']
                data_for_df.append(row_dict)
            if not data_for_df: continue
            df = pd.DataFrame(data_for_df)
            csv_string = df.to_csv(index=False, encoding='utf-8')
            page_num = table_dict.get("page_number")
            safe_title = "".join(c for c in table_dict.get("title", "") if c.isalnum() or c in (' ', '_')).rstrip()[:30]
            csv_filename = f"page_{page_num}_table_{i+1}_{safe_title}.csv"
            zip_file.writestr(csv_filename, csv_string)
    zip_buffer.seek(0)
    zip_filename = f"{os.path.splitext(selected_file)[0]}_tables.zip"
    return dcc.send_bytes(zip_buffer.getvalue(), zip_filename)

if __name__ == "__main__":
    app.run(debug=True)
