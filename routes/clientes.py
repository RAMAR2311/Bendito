from datetime import datetime
from decimal import Decimal

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from decorators import admin_required
from models import (
    AbonoCredito,
    AcuerdoPago,
    ClienteCartera,
    DetalleFacturaCredito,
    FacturaCredito,
    MovimientoCajaCartera,
    Product,
    ProductVariant,
    Sale,
    SaleDetail,
    SalePayment,
    db,
    obtener_hora_bogota,
)

clientes_bp = Blueprint('clientes_bp', __name__)

@clientes_bp.route('/')
@login_required
@admin_required
def index():
    clientes = ClienteCartera.query.order_by(ClienteCartera.nombre_completo).all()
    return render_template('clientes/index.html', clientes=clientes)

@clientes_bp.route('/nuevo', methods=['POST'])
@login_required
@admin_required
def crear_cliente():
    nombre = request.form.get('nombre_completo')
    telefono = request.form.get('telefono')
    
    if not nombre or not telefono:
        flash('Nombre y teléfono son obligatorios.', 'danger')
        return redirect(url_for('clientes_bp.index'))
    
    nuevo_cliente = ClienteCartera(
        nombre_completo=nombre.strip(),
        telefono=telefono.strip()
    )
    db.session.add(nuevo_cliente)
    db.session.commit()
    flash(f'Cliente {nombre} registrado. Registre la primera factura a continuación.', 'success')
    return redirect(url_for('clientes_bp.perfil', id=nuevo_cliente.id, pos='true'))

@clientes_bp.route('/perfil/<int:id>')
@login_required
@admin_required
def perfil(id):
    cliente = ClienteCartera.query.get_or_404(id)
    from models import obtener_hora_bogota
    today = obtener_hora_bogota().date()
    return render_template('clientes/perfil.html', cliente=cliente, today=today)

