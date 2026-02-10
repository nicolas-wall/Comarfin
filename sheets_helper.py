import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os
import json

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Module-level cache
_gc = None
_spreadsheet_id = None
SPREADSHEET_ID_FILE = os.path.join(os.path.dirname(__file__), '.sheets_id')


def _get_client():
    """Get or create authenticated gspread client."""
    global _gc
    if _gc is not None:
        return _gc

    # Try env var first (for Render), then local file
    creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    if creds_json:
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    else:
        creds_path = os.path.join(os.path.dirname(__file__), 'google_credentials.json')
        if not os.path.exists(creds_path):
            raise FileNotFoundError("Google credentials not found")
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)

    _gc = gspread.authorize(creds)
    return _gc


def _get_spreadsheet_id():
    """Get the stored spreadsheet ID."""
    global _spreadsheet_id
    if _spreadsheet_id:
        return _spreadsheet_id

    if os.path.exists(SPREADSHEET_ID_FILE):
        with open(SPREADSHEET_ID_FILE, 'r') as f:
            _spreadsheet_id = f.read().strip()
            return _spreadsheet_id
    return None


def _save_spreadsheet_id(sid):
    """Save spreadsheet ID to file."""
    global _spreadsheet_id
    _spreadsheet_id = sid
    with open(SPREADSHEET_ID_FILE, 'w') as f:
        f.write(sid)


def _get_or_create_spreadsheet(user_email=None):
    """Get the existing shared spreadsheet."""
    gc = _get_client()
    
    # ID of the spreadsheet shared by the user
    # Title: 'Comarfin users'
    sid = '1ToLqnylV8AO_84Rk4tya0facoDJvxHwRfgTD1IRO9as'

    try:
        sh = gc.open_by_key(sid)
        _save_spreadsheet_id(sid)
        
        # Check if header exists, if not add it
        ws = sh.sheet1
        existing_val = ws.acell('A1').value
        if not existing_val:
            headers = [
                'Fecha Consulta', 'DNI', 'Sexo', 'CUIT',
                # BCRA
                'Nombre (BCRA)', 'Situación BCRA', 'Deuda Total',
                'Entidades Reportando',
                # AFIP
                'Nombre (AFIP)', 'Estado CUIT', 'Tipo Persona',
                'Condición Fiscal', 'Monotributista', 'Categoría Mono',
                'Resp. Inscripto', 'Autónomo', 'Rel. Dependencia',
                'Domicilio Fiscal', 'Actividades', 'Impuestos Activos'
            ]
            ws.append_row(headers)
            # Format header row
            ws.format('A1:T1', {
                'textFormat': {'bold': True, 'foregroundColorStyle': {'rgbColor': {'red': 1, 'green': 1, 'blue': 1}}},
                'backgroundColor': {'red': 0, 'green': 0.34, 'blue': 0.7},
                'horizontalAlignment': 'CENTER'
            })
            ws.freeze(rows=1)
            
        return sh
    except Exception as e:
        # Fallback if specific sheet differs or fails
        print(f"Error accessing shared sheet: {e}")
        # If we can't search by name easily without listing all, we might fallback to create
        # But for now, let's Raise to see what happens or try to find by name if key fails
        raise e


def save_consultation(data):
    """
    Save a consultation result to Google Sheets.
    
    data should contain:
    - dni, sex, cuit
    - bcra: {name, situacion, deuda_total, entidades}
    - afip: {nombre, estado_clave, tipo_persona, condicion_fiscal, 
             is_monotributo, categoria_monotributo, is_responsable_inscripto,
             is_autonomo, is_relacion_dependencia, domicilio, actividades, impuestos}
    """
    sh = _get_or_create_spreadsheet()
    ws = sh.sheet1

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    bcra = data.get('bcra', {})
    afip = data.get('afip', {})

    # Format activities as comma-separated string
    actividades = ', '.join(afip.get('actividades', [])) if afip.get('actividades') else ''

    # Format active impuestos
    impuestos_list = afip.get('impuestos', [])
    impuestos_activos = ', '.join(
        imp.get('descripcion', '') for imp in impuestos_list if imp.get('estado') == 'Activo'
    ) if impuestos_list else ''

    row = [
        now,
        data.get('dni', ''),
        data.get('sex', ''),
        str(data.get('cuit', '')),
        # BCRA
        bcra.get('name', ''),
        str(bcra.get('situacion', '')),
        str(bcra.get('deuda_total', '')),
        str(bcra.get('entidades', '')),
        # AFIP
        afip.get('nombre', ''),
        afip.get('estado_clave', ''),
        afip.get('tipo_persona', ''),
        afip.get('condicion_fiscal', ''),
        'Sí' if afip.get('is_monotributo') else 'No',
        afip.get('categoria_monotributo', '') or '',
        'Sí' if afip.get('is_responsable_inscripto') else 'No',
        'Sí' if afip.get('is_autonomo') else 'No',
        'Sí' if afip.get('is_relacion_dependencia') else 'No',
        afip.get('domicilio', ''),
        actividades,
        impuestos_activos
    ]

    ws.append_row(row, value_input_option='USER_ENTERED')
    return sh.url


def get_spreadsheet_url():
    """Get the URL of the existing spreadsheet."""
    sid = _get_spreadsheet_id()
    if sid:
        return f'https://docs.google.com/spreadsheets/d/{sid}'
    return None
