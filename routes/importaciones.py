from decimal import Decimal

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from decorators import admin_required
from models import (
    Importacion,
    Product,
    Provider,
    SaldoImportacion,
    SaleDetail,
    db,
    obtener_hora_bogota,
)

importaciones_bp = Blueprint('importaciones_bp', __name__)


def _calcular_ganancia_ventas():
    """Calcula la ganancia neta total de todas las ventas (precio_venta - precio_costo)."""
    detalles = db.session.query(SaleDetail, Product).join(
        Product, SaleDetail.product_id == Product.id
    ).all()
    ganancia = Decimal('0.0')
    for row in detalles:
        det = row.SaleDetail
        prod = row.Product
        costo = det.precio_costo_manual if det.precio_costo_manual else (prod.precio_costo or Decimal(0))
        ganancia += (Decimal(str(det.precio_venta_final)) - Decimal(str(costo))) * det.cantidad_vendida
    return ganancia


@importaciones_bp.route('/', methods=['GET'])
@login_required
@admin_required
def index():
    importaciones = Importacion.query.order_by(Importacion.fecha_registro.desc()).all()
    proveedores = Provider.query.order_by(Provider.nombre).all()

    # Calcular saldo dinámico
    saldo_obj = SaldoImportacion.obtener()
    capital_base = Decimal(str(saldo_obj.saldo_actual))

    # Descontar el total de todas las importaciones registradas
    total_importado = sum(
        Decimal(str(imp.valor_contenedor)) + Decimal(str(imp.valor_flete))
        for imp in importaciones
    )

    # Sumar ganancias generadas por ventas
    ganancia_ventas = _calcular_ganancia_ventas()

    saldo_disponible = capital_base - total_importado + ganancia_ventas

    return render_template(
        'importaciones/index.html',
        importaciones=importaciones,
        proveedores=proveedores,
        saldo_disponible=float(saldo_disponible),
        capital_base=float(capital_base),
        total_importado=float(total_importado),
        ganancia_ventas=float(ganancia_ventas)
    )


@importaciones_bp.route('/ajustar-saldo', methods=['POST'])
@login_required
@admin_required
def ajustar_saldo():
    """Permite al admin definir o ajustar el capital base de importaciones."""
    nuevo_saldo = request.form.get('nuevo_saldo', '').strip()
    try:
        nuevo_saldo = float(nuevo_saldo.replace(',', ''))
        if nuevo_saldo < 0:
            raise ValueError("Negativo")
    except ValueError:
        flash('Valor inválido para el saldo inicial.', 'danger')
        return redirect(url_for('importaciones_bp.index'))

    saldo_obj = SaldoImportacion.obtener()
    saldo_obj.saldo_actual = nuevo_saldo
    saldo_obj.ultima_actualizacion = obtener_hora_bogota()
    db.session.commit()
    flash(f'Capital base actualizado a ${nuevo_saldo:,.0f}.', 'success')
    return redirect(url_for('importaciones_bp.index'))


@importaciones_bp.route('/proveedores/crear', methods=['POST'])
@login_required
@admin_required
def crear_proveedor():
    """Crea un proveedor rápido desde el modal de importaciones."""
    nombre = request.form.get('nombre_proveedor', '').strip()
    empresa = request.form.get('empresa_proveedor', '').strip()
    telefono = request.form.get('telefono_proveedor', '').strip()

    if not nombre:
        flash('El nombre del proveedor es obligatorio.', 'danger')
        return redirect(url_for('importaciones_bp.index'))

    nuevo = Provider(
        nombre=nombre,
        empresa=empresa or None,
        telefono=telefono or None
    )
    db.session.add(nuevo)
    db.session.commit()
    flash(f'Proveedor "{nombre}" creado y disponible para seleccionar.', 'success')
    return redirect(url_for('importaciones_bp.index'))


@importaciones_bp.route('/crear', methods=['POST'])
@login_required
@admin_required
def crear():
    proveedor_id = request.form.get('proveedor_id')
    numero_contenedor = request.form.get('numero_contenedor')
    valor_contenedor = float(request.form.get('valor_contenedor', '0').replace(',', ''))
    valor_flete = float(request.form.get('valor_flete', '0').replace(',', ''))
    # El toggle/checkbox llega como 'on' si está marcado, o no llega si no está marcado
    pedido_completo = request.form.get('pedido_completo') == 'on'
    observaciones = request.form.get('observaciones')

    # Validaciones de Backend
    if not proveedor_id or not numero_contenedor:
        flash('Proveedor y número de contenedor son obligatorios.', 'danger')
        return redirect(url_for('importaciones_bp.index'))

    # Regla: observaciones obligatorias si pedido_completo es False
    if not pedido_completo and (not observaciones or not observaciones.strip()):
        flash('Si el pedido no llegó completo, las observaciones son obligatorias.', 'danger')
        return redirect(url_for('importaciones_bp.index'))

    nueva_importacion = Importacion(
        proveedor_id=proveedor_id,
        numero_contenedor=numero_contenedor.strip(),
        valor_contenedor=valor_contenedor,
        valor_flete=valor_flete,
        pedido_completo=pedido_completo,
        observaciones=observaciones.strip() if observaciones else None
    )

    db.session.add(nueva_importacion)
    db.session.commit()

    flash(f'Importación del contenedor #{numero_contenedor} registrada exitosamente.', 'success')
    return redirect(url_for('importaciones_bp.index'))


@importaciones_bp.route('/<int:id>', methods=['GET'])
@login_required
@admin_required
def detalle(id):
    importacion = Importacion.query.get_or_404(id)
    return jsonify({
        'id': importacion.id,
        'numero_contenedor': importacion.numero_contenedor,
        'valor_contenedor': float(importacion.valor_contenedor),
        'valor_flete': float(importacion.valor_flete),
        'pago_total': importacion.pago_total,
        'pedido_completo': importacion.pedido_completo,
        'observaciones': importacion.observaciones,
        'proveedor_id': importacion.proveedor_id,
        'proveedor_nombre': importacion.proveedor.nombre,
        'fecha': importacion.fecha_registro.strftime('%Y-%m-%d %H:%M')
    })


@importaciones_bp.route('/editar/<int:id>', methods=['POST'])
@login_required
@admin_required
def editar(id):
    importacion = Importacion.query.get_or_404(id)

    proveedor_id = request.form.get('proveedor_id')
    numero_contenedor = request.form.get('numero_contenedor')
    valor_contenedor = float(request.form.get('valor_contenedor', '0').replace(',', ''))
    valor_flete = float(request.form.get('valor_flete', '0').replace(',', ''))
    pedido_completo = request.form.get('pedido_completo') == 'on'
    observaciones = request.form.get('observaciones')

    if not proveedor_id or not numero_contenedor:
        flash('Proveedor y número de contenedor son obligatorios.', 'danger')
        return redirect(url_for('importaciones_bp.index'))

    if not pedido_completo and (not observaciones or not observaciones.strip()):
        flash('Si el pedido no llegó completo, las observaciones son obligatorias.', 'danger')
        return redirect(url_for('importaciones_bp.index'))

    importacion.proveedor_id = proveedor_id
    importacion.numero_contenedor = numero_contenedor.strip()
    importacion.valor_contenedor = valor_contenedor
    importacion.valor_flete = valor_flete
    importacion.pedido_completo = pedido_completo
    importacion.observaciones = observaciones.strip() if observaciones else None

    db.session.commit()
    flash(f'Importación #{numero_contenedor} actualizada correctamente.', 'success')
    return redirect(url_for('importaciones_bp.index'))