@clientes_bp.route('/factura/nueva', methods=['POST'])
@login_required
@admin_required
def registrar_factura():
    data = request.get_json()
    cliente_id = data.get('cliente_id')
    items = data.get('items', [])
    abono_inicial = Decimal(str(data.get('abono_inicial', 0)).replace(',', ''))
    metodo_abono = data.get('metodo_abono', 'efectivo')
    
    if not cliente_id or not items:
        return jsonify({'error': 'Datos de factura incompletos.'}), 400
    
    try:
        total_factura = Decimal('0.00')
        nueva_factura = FacturaCredito(
            cliente_id=cliente_id,
            total_factura=0,
            saldo_pendiente=0
        )
        db.session.add(nueva_factura)
        db.session.flush()

        for item in items:
            product_id = item.get('product_id')
            variant_id = item.get('variant_id')
            nombre_manual = item.get('nombre_manual')
            cantidad = int(item['cantidad'])
            precio = Decimal(str(item['precio']))
            subtotal = precio * cantidad
            
            # Descuento de Inventario si es un producto del sistema
            if product_id:
                if variant_id:
                    variante = ProductVariant.query.with_for_update().get(variant_id)
                    if not variante or variante.cantidad_stock < cantidad:
                        raise ValueError(f"Stock insuficiente para variante {variant_id}")
                    variante.cantidad_stock -= cantidad
                else:
                    producto = Product.query.with_for_update().get(product_id)
                    if not producto or producto.cantidad_stock < cantidad:
                        raise ValueError(f"Stock insuficiente para producto {product_id}")
                    producto.cantidad_stock -= cantidad
            elif not nombre_manual:
                raise ValueError("El ítem debe tener un producto o un nombre manual.")
            
            detalle = DetalleFacturaCredito(
                factura_id=nueva_factura.id,
                producto_id=product_id if product_id else None,
                variant_id=variant_id,
                cantidad=cantidad,
                precio_unitario=precio,
                subtotal=subtotal,
                nombre_manual=nombre_manual
            )
            db.session.add(detalle)
            total_factura += subtotal
        
        nueva_factura.total_factura = total_factura
        nueva_factura.saldo_pendiente = total_factura - abono_inicial
        
        # Procesar Abono Inicial si existe
        if abono_inicial > 0:
            nuevo_abono = AbonoCredito(
                factura_id=nueva_factura.id,
                monto_abono=abono_inicial
            )
            db.session.add(nuevo_abono)
            db.session.flush()
            
            # Registro en Caja
            mov_caja = MovimientoCajaCartera(
                monto=abono_inicial,
                concepto=f"Abono Inicial Factura #{nueva_factura.id} - Cliente ID {cliente_id} ({metodo_abono.capitalize()})",
                abono_id=nuevo_abono.id
            )
            db.session.add(mov_caja)

        # Procesar Acuerdos de Pago (NUEVO)
        acuerdos_data = data.get('acuerdos', [])
        for ac in acuerdos_data:
            if ac.get('fecha'):
                nuevo_acuerdo = AcuerdoPago(
                    factura_id=nueva_factura.id,
                    fecha_acordada=datetime.strptime(ac['fecha'], '%Y-%m-%d').date(),
                    monto_esperado=Decimal(str(ac['monto']).replace(',', '')) if ac.get('monto') else None
                )
                db.session.add(nuevo_acuerdo)

        # --- INTEGRACIÓN CON HISTORIAL DE VENTAS (Sale) ---
        cliente_obj = ClienteCartera.query.get(cliente_id)
        nombre_cliente = cliente_obj.nombre_completo if cliente_obj else "Cliente Cartera"
        
        ultimo_consecutivo = db.session.query(db.func.max(Sale.consecutivo)).scalar() or 0
        nueva_venta = Sale(
            vendedor_id=current_user.id,
            consecutivo=ultimo_consecutivo + 1,
            cliente_nombre=nombre_cliente,
            monto_total=total_factura,
            metodo_pago='mixto' if abono_inicial > 0 else 'credito',
            fecha_venta=obtener_hora_bogota()
        )
        db.session.add(nueva_venta)
        db.session.flush()

        # Detalles de la Venta (para reportes de productos)
        for detail in nueva_factura.detalles:
            sd = SaleDetail(
                sale_id=nueva_venta.id,
                product_id=detail.producto_id,
                variant_id=detail.variant_id,
                cantidad_vendida=detail.cantidad,
                precio_venta_final=detail.precio_unitario,
                nombre_manual=detail.nombre_manual
            )
            db.session.add(sd)

        # Pagos de la Venta
        if abono_inicial > 0:
            p_abono = SalePayment(
                sale_id=nueva_venta.id,
                metodo_pago=metodo_abono,
                monto=abono_inicial
            )
            db.session.add(p_abono)
        
        if nueva_factura.saldo_pendiente > 0:
            p_credito = SalePayment(
                sale_id=nueva_venta.id,
                metodo_pago='credito',
                monto=nueva_factura.saldo_pendiente
            )
            db.session.add(p_credito)
        # --------------------------------------------------

        db.session.commit()
        return jsonify({'success': True, 'factura_id': nueva_factura.id})
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@clientes_bp.route('/factura/<int:id>/ticket')
@login_required
def ticket_credito(id):
    factura = FacturaCredito.query.get_or_404(id)
    return render_template('clientes/ticket_credito.html', factura=factura)

