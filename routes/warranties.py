from datetime import timedelta

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from decorators import admin_required
from models import Sale, Warranty, db, obtener_hora_bogota

warranties_bp = Blueprint('warranties_bp', __name__)

# ---- API: Buscar factura y validar garantía ----
@warranties_bp.route('/api/sale/<int:sale_id>', methods=['GET'])
@login_required
def buscar_factura(sale_id):
    venta = Sale.query.get(sale_id)
    
    if not venta:
        return jsonify({'error': f'No existe ninguna factura con el ID #{sale_id}.'}), 404

    # Validación estricta de los 90 días
    hoy = obtener_hora_bogota()
    limite_garantia = venta.fecha_venta + timedelta(days=90)
    
    if hoy > limite_garantia:
        dias_expirado = (hoy - limite_garantia).days
        return jsonify({
            'error': f'El plazo de garantía de 3 meses ha expirado para esta factura. Venció hace {dias_expirado} día(s).',
            'expired': True
        }), 200

    # Construir payload de items
    items = []
    for detalle in venta.detalles:
        nombre = detalle.nombre_manual if detalle.nombre_manual else (detalle.producto.nombre if detalle.producto else 'Producto eliminado')
        items.append({
            'sale_detail_id': detalle.id,
            'product_id': detalle.product_id,
            'nombre_manual': detalle.nombre_manual,
            'nombre': nombre,
            'cantidad': detalle.cantidad_vendida,
            'precio': float(detalle.precio_venta_final)
        })

    return jsonify({
        'valid': True,
        'sale_id': venta.id,
        'fecha': venta.fecha_venta.strftime('%d/%m/%Y %H:%M'),
        'vendedor': venta.vendedor.nombre,
        'total': float(venta.monto_total),
        'items': items
    })

# ---- Vista principal del módulo ----
@warranties_bp.route('/', methods=['GET'])
@login_required
@admin_required
def index():
    garantias_recientes = Warranty.query.order_by(Warranty.created_at.desc()).limit(10).all()
    return render_template('admin/garantias.html', garantias_recientes=garantias_recientes)

# ---- POST: Procesar y guardar garantía ----
@warranties_bp.route('/nueva', methods=['POST'])
@login_required
@admin_required
def registrar():
    sale_id = request.form.get('sale_id', type=int)
    product_id = request.form.get('product_id', type=int) or None
    nombre_manual = request.form.get('nombre_manual')
    quantity = request.form.get('quantity', 1, type=int)
    reason = request.form.get('reason', '').strip()

    if not sale_id or not reason:
        flash('Datos incompletos. Factura y motivo son requeridos.', 'danger')
        return redirect(url_for('warranties_bp.index'))

    venta = Sale.query.get(sale_id)
    if not venta:
        flash(f'No se encontró la factura #{sale_id}.', 'danger')
        return redirect(url_for('warranties_bp.index'))

    # Revalidar los 90 días en el backend (seguridad)
    hoy = obtener_hora_bogota()
    if hoy > venta.fecha_venta + timedelta(days=90):
        flash('El plazo de garantía de 3 meses ha expirado. No se puede registrar.', 'danger')
        return redirect(url_for('warranties_bp.index'))

    nueva_garantia = Warranty(
        sale_id=sale_id,
        product_id=product_id,
        nombre_manual=nombre_manual if not product_id else None,
        quantity=quantity,
        reason=reason,
        resolution='Pendiente'
    )

    db.session.add(nueva_garantia)
    db.session.commit()

    flash(f'Garantía #{nueva_garantia.id} registrada correctamente.', 'success')
    return redirect(url_for('warranties_bp.ticket', warranty_id=nueva_garantia.id))

# ---- Ticket de garantía imprimible ----
@warranties_bp.route('/ticket/<int:warranty_id>', methods=['GET'])
@login_required
def ticket(warranty_id):
    garantia = Warranty.query.get_or_404(warranty_id)
    return render_template('sales/ticket_garantia.html', garantia=garantia)
