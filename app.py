from flask import Flask, render_template, request, jsonify
from pyBCRAdata.client import BCRAclient
import pandas as pd
import traceback

app = Flask(__name__)

# Initialize BCRA Client
try:
    client = BCRAclient()
except Exception as e:
    print(f"Error initializing BCRA client: {e}")
    client = None

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

if __name__ == '__main__':
    app.run(debug=True, port=5000)
