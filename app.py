import os
import json
import dash
from dash import Dash, html, dcc, Input, Output, State, no_update, ALL
import dash_bootstrap_components as dbc
from trp import Document
import dash_ag_grid as dag

# --- Basic Setup ---
BASE_DATA_PATH = 'data'
JSON_DATA_PATH = os.path.join(BASE_DATA_PATH, 'corrected_textract_json')

# Every Dash app starts by creating an instance of the `Dash` class.
app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
)
app.title = "Intro to Dash"


# --- Helper Functions for Data Handling ---
def list_json_files_from_local():
    """Lists all .json files in our data directory."""
    try:
        return [f for f in os.listdir(JSON_DATA_PATH) if f.endswith('.json')]
    except FileNotFoundError:
        print(f"ERROR: Data directory not found at: {JSON_DATA_PATH}")
        return []

def get_all_tables_from_file(json_filename: str):
    """Reads a single JSON file and returns a list of all tables."""
    if not json_filename:
        return []
    json_path = os.path.join(JSON_DATA_PATH, json_filename)
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            textract_json = json.load(f)
        doc = Document(textract_json)
        all_tables = []
        for page in doc.pages:
            if page.tables:
                all_tables.extend(page.tables)
        return all_tables
    except Exception as e:
        print(f"Error reading or parsing {json_path}: {e}")
        return []


# --- WHAT: Layout ---
# The `app.layout` defines the visual structure of your application.
app.layout = dbc.Container([
    html.H1("Minimal Dash Table Viewer", className="my-4"),

    dbc.DropdownMenu(
        id="file-dropdown",
        label="Select a File...",
        children=[],
        color="secondary",
        className="w-100 mb-3"
    ),

    html.Div([
        html.Button("< Previous Table", id="prev-button", className="me-2"),
        html.Button("Next Table >", id="next-button"),
    ], className="my-3"),

    dag.AgGrid(
        id='table-display',
        className="ag-theme-alpine-dark",
        style={"height": "60vh"},
        columnSize="sizeToFit",
    ),

    # `dcc.Store` is an invisible component that stores data in the browser.
    # It's essential for sharing state between callbacks.
    dcc.Store(id='current-table-index', data=0),
    dcc.Store(id='selected-file-store'),

], fluid=True)


# --- HOW: Callbacks ---
# Callbacks link user interactions (Inputs) to app updates (Outputs).

@app.callback(
    Output('file-dropdown', 'children'),
    Input('file-dropdown', 'id')
)
def populate_file_dropdown(_):
    """This callback runs once on app load to create the dropdown items."""
    files = list_json_files_from_local()
    return [dbc.DropdownMenuItem(file, id={'type': 'select-file-button', 'index': file}) for file in files]


@app.callback(
    Output('selected-file-store', 'data'),
    Output('file-dropdown', 'label'),
    Input({'type': 'select-file-button', 'index': ALL}, 'n_clicks'),
    prevent_initial_call=True
)
def update_selected_file(n_clicks):
    """When a menu item is clicked, this saves the filename to our invisible store."""
    # `dash.ctx.triggered_id` tells us exactly which item was clicked.
    selected_file = dash.ctx.triggered_id['index']
    return selected_file, selected_file


@app.callback(
    Output('table-display', 'rowData'), #       1. return value of the fuction
    Output('table-display', 'columnDefs'), #    2. return value of the function
    Output('current-table-index', 'data'), #    3. return value of the function

    Input('selected-file-store', 'data'), #     1. input to the function
    Input('prev-button', 'n_clicks'), #         2. input to the function
    Input('next-button', 'n_clicks'), #         3. input to the function
    State('current-table-index', 'data'), #     4. input to the function
    prevent_initial_call=True
)
def update_table_view(selected_file, prev_clicks, next_clicks, table_index):
    """This function updates the table display based on user actions."""
    # `dash.ctx.triggered_id` tells us which Input was activated.
    triggered_id = dash.ctx.triggered_id

    if not selected_file:
        return no_update, no_update, no_update

    all_tables = get_all_tables_from_file(selected_file)
    if not all_tables:
        return [], [], 0

    # Logic to navigate between tables
    # Check if the trigger is a simple string (from a button) or a dict (from the store).
    if isinstance(triggered_id, str) and triggered_id == 'next-button':
        table_index += 1
    elif isinstance(triggered_id, str) and triggered_id == 'prev-button':
        table_index -= 1
    else:
        # If a new file was selected, reset to the first table.
        table_index = 0

    num_tables = len(all_tables)
    table_index = table_index % num_tables
    current_table = all_tables[table_index]

    # --- Data Preparation for AG Grid ---
    max_cols = 0
    rowData = []
    for row in current_table.rows:
        row_dict = {}
        for i, cell in enumerate(row.cells):
            row_dict[f'col_{i}'] = cell.text
        rowData.append(row_dict)
        if len(row.cells) > max_cols:
            max_cols = len(row.cells)

    columnDefs = [{"headerName": f"Column {i+1}", "field": f"col_{i}"} for i in range(max_cols)]

    return rowData, columnDefs, table_index


# --- Running the Application ---
if __name__ == "__main__":
    app.run(debug=True)
