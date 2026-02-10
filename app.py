from flask import Flask, render_template, request, jsonify
from pyBCRAdata.client import BCRAclient
from afip import Afip
import pandas as pd
import traceback
import os

app = Flask(__name__)

# Initialize BCRA Client
try:
    client = BCRAclient()
except Exception as e:
    print(f"Error initializing BCRA client: {e}")
    client = None

# Initialize AFIP SDK (Production mode with AFIP certificate)
AFIP_ACCESS_TOKEN = os.environ.get('AFIP_ACCESS_TOKEN', 'qGfm4QDkgugrJrxdw5YDHpfdrBhxwCYH4x3AcwgoavFCjfK4CWBD2lIfE3HjcpN3')
AFIP_CUIT = 20289107364  # Production CUIT

# Load cert and key from env vars (Render) or local files
afip_cert = os.environ.get('AFIP_CERT', None)
afip_key = os.environ.get('AFIP_KEY', None)

if not afip_cert:
    cert_path = os.path.join(os.path.dirname(__file__), 'comarfin.crt')
    if os.path.exists(cert_path):
        with open(cert_path, 'r') as f:
            afip_cert = f.read()

if not afip_key:
    key_path = os.path.join(os.path.dirname(__file__), 'comarfin.key')
    if os.path.exists(key_path):
        with open(key_path, 'r') as f:
            afip_key = f.read()

try:
    afip_config = {
        "CUIT": AFIP_CUIT,
        "access_token": AFIP_ACCESS_TOKEN,
        "production": True,
        "cert": afip_cert,
        "key": afip_key,
    }
    afip_client = Afip(afip_config)
    print("AFIP client initialized in PRODUCTION mode")
except Exception as e:
    print(f"Error initializing AFIP client: {e}")
    afip_client = None

def calculate_cuil(dni, gender):
    """
    Calculates the CUIL for a given DNI and Gender.
    Logic based on standard algorithm:
    - Prefix: 20 for Male, 27 for Female.
    - If conflict, potential fallback (logic can be complex, using basic standard here).
    - Checks: 5432765432
    """
    if not dni or not gender:
        return None
    
    dni = str(dni).strip()
    gender = gender.upper()
    
    if len(dni) > 8 or len(dni) < 7:
         # Assume if it's already 11 digits, it's a CUIL
         if len(dni) == 11:
             return dni
         return None

    # Pad DNI to 8 digits
    dni = dni.zfill(8)

    # Determine prefix
    if gender == 'M':
        prefix = '20'
    elif gender == 'F':
        prefix = '27'
    else:
        # Default/Business/Other - usually 30 but for individuals often 20/27 or 23
        # For 'X' or others, 23 is often used for generic/n-b
        prefix = '23' 
        
    base = prefix + dni
    
    # Calculate check digit
    multipliers = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    total = 0
    for i in range(10):
        total += int(base[i]) * multipliers[i]
        
    remainder = total % 11
    check_digit = 11 - remainder
    
    if check_digit == 11:
        check_digit = 0
    elif check_digit == 10:
        # Special case: logic varies. 
        # Standard: If calculated digit is 10, prefix usually changes (M->23, F->23).
        # We will try switching to 23 and recalculating for M/F.
        if gender in ['M', 'F'] and prefix != '23':
            prefix = '23'
            base = prefix + dni
            total = 0
            for i in range(10):
                total += int(base[i]) * multipliers[i]
            remainder = total % 11
            check_digit = 11 - remainder
            if check_digit == 11: check_digit = 0
            # If it's still 10, it's a rare edge case, but 23 usually resolves it.
        
    return f"{prefix}{dni}{check_digit}"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/check_score', methods=['POST'])
