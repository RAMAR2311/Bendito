from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import extract

from models import Expense, db, obtener_hora_bogota

gastos_bp = Blueprint('gastos_bp', __name__)

@gastos_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    if request.method == 'POST':
        tipo_gasto = request.form.get('tipo_gasto')
        
        # Restricción de seguridad backend: Vendedores sólo registran gastos operativos
        if current_user.rol != 'admin':
            tipo_gasto = 'Gasto Diario'
            
        categoria = request.form.get('categoria')
        descripcion = request.form.get('descripcion')
        monto = float(request.form.get('monto', '0').replace(',', ''))
        fecha_str = request.form.get('fecha_gasto')

        # Use the provided date or fallback to current datetime
        if fecha_str:
            try:
                # El front devuelve yy-mm-dd si se uso <input type="date">
                fecha_obj = datetime.strptime(fecha_str, '%Y-%m-%d')
            except ValueError:
                fecha_obj = obtener_hora_bogota()
        else:
            fecha_obj = obtener_hora_bogota()

        try:
            nuevo_gasto = Expense(
                usuario_id=current_user.id,
                tipo_gasto=tipo_gasto,
                categoria=categoria,
                descripcion=descripcion,
                monto=monto,
                fecha_gasto=fecha_obj
            )
            db.session.add(nuevo_gasto)
            db.session.commit()
            flash('Gasto registrado exitosamente.', 'success')
        except Exception:
            db.session.rollback()
            flash('Error al intentar registrar el gasto en la base de datos.', 'danger')
        
        return redirect(url_for('gastos_bp.index'))

    # GET Logic (Filters current month expenses)
    ahora = obtener_hora_bogota()
    mes_actual = ahora.month
    anio_actual = ahora.year

    # Consultamos registros del mes y del año actual
    query = Expense.query.filter(
        extract('month', Expense.fecha_gasto) == mes_actual,
        extract('year', Expense.fecha_gasto) == anio_actual
    )
    
    # Restricción de visibilidad: 
    # Si no es administrador, SOLAMENTE puede ver los gastos que haya registrado él mismo.
    if current_user.rol != 'admin':
        query = query.filter(Expense.usuario_id == current_user.id)
        
    gastos_mes = query.order_by(Expense.fecha_gasto.desc()).all()

    total_diarios = sum(g.monto for g in gastos_mes if g.tipo_gasto == 'Gasto Diario')
    total_indirectos = sum(g.monto for g in gastos_mes if g.tipo_gasto == 'Costo Indirecto')

    # Provide today's date formatted for HTML5 <input type="date">
    hoy_str = ahora.strftime('%Y-%m-%d')
    return render_template('gastos/index.html', gastos=gastos_mes, total_diarios=total_diarios, total_indirectos=total_indirectos, hoy=hoy_str)

@gastos_bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
def eliminar_gasto(id):
    gasto = Expense.query.get_or_404(id)
    
    # Restricción: Solo Admin o el usuario que lo creó
    if current_user.rol != 'admin' and gasto.usuario_id != current_user.id:
        flash('No tienes permiso para eliminar este gasto.', 'danger')
        return redirect(url_for('gastos_bp.index'))
    
    try:
        db.session.delete(gasto)
        db.session.commit()
        flash('Gasto eliminado correctamente.', 'success')
    except Exception:
        db.session.rollback()
        flash('Error al intentar eliminar el gasto.', 'danger')
        
    return redirect(url_for('gastos_bp.index'))