@clientes_bp.route('/abono/nuevo', methods=['POST'])
@login_required
@admin_required
def registrar_abono():
    factura_id = request.form.get('factura_id')
    monto = Decimal(str(request.form.get('monto_abono', 0)).replace(',', ''))
    metodo_pago = request.form.get('metodo_pago', 'efectivo')
    
    factura = FacturaCredito.query.get_or_404(factura_id)
    
    if monto <= 0 or monto > factura.saldo_pendiente:
        flash('Monto de abono inválido.', 'danger')
        return redirect(request.referrer)
    
    try:
        nuevo_abono = AbonoCredito(
            factura_id=factura_id,
            monto_abono=monto
        )
        db.session.add(nuevo_abono)
        db.session.flush()
        
        # Actualizar saldo
        factura.saldo_pendiente -= monto
        
        # Registro en Caja
        mov_caja = MovimientoCajaCartera(
            monto=monto,
            concepto=f"Abono Factura #{factura.id} - {factura.cliente.nombre_completo} ({metodo_pago.capitalize()})",
            abono_id=nuevo_abono.id
        )
        db.session.add(mov_caja)
        
        # --- INTEGRACIÓN CON HISTORIAL DE VENTAS (Sale) ---
        # Registramos el abono como una "venta" de tipo ingreso para que sume en el reporte diario
        ultimo_consecutivo = db.session.query(db.func.max(Sale.consecutivo)).scalar() or 0
        venta_abono = Sale(
            vendedor_id=current_user.id,
            consecutivo=ultimo_consecutivo + 1,
            cliente_nombre=factura.cliente.nombre_completo,
            monto_total=monto,
            metodo_pago=metodo_pago,
            fecha_venta=obtener_hora_bogota()
        )
        db.session.add(venta_abono)
        db.session.flush()

        # Detalle descriptivo (Manual para no afectar inventario)
        sd_abono = SaleDetail(
            sale_id=venta_abono.id,
            cantidad_vendida=1,
            precio_venta_final=monto,
            nombre_manual=f"ABONO Cartera - Factura #{factura.id}"
        )
        db.session.add(sd_abono)

        # Pago real
        p_abono = SalePayment(
            sale_id=venta_abono.id,
            metodo_pago=metodo_pago,
            monto=monto
        )
        db.session.add(p_abono)
        # --------------------------------------------------
            
        db.session.commit()
        flash('Abono registrado e ingresado a caja.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al registrar abono: {e!s}', 'danger')
        
    return redirect(url_for('clientes_bp.perfil', id=factura.cliente_id))