def check_score():
    if not client:
        return jsonify({'error': 'BCRA client not initialized'}), 500

    data = request.json
    dni = data.get('dni')
    sex = data.get('sex') # M, F, X
    
    # Optional fields for display or logging
    name = data.get('name')

    if not dni:
        return jsonify({'error': 'DNI is required'}), 400
        
    # Attempt to use as CUIT if passes length check or calculate from DNI
    final_cuit = dni
    if len(str(dni)) < 10:
        if not sex:
             return jsonify({'error': 'Sexo es requerido para calcular el CUIL desde el DNI.'}), 400
        
        calculated = calculate_cuil(dni, sex)
        if calculated:
            final_cuit = calculated
        else:
            return jsonify({'error': 'No se pudo calcular el CUIL. Verifique el DNI.'}), 400

    try:
        # Fetch data from BCRA using the calculated or provided CUIT
        result = client.debtors.debtors(identificacion=final_cuit)

        if isinstance(result, pd.DataFrame):
            if result.empty:
                 return jsonify({
                     'status': 'no_data', 
                     'message': f'No se encontraron datos para el CUIT {final_cuit}.',
                     'calculated_cuit': final_cuit
                 })
            
            # Convert DataFrame to list of dicts for JSON response
            records = result.to_dict(orient='records')
            
            # Calculate a "summary" status if there are multiple debts
            max_situation = 0
            for record in records:
                try:
                    sit = int(record.get('periodos_entidades_situacion', 1))
                    if sit > max_situation:
                        max_situation = sit
                except:
                    pass
            
            return jsonify({
                'status': 'success',
                'data': records,
                'summary_situation': max_situation,
                'calculated_cuit': final_cuit
            })
        elif isinstance(result, dict):
             # pyBCRA returns a dict when no data is found (404) or other API errors
             status_code = result.get('status', 0)
             error_msgs = result.get('errorMessages', [])
             
             if status_code == 404:
                 # 404 means no debts found - this is a VALID result (person has no credit issues)
                 return jsonify({
                     'status': 'no_data',
                     'message': f'No se encontraron deudas para el CUIT {final_cuit}. (Sin registros en Central de Deudores)',
                     'calculated_cuit': final_cuit
                 })
             else:
                 # Other API errors
                 return jsonify({
                     'error': f'Error del BCRA (código {status_code})',
                     'details': '; '.join(error_msgs) if error_msgs else str(result)
                 }), 500
        else:
             return jsonify({'error': 'Respuesta inesperada del BCRA', 'details': str(type(result))}), 500

    except Exception as e:
        with open("error.log", "a") as f:
            f.write(f"Error processing check_score for input {data}:\n")
            traceback.print_exc(file=f)
            f.write("\n" + "-"*20 + "\n")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/check_history', methods=['POST'])