@clientes_bp.route('/abono/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_abono(id):
    abono = AbonoCredito.query.get_or_404(id)
    factura = abono.factura
    cliente_id = factura.cliente_id
    
    try:
        # Reversar saldo pendiente
        factura.saldo_pendiente += abono.monto_abono
        
        db.session.delete(abono)
        db.session.commit()
        flash('Abono eliminado y saldo reversado.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar abono: {e!s}', 'danger')
        
    return redirect(url_for('clientes_bp.perfil', id=cliente_id))

@clientes_bp.route('/acuerdo/nuevo', methods=['POST'])
@login_required
@admin_required
def crear_acuerdo():
    factura_id = request.form.get('factura_id')
    fecha_str = request.form.get('fecha_acordada')
    monto = request.form.get('monto_esperado')
    
    if not factura_id or not fecha_str:
        flash('Datos de acuerdo inválidos.', 'danger')
        return redirect(request.referrer)
    
    nuevo_acuerdo = AcuerdoPago(
        factura_id=factura_id,
        fecha_acordada=datetime.strptime(fecha_str, '%Y-%m-%d').date(),
        monto_esperado=float(monto.replace(',', '')) if monto else None
    )
    db.session.add(nuevo_acuerdo)
    db.session.commit()
    flash('Acuerdo de pago programado.', 'success')
    return redirect(request.referrer)

@clientes_bp.route('/editar/<int:id>', methods=['POST'])
@login_required
@admin_required
def editar_cliente(id):
    cliente = ClienteCartera.query.get_or_404(id)
    nombre = request.form.get('nombre_completo')
    telefono = request.form.get('telefono')
    
    if nombre: cliente.nombre_completo = nombre.strip()
    if telefono: cliente.telefono = telefono.strip()
    
    db.session.commit()
    flash('Información del cliente actualizada.', 'success')
    return redirect(url_for('clientes_bp.perfil', id=id))

@clientes_bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_cliente(id):
    cliente = ClienteCartera.query.get_or_404(id)
    
    try:
        # Al eliminar el cliente, el cascade se encarga de facturas, abonos, etc.
        # Pero debemos devolver el stock de todos los productos en todas sus facturas (solo los de inventario)
        for factura in cliente.facturas:
            for detalle in factura.detalles:
                if detalle.producto_id:
                    if detalle.variant_id:
                        v = ProductVariant.query.get(detalle.variant_id)
                        if v: v.cantidad_stock += detalle.cantidad
                    else:
                        p = Product.query.get(detalle.producto_id)
                        if p: p.cantidad_stock += detalle.cantidad
        
        db.session.delete(cliente)
        db.session.commit()
        flash(f'Cliente {cliente.nombre_completo} y todos sus registros eliminados.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar cliente: {e!s}', 'danger')
        
    return redirect(url_for('clientes_bp.index'))

@clientes_bp.route('/factura/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_factura(id):
    factura = FacturaCredito.query.get_or_404(id)
    cliente_id = factura.cliente_id
    
    try:
        # Devolver Stock
        for detalle in factura.detalles:
            if detalle.producto_id:
                if detalle.variant_id:
                    v = ProductVariant.query.get(detalle.variant_id)
                    if v: v.cantidad_stock += detalle.cantidad
                else:
                    p = Product.query.get(detalle.producto_id)
                    if p: p.cantidad_stock += detalle.cantidad
                
        db.session.delete(factura)
        db.session.commit()
        flash(f'Factura #{id} eliminada y stock devuelto.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar factura: {e!s}', 'danger')
        
    return redirect(url_for('clientes_bp.perfil', id=cliente_id))

@clientes_bp.route('/acuerdo/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_acuerdo(id):
    acuerdo = AcuerdoPago.query.get_or_404(id)
    db.session.delete(acuerdo)
    db.session.commit()
    flash('Acuerdo de pago eliminado.', 'info')
    return redirect(request.referrer)

@clientes_bp.route('/acuerdo/editar/<int:id>', methods=['POST'])
@login_required
@admin_required
def editar_acuerdo(id):
    acuerdo = AcuerdoPago.query.get_or_404(id)
    fecha_str = request.form.get('fecha_acordada')
    monto = request.form.get('monto_esperado')
    cumplido = request.form.get('cumplido') == 'on'
    
    if fecha_str:
        acuerdo.fecha_acordada = datetime.strptime(fecha_str, '%Y-%m-%d').date()
    acuerdo.monto_esperado = float(monto.replace(',', '')) if monto else None
    acuerdo.cumplido = cumplido
    
    db.session.commit()
    flash('Acuerdo de pago actualizado.', 'success')
    return redirect(request.referrer)

@clientes_bp.route('/factura/editar/<int:id>', methods=['GET'])
@login_required
@admin_required
def vista_editar_factura(id):
    factura = FacturaCredito.query.get_or_404(id)
    return render_template('clientes/editar_factura.html', factura=factura)

@clientes_bp.route('/factura/item/eliminar', methods=['POST'])
@login_required
@admin_required
def eliminar_item_factura():
    data = request.get_json()
    detalle_id = data.get('detalle_id')
    
    detalle = DetalleFacturaCredito.query.get_or_404(detalle_id)
    factura = detalle.factura
    
    try:
        # Devolver Stock solo si es un producto del inventario
        if detalle.producto_id:
            if detalle.variant_id:
                variante = ProductVariant.query.get(detalle.variant_id)
                if variante: variante.cantidad_stock += detalle.cantidad
            else:
                producto = Product.query.get(detalle.producto_id)
                if producto: producto.cantidad_stock += detalle.cantidad
            
        # Ajustar Totales
        monto_a_restar = detalle.subtotal
        factura.total_factura -= monto_a_restar
        factura.saldo_pendiente -= monto_a_restar
        
        db.session.delete(detalle)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@clientes_bp.route('/factura/item/agregar', methods=['POST'])
@login_required
@admin_required
def agregar_item_factura():
    data = request.get_json()
    factura_id = data.get('factura_id')
    product_id = data.get('product_id')
    variant_id = data.get('variant_id')
    nombre_manual = data.get('nombre_manual')
    cantidad = int(data.get('cantidad', 1))
    precio = Decimal(str(data.get('precio')).replace(',', ''))
    
    factura = FacturaCredito.query.get_or_404(factura_id)
    
    try:
        # Descontar Stock si es producto del sistema
        if product_id:
            if variant_id:
                variante = ProductVariant.query.with_for_update().get(variant_id)
                if not variante or variante.cantidad_stock < cantidad:
                    return jsonify({'error': 'Stock insuficiente'}), 400
                variante.cantidad_stock -= cantidad
            else:
                producto = Product.query.with_for_update().get(product_id)
                if not producto or producto.cantidad_stock < cantidad:
                    return jsonify({'error': 'Stock insuficiente'}), 400
                producto.cantidad_stock -= cantidad
        elif not nombre_manual:
            return jsonify({'error': 'Debe especificar un producto o un nombre manual'}), 400
            
        subtotal = precio * cantidad
        nuevo_detalle = DetalleFacturaCredito(
            factura_id=factura_id,
            producto_id=product_id if product_id else None,
            variant_id=variant_id,
            cantidad=cantidad,
            precio_unitario=precio,
            subtotal=subtotal,
            nombre_manual=nombre_manual
        )
        db.session.add(nuevo_detalle)
        
        # Ajustar Totales
        factura.total_factura += subtotal
        factura.saldo_pendiente += subtotal
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@clientes_bp.route('/factura/item/editar', methods=['POST'])
@login_required
@admin_required
def editar_item_factura():
    data = request.get_json()
    detalle_id = data.get('detalle_id')
    nueva_cantidad = int(data.get('cantidad'))
    nuevo_precio = Decimal(str(data.get('precio')).replace(',', ''))
    
    detalle = DetalleFacturaCredito.query.get_or_404(detalle_id)
    factura = detalle.factura
    
    try:
        # Calcular diferencia de stock
        diff_stock = nueva_cantidad - detalle.cantidad
        
        # Validar y modificar stock solo si es producto del sistema
        if detalle.producto_id:
            if diff_stock > 0:
                if detalle.variant_id:
                    variante = ProductVariant.query.with_for_update().get(detalle.variant_id)
                    if not variante or variante.cantidad_stock < diff_stock:
                        return jsonify({'error': 'Stock insuficiente'}), 400
                    variante.cantidad_stock -= diff_stock
                else:
                    producto = Product.query.with_for_update().get(detalle.producto_id)
                    if not producto or producto.cantidad_stock < diff_stock:
                        return jsonify({'error': 'Stock insuficiente'}), 400
                    producto.cantidad_stock -= diff_stock
            elif diff_stock < 0:
                # Devolver stock
                if detalle.variant_id:
                    variante = ProductVariant.query.get(detalle.variant_id)
                    if variante: variante.cantidad_stock += abs(diff_stock)
                else:
                    producto = Product.query.get(detalle.producto_id)
                    if producto: producto.cantidad_stock += abs(diff_stock)

        # Ajustar Totales de la Factura
        diferencia_total = (nuevo_precio * nueva_cantidad) - detalle.subtotal
        factura.total_factura += diferencia_total
        factura.saldo_pendiente += diferencia_total
        
        # Actualizar Detalle
        detalle.cantidad = nueva_cantidad
        detalle.precio_unitario = nuevo_precio
        detalle.subtotal = nuevo_precio * nueva_cantidad
        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@clientes_bp.route('/api/productos/buscar', methods=['GET'])
@login_required
def buscar_productos():
    q = request.args.get('q', '').strip()
    if not q: return jsonify([])
    
    search_term = f"%{q}%"
    productos = Product.query.filter_by(tipo_inventario='tienda').filter(
        or_(Product.sku.ilike(search_term), Product.nombre.ilike(search_term))
    ).all()
    
    results = []
    for p in productos:
        if p.variantes:
            for v in p.variantes:
                results.append({
                    'id': p.id,
                    'variant_id': v.id,
                    'nombre': f"{p.nombre} - {v.nombre_variante}",
                    'sku': p.sku,
                    'precio': float(v.precio_sugerido or p.precio_sugerido),
                    'stock': v.cantidad_stock
                })
        else:
            results.append({
                'id': p.id,
                'variant_id': None,
                'nombre': p.nombre,
                'sku': p.sku,
                'precio': float(p.precio_sugerido),
                'stock': p.cantidad_stock
            })
    return jsonify(results[:15])