def check_history():
    """Fetch 6-month debt history for a CUIT"""
    if not client:
        return jsonify({'error': 'BCRA client not initialized'}), 500

    data = request.json
    cuit = data.get('cuit')

    if not cuit:
        return jsonify({'error': 'CUIT is required'}), 400

    try:
        result = client.debtors.history(identificacion=cuit)

        if isinstance(result, pd.DataFrame):
            if result.empty:
                return jsonify({'status': 'no_history', 'message': 'Sin historial disponible.'})

            # Get last 6 periods (months)
            periods = sorted(result['periodos_periodo'].unique(), reverse=True)[:6]
            
            history_summary = []
            for period in periods:
                period_data = result[result['periodos_periodo'] == period]
                
                # Worst situation in this period
                worst_sit = 0
                try:
                    worst_sit = int(period_data['periodos_entidades_situacion'].max())
                except:
                    worst_sit = 0
                
                # Total debt in this period
                total_debt = 0
                try:
                    total_debt = float(period_data['periodos_entidades_monto'].sum())
                except:
                    total_debt = 0
                
                # Number of entities
                num_entities = len(period_data)
                
                # Format period YYYYMM -> YYYY-MM
                period_str = str(period)
                formatted_period = f"{period_str[:4]}-{period_str[4:]}" if len(period_str) == 6 else period_str
                
                history_summary.append({
                    'period': formatted_period,
                    'worst_situation': worst_sit,
                    'total_debt': total_debt,
                    'num_entities': num_entities
                })
            
            return jsonify({
                'status': 'success',
                'history': history_summary,
                'person_name': result['denominacion'].iloc[0] if 'denominacion' in result.columns else 'N/A'
            })

        elif isinstance(result, dict):
            status_code = result.get('status', 0)
            if status_code == 404:
                return jsonify({'status': 'no_history', 'message': 'Sin historial de deudas registrado.'})
            else:
                error_msgs = result.get('errorMessages', [])
                return jsonify({'error': f'Error del BCRA ({status_code})', 'details': '; '.join(error_msgs)}), 500
        else:
            return jsonify({'error': 'Respuesta inesperada', 'details': str(type(result))}), 500

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/check_afip', methods=['POST'])
def check_afip():
    """Query AFIP for taxpayer tax status (monotributo, IVA, etc.)"""
    if not afip_client:
        return jsonify({'error': 'AFIP client not initialized'}), 500

    data = request.json
    dni = data.get('dni')
    sex = data.get('sex')
    cuit = data.get('cuit')  # Can be provided directly

    # Calculate CUIT from DNI if not provided
    if not cuit:
        if not dni:
            return jsonify({'error': 'DNI o CUIT es requerido'}), 400
        if not sex:
            return jsonify({'error': 'Sexo es requerido para calcular el CUIL'}), 400
        cuit = calculate_cuil(dni, sex)
        if not cuit:
            return jsonify({'error': 'No se pudo calcular el CUIL'}), 400

    try:
        taxpayer = afip_client.RegisterInscriptionProof.getTaxpayerDetails(int(cuit))

        if not taxpayer:
            return jsonify({'status': 'no_data', 'message': 'No se encontraron datos en AFIP.', 'cuit': cuit})

        # Check for errorConstancia (partial data)
        if 'errorConstancia' in taxpayer and 'datosGenerales' not in taxpayer:
            error_data = taxpayer['errorConstancia']
            nombre = error_data.get('nombre', '')
            apellido = error_data.get('apellido', '')
            errors = error_data.get('error', [])
            return jsonify({
                'status': 'partial',
                'cuit': cuit,
                'nombre': f"{nombre} {apellido}".strip() or 'N/A',
                'errors': errors,
                'message': 'Datos parciales - la constancia tiene observaciones'
            })

        # Extract general data
        datos_gen = taxpayer.get('datosGenerales', {})
        nombre = datos_gen.get('nombre', '')
        apellido = datos_gen.get('apellido', '')
        razon_social = datos_gen.get('razonSocial', '')
        full_name = razon_social if razon_social else f"{nombre} {apellido}".strip()
        estado_clave = datos_gen.get('estadoClave', 'N/A')
        tipo_persona = datos_gen.get('tipoPersona', 'N/A')

        # Determine tax condition
        condition = 'Sin datos'
        category = None
        is_monotributo = False
        is_responsable_inscripto = False
        is_relacion_dependencia = False
        is_autonomo = False

        # Check monotributo
        datos_mono = taxpayer.get('datosMonotributo', {})
        if datos_mono:
            mono_impuestos = datos_mono.get('impuesto', [])
            for imp in mono_impuestos:
                if imp.get('idImpuesto') == 20 and imp.get('estadoImpuesto') == 'AC':
                    is_monotributo = True
                    cat_mono = datos_mono.get('categoriaMonotributo', {})
                    category = cat_mono.get('descripcionCategoria', 'N/A')
                    break

        # Check regimen general
        datos_rg = taxpayer.get('datosRegimenGeneral', {})
        if datos_rg:
            rg_impuestos = datos_rg.get('impuesto', [])
            for imp in rg_impuestos:
                desc = imp.get('descripcionImpuesto', '').upper()
                estado = imp.get('estadoImpuesto', '')
                if estado == 'AC':
                    if 'IVA' in desc and imp.get('idImpuesto') == 30:
                        is_responsable_inscripto = True
                    if 'AUTONOMO' in desc or 'AUTÓNOMO' in desc:
                        is_autonomo = True

        # Check activities for relacion de dependencia
        all_activities = []
        for section in ['datosMonotributo', 'datosRegimenGeneral']:
            sec_data = taxpayer.get(section, {})
            activities = sec_data.get('actividad', [])
            for act in activities:
                desc_act = act.get('descripcionActividad', '')
                all_activities.append(desc_act)
                if 'RELAC' in desc_act.upper() and 'DEPENDENCIA' in desc_act.upper():
                    is_relacion_dependencia = True

        # Determine condition label
        conditions = []
        if is_monotributo:
            conditions.append(f'Monotributista ({category})' if category else 'Monotributista')
        if is_responsable_inscripto:
            conditions.append('Responsable Inscripto')
        if is_relacion_dependencia:
            conditions.append('Relacion de Dependencia')
        if is_autonomo:
            conditions.append('Autonomo')

        # If no tax inscriptions found, indicate it clearly
        if not conditions:
            if not datos_mono and not datos_rg:
                condition = 'Sin inscripciones activas — Posible empleado en relación de dependencia, jubilado o sin actividad registrada'
            else:
                condition = 'Sin condicion activa detectada'
        else:
            condition = ' | '.join(conditions)

        # Get domicilio
        domicilio = datos_gen.get('domicilioFiscal', {})
        domicilio_str = ''
        if domicilio:
            parts = [domicilio.get('direccion', ''), domicilio.get('localidad', ''), 
                     domicilio.get('descripcionProvincia', ''), domicilio.get('codPostal', '')]
            domicilio_str = ', '.join(p for p in parts if p)

        # Collect all impuestos
        all_impuestos = []
        for section in ['datosMonotributo', 'datosRegimenGeneral']:
            sec_data = taxpayer.get(section, {})
            for imp in sec_data.get('impuesto', []):
                all_impuestos.append({
                    'descripcion': imp.get('descripcionImpuesto', 'N/A'),
                    'estado': 'Activo' if imp.get('estadoImpuesto') == 'AC' else 'Inactivo',
                    'periodo': imp.get('periodo', 'N/A')
                })

        return jsonify({
            'status': 'success',
            'cuit': cuit,
            'nombre': full_name,
            'estado_clave': estado_clave,
            'tipo_persona': tipo_persona,
            'condicion_fiscal': condition,
            'is_monotributo': is_monotributo,
            'is_responsable_inscripto': is_responsable_inscripto,
            'is_relacion_dependencia': is_relacion_dependencia,
            'is_autonomo': is_autonomo,
            'categoria_monotributo': category,
            'domicilio': domicilio_str,
            'actividades': list(set(all_activities))[:10],
            'impuestos': all_impuestos
        })

    except Exception as e:
        error_msg = str(e)
        if 'No existe persona' in error_msg:
            return jsonify({
                'status': 'not_found',
                'cuit': cuit,
                'message': f'No se encontro persona con CUIT {cuit} en AFIP'
            })
        traceback.print_exc()
        return jsonify({'error': error_msg}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
