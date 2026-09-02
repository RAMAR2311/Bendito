from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import joinedload

from decorators import admin_required
from models import (
    Expense,
    Product,
    ProductExchange,
    ProductVariant,
    Sale,
    SaleDetail,
    SalePayment,
    db,
    obtener_hora_bogota,
)

sales_bp = Blueprint('sales_bp', __name__)



@sales_bp.route('/nueva', methods=['GET', 'POST'])
@login_required # Importante: Te bloqueará el acceso si no hay current_user logeado (Flask-Login)
def procesar_venta():
    if request.method == 'GET':
        return render_template('sales/nueva.html')

    """
    Se espera que los datos vengan en el cuerpo de la petición (JSON)
    Ej: {'items': [{ 'product_id': 1, 'cantidad': 2, 'precio_final': 15.50}, ...], 'metodo_pago': 'transferencia'}
    """
    data = request.get_json()
    items = data.get('items', [])
    pagos_data = data.get('pagos', [])
    cliente_nombre = data.get('cliente', 'Consumidor Final')
    metodo_pago_legacy = data.get('metodo_pago', 'efectivo')
    
    if not items:
        return jsonify({'error': 'No se enviaron productos para la venta'}), 400

    # Si no se envían pagos en el nuevo formato, crear uno único con el método legacy
    if not pagos_data:
        pagos_data = [{'metodo_pago': metodo_pago_legacy, 'monto': None}]  # monto=None se llenará con el total

    try:
        # Determinar el método de pago principal (para la columna legacy de retrocompatibilidad)
        if len(pagos_data) == 1:
            metodo_pago_principal = pagos_data[0].get('metodo_pago', 'efectivo')
        else:
            metodo_pago_principal = 'mixto'

        # Manejar Fecha de Venta para registros de fechas anteriores
        fecha_venta_str = data.get('fecha_venta')
        fecha_venta_obj = obtener_hora_bogota()
        if fecha_venta_str:
            try:
                fecha_seleccionada = datetime.strptime(fecha_venta_str, '%Y-%m-%d').date()
                if fecha_seleccionada != fecha_venta_obj.date():
                    # Si no es hoy, combinamos la fecha seleccionada con la hora actual para conservar secuencialidad de hora de registro
                    fecha_venta_obj = datetime.combine(fecha_seleccionada, fecha_venta_obj.time())
            except ValueError:
                pass # Fallback silencioso a la hora actual si el formato falla

        # Calcular el siguiente consecutivo
        ultimo_consecutivo = db.session.query(func.max(Sale.consecutivo)).scalar() or 0
        siguiente_consecutivo = ultimo_consecutivo + 1

        nueva_venta = Sale(
            vendedor_id=current_user.id,
            consecutivo=siguiente_consecutivo,
            cliente_nombre=cliente_nombre,
            monto_total=Decimal('0.00'),
            metodo_pago=metodo_pago_principal,
            fecha_venta=fecha_venta_obj
        )
        db.session.add(nueva_venta)
        db.session.flush()

        monto_total = Decimal('0.00')

        for item in items:
            product_id = item.get('product_id')
            variant_id = item.get('variant_id') # Posible variante
            cantidad_vendida = int(item.get('cantidad', 0))
            precio_venta_final = Decimal(str(item.get('precio_final', '0.00')))
            es_manual = item.get('es_manual', False)

            if cantidad_vendida <= 0:
                raise ValueError("La cantidad vendida debe ser mayor a 0.")

            if es_manual:
                # Producto manual (prestado de otro local) — no descuenta stock
                nombre_manual = item.get('nombre_manual', 'Producto Externo')
                precio_costo_manual = Decimal(str(item.get('precio_costo', '0.00')))

                detalle = SaleDetail(
                    sale_id=nueva_venta.id,
                    product_id=None,
                    variant_id=None,
                    cantidad_vendida=cantidad_vendida,
                    precio_venta_final=precio_venta_final,
                    nombre_manual=nombre_manual,
                    precio_costo_manual=precio_costo_manual
                )
                db.session.add(detalle)
                monto_total += (precio_venta_final * cantidad_vendida)

                # Crear el gasto automático para descontar el ingreso prestado del balance final
                if precio_costo_manual > 0:
                    gasto_externo = Expense(
                        usuario_id=current_user.id,
                        tipo_gasto='Gasto Diario',
                        categoria='Pago Prod. Externo',
                        descripcion=f"Pago por producto manual prestado: {nombre_manual}",
                        monto=(precio_costo_manual * cantidad_vendida),
                        fecha_gasto=fecha_venta_obj
                    )
                    db.session.add(gasto_externo)
            else:
                # Producto del inventario propio
                producto = Product.query.with_for_update().get(product_id)
                
                if not producto:
                    raise ValueError(f"El producto con ID {product_id} no existe.")

                if variant_id:
                    variante = ProductVariant.query.with_for_update().get(variant_id)
                    if not variante:
                        raise ValueError(f"La variante con ID {variant_id} no existe.")
                    if cantidad_vendida > variante.cantidad_stock:
                        raise ValueError(f"Stock insuficiente para la variante '{variante.nombre_variante}' de '{producto.nombre}'. Solicitado: {cantidad_vendida}, Disponible: {variante.cantidad_stock}.")
                    variante.cantidad_stock -= cantidad_vendida
                else:
                    if cantidad_vendida > producto.cantidad_stock:
                        raise ValueError(f"Stock insuficiente para el producto '{producto.nombre}'. Solicitado: {cantidad_vendida}, Disponible: {producto.cantidad_stock}.")
                    producto.cantidad_stock -= cantidad_vendida


                detalle = SaleDetail(
                    sale_id=nueva_venta.id,
                    product_id=producto.id,
                    variant_id=variant_id,
                    cantidad_vendida=cantidad_vendida,
                    precio_venta_final=precio_venta_final
                )
                db.session.add(detalle)
                
                monto_total += (precio_venta_final * cantidad_vendida)

        nueva_venta.monto_total = monto_total

        # Registrar los pagos mixtos en la tabla sale_payments
        total_pagos = Decimal('0.00')
        for pago_info in pagos_data:
            metodo = pago_info.get('metodo_pago', 'efectivo')
            monto_pago = pago_info.get('monto')
            
            if monto_pago is None:
                # Si solo hay un pago sin monto explícito, asignar el total completo
                monto_pago = monto_total
            else:
                monto_pago = Decimal(str(monto_pago))
            
            if monto_pago <= 0:
                raise ValueError(f"El monto del pago por '{metodo}' debe ser mayor a 0.")
            
            pago = SalePayment(
                sale_id=nueva_venta.id,
                metodo_pago=metodo,
                monto=monto_pago
            )
            db.session.add(pago)
            total_pagos += monto_pago

        # Validar que la suma de pagos cubra el total de la venta
        if total_pagos != monto_total:
            raise ValueError(f"La suma de los pagos (${total_pagos}) no coincide con el total de la venta (${monto_total}). Diferencia: ${monto_total - total_pagos}.")

        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': 'Venta registrada e inventario descontado con éxito.',
            'sale_id': nueva_venta.id,
            'total': str(monto_total)
        }), 201

    except ValueError as val_err:
        db.session.rollback()
        return jsonify({'error': str(val_err)}), 400
        
    except Exception:
        db.session.rollback()
        return jsonify({'error': 'Ocurrió un error interno al procesar la venta.'}), 500

# Endpoint API asíncrono para el escáner del Punto de Venta
@sales_bp.route('/api/producto/<path:sku>', methods=['GET'])
@login_required
def api_buscar_producto(sku):
    producto = Product.query.filter_by(sku=sku, tipo_inventario='tienda').first()
    
    if not producto:
        return jsonify({'error': 'Código SKU no encontrado en el sistema'}), 404
        
    return jsonify({
        'id': producto.id,
        'nombre': producto.nombre,
        'sku': producto.sku,
        'cantidad_stock': producto.total_stock,
        'precio_minimo': float(producto.precio_minimo),
        'precio_limite': float(producto.precio_costo) if current_user.rol == 'admin' else float(producto.precio_minimo),
        'precio_sugerido': float(producto.precio_sugerido),
        'variantes': [{"id": v.id, "nombre": v.nombre_variante, "stock": v.cantidad_stock, "precio_minimo": float(v.precio_minimo or producto.precio_minimo), "precio_limite": float(v.precio_costo or producto.precio_costo) if current_user.rol == 'admin' else float(v.precio_minimo or producto.precio_minimo), "precio_sugerido": float(v.precio_sugerido or producto.precio_sugerido)} for v in producto.variantes]
    })

# Ruta para la Impresión del formato Térmico (Ticket)
@sales_bp.route('/recibo/<int:sale_id>', methods=['GET'])
@login_required # Proteger confidencialidad del cajero
def imprimir_ticket(sale_id):
    # Regla: Retorna 404 si alguien ingresa un ID falso
    venta = Sale.query.get_or_404(sale_id)
    return render_template('sales/ticket.html', venta=venta)

# Endpoint Historial de Ventas (Administradores)
@sales_bp.route('/historial', methods=['GET'])
@login_required
@admin_required
def historial():
    # Calcular el valor exacto de 'HOY' en Bogotá
    hoy_bogota = obtener_hora_bogota().strftime('%Y-%m-%d')
    
    # Si existen los args, los usa, de lo contrario colapsa a HOY por defecto
    fecha_inicio = request.args.get('fecha_inicio', hoy_bogota)
    fecha_fin = request.args.get('fecha_fin', hoy_bogota)
    
    # Optimización: eager loading (evita N+1 con joinedload)
    query = Sale.query.options(joinedload(Sale.vendedor))
    
    # Motor de búsqueda por Rango Restricto
    if fecha_inicio:
        inicio_dt = datetime.strptime(fecha_inicio, '%Y-%m-%d')
        query = query.filter(Sale.fecha_venta >= inicio_dt)
        
    if fecha_fin:
        fin_dt = datetime.strptime(fecha_fin, '%Y-%m-%d')
        # Sumar 1 día matemáticamente para incluir los registros hasta las 23:59:59 del último día
        query = query.filter(Sale.fecha_venta < fin_dt + timedelta(days=1))
        
    ventas = query.order_by(Sale.fecha_venta.desc()).all()
    
    # Auditar y cruzar sumatorios de métricas de pago
    # Sistema híbrido: usa SalePayment si existe, caso contrario cae al metodo_pago legacy
    total_efectivo = Decimal(0)
    total_nequi = Decimal(0)
    total_bancolombia = Decimal(0)
    total_daviplata = Decimal(0)
    total_tarjeta = Decimal(0)
    total_credito = Decimal(0)
    total_transferencia_legacy = Decimal(0)
    total_mixto = 0  # Contador de ventas con pago mixto

    for v in ventas:
        if v.pagos:  # Pagos nuevos con tabla sale_payments
            for pago in v.pagos:
                if pago.metodo_pago == 'efectivo':
                    total_efectivo += pago.monto
                elif pago.metodo_pago == 'nequi':
                    total_nequi += pago.monto
                elif pago.metodo_pago == 'bancolombia':
                    total_bancolombia += pago.monto
                elif pago.metodo_pago == 'daviplata':
                    total_daviplata += pago.monto
                elif pago.metodo_pago == 'tarjeta':
                    total_tarjeta += pago.monto
                elif pago.metodo_pago == 'credito':
                    total_credito += pago.monto
                elif pago.metodo_pago == 'transferencia':
                    total_transferencia_legacy += pago.monto
            if len(v.pagos) > 1:
                total_mixto += 1
        else:  # Retrocompatibilidad con ventas antiguas sin SalePayment
            if v.metodo_pago == 'efectivo':
                total_efectivo += v.monto_total
            elif v.metodo_pago == 'nequi':
                total_nequi += v.monto_total
            elif v.metodo_pago == 'bancolombia':
                total_bancolombia += v.monto_total
            elif v.metodo_pago == 'daviplata':
                total_daviplata += v.monto_total
            elif v.metodo_pago == 'tarjeta':
                total_tarjeta += v.monto_total
            elif v.metodo_pago == 'credito':
                total_credito += v.monto_total
            elif v.metodo_pago == 'transferencia':
                total_transferencia_legacy += v.monto_total

    # Envío al Engine de HTML
    return render_template('sales/historial.html', 
                           ventas=ventas, 
                           total_efectivo=total_efectivo,
                           total_nequi=total_nequi,
                           total_bancolombia=total_bancolombia,
                           total_daviplata=total_daviplata,
                           total_tarjeta=total_tarjeta,
                           total_credito=total_credito,
                           total_transferencia_legacy=total_transferencia_legacy,
                           total_mixto=total_mixto,
                           fecha_inicio=fecha_inicio,
                           fecha_fin=fecha_fin)


# Endpoint para Anular/Eliminar Venta Histórica
@sales_bp.route('/eliminar/<int:sale_id>', methods=['POST'])
@login_required
@admin_required
def eliminar_venta(sale_id):
    venta = Sale.query.get_or_404(sale_id)
    
    try:
        # Revertir Stock
        for detalle in venta.detalles:
            if detalle.variant_id:
                variante = ProductVariant.query.with_for_update().get(detalle.variant_id)
                if variante:
                    variante.cantidad_stock += detalle.cantidad_vendida
            else:
                producto = Product.query.with_for_update().get(detalle.product_id)
                if producto:
                    producto.cantidad_stock += detalle.cantidad_vendida
                    
        # Eliminar Venta y Detalles (Cascada)
        db.session.delete(venta)
        db.session.commit()
        flash('Venta anulada y stock devuelto exitosamente.', 'success')
        
    except Exception:
        db.session.rollback()
        flash('Ocurrió un error al anular la venta.', 'danger')
        
    return redirect(url_for('sales_bp.historial'))

# Endpoint Catálogo Estricto de solo vista para Operarios
@sales_bp.route('/catalogo', methods=['GET'])
@login_required 
def catalogo():
    query_str = request.args.get('q', '').strip()
    
    if query_str:
        # Motor de similitud Case-Insensitive (Like)
        search_term = f"%{query_str}%"
        productos = Product.query.filter_by(tipo_inventario='tienda').filter(
            or_(
                Product.sku.ilike(search_term), 
                Product.nombre.ilike(search_term)
            )
        ).limit(50).all()
    else:
        # Límite pasivo de 50 ítems para ahorrar memoria RAM de BD en carga inicial
        productos = Product.query.filter_by(tipo_inventario='tienda').limit(50).all()
        
    return render_template('sales/catalogo.html', productos=productos, q=query_str)


# ========================================================
# ====== NUEVO MÓDULO: CAJA RÁPIDA VISUAL ======
# ========================================================
@sales_bp.route('/pos_visual', methods=['GET'])
@login_required
def pos_visual():
    # Obtener todos los productos de la tienda con stock > 0 para agilizar la venta visual
    # Usamos joinedload para traer las variantes en la misma consulta
    productos = Product.query.options(joinedload(Product.variantes)).filter_by(tipo_inventario='tienda').all()
    
    # Dado que no hay un campo formal de categoría en el modelo actual, 
    # inferimos las categorías a partir de la primera palabra del nombre del producto (ej: Saco, Buzo, Pantalón)
    categorias_set = set()
    for p in productos:
        if p.nombre:
            primera_palabra = p.nombre.strip().split(' ')[0].capitalize()
            # Filtro básico para no llenar de categorías basura (palabras muy cortas)
            if len(primera_palabra) > 2:
                categorias_set.add(primera_palabra)
                
    categorias = sorted(categorias_set)
    
    # Pre-estructurar los datos para enviarlos fácilmente como JSON al frontend
    productos_data = []
    for p in productos:
        cat_inferida = p.nombre.strip().split(' ')[0].capitalize() if p.nombre else 'Otros'
        
        # Agregar el producto base
        item = {
            'id': p.id,
            'sku': p.sku,
            'nombre': p.nombre,
            'categoria': cat_inferida,
            'precio_final': float(p.precio_sugerido),
            'cantidad_stock': p.cantidad_stock,
            'imagen': p.imagen,
            'es_variante': False,
            'variantes': []
        }
        
        # Agregar variantes si tiene
        if p.variantes:
            for v in p.variantes:
                item['variantes'].append({
                    'id': v.id,
                    'nombre_variante': v.nombre_variante,
                    'precio_final': float(v.precio_sugerido or p.precio_sugerido),
                    'cantidad_stock': v.cantidad_stock
                })
                
        productos_data.append(item)

    return render_template('sales/pos_visual.html', categorias=categorias, productos_data=productos_data)

# ========================================================
# ====== API ENDPOINTS PARA GESTIÓN DE CAMBIOS ======
# ========================================================

@sales_bp.route('/api/search-invoices', methods=['GET'])
@login_required
def search_invoices():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    
    # Buscar por Consecutivo (Ticket #) o cliente
    invoices = Sale.query.filter(
        or_(
            cast(Sale.consecutivo, String).ilike(f"%{q}%"),
            Sale.cliente_nombre.ilike(f"%{q}%")
        )
    ).order_by(Sale.fecha_venta.desc()).limit(10).all()
    
    return jsonify([{
        'id': inv.id,
        'factura': f"{inv.consecutivo:05d}" if inv.consecutivo else f"{inv.id:05d}",
        'cliente': inv.cliente_nombre or 'Consumidor Final',
        'fecha': inv.fecha_venta.strftime('%d/%m/%Y'),
        'total': float(inv.monto_total) 
    } for inv in invoices])

@sales_bp.route('/api/invoice-details/<int:id>', methods=['GET'])
@login_required
def invoice_details(id):
    sale = Sale.query.get_or_404(id)
    details = []
    for d in sale.detalles:
        # Manejar nombre para productos normales y manuales
        nombre_item = d.producto.nombre if d.producto else d.nombre_manual
        
        details.append({
            'id': d.id,
            'product_id': d.product_id,
            'variant_id': d.variant_id,
            'nombre': nombre_item or 'Producto sin nombre',
            'variante': d.variante.nombre_variante if d.variante else 'Base',
            'cantidad': d.cantidad_vendida,
            'precio_final': float(d.precio_venta_final)
        })
    return jsonify(details)

@sales_bp.route('/api/search-stock', methods=['GET'])
@login_required
def search_stock():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    
    search_term = f"%{q}%"
    
    # Buscar productos base que no tienen variantes
    products_base = Product.query.filter_by(tipo_inventario='tienda').filter(
        or_(Product.sku.ilike(search_term), Product.nombre.ilike(search_term))
    ).all()
    
    results = []
    for p in products_base:
        if not p.variantes:
            results.append({
                'id': p.id,
                'variant_id': None,
                'sku': p.sku,
                'nombre': p.nombre,
                'variante': None,
                'precio': float(p.precio_sugerido),
                'stock': p.cantidad_stock
            })
        else:
            for v in p.variantes:
                # Búsqueda más flexible
                if q.lower() in v.nombre_variante.lower() or q.lower() in p.nombre.lower() or q.lower() in p.sku.lower():
                    results.append({
                        'id': p.id,
                        'variant_id': v.id,
                        'sku': p.sku,
                        'nombre': p.nombre,
                        'variante': v.nombre_variante,
                        'precio': float(v.precio_sugerido or p.precio_sugerido),
                        'stock': v.cantidad_stock
                    })
    
    return jsonify(results[:20])

@sales_bp.route('/api/process-exchange', methods=['POST'])
@login_required
def process_exchange():
    data = request.json
    try:
        sale_id = data.get('sale_id')
        returned = data.get('returned_item')
        new = data.get('new_item')
        reason = data.get('reason')
        perfect_state = data.get('perfect_state')
        excedente = Decimal(str(data.get('excedente', 0)).replace(',', ''))
        metodo_pago = data.get('metodo_pago')

        # 1. Reversar Stock del producto que regresa (si está OK)
        if perfect_state:
            if returned.get('variant_id'):
                var_ret = ProductVariant.query.get(returned['variant_id'])
                if var_ret: var_ret.cantidad_stock += 1
            else:
                prod_ret = Product.query.get(returned['product_id'])
                if prod_ret: prod_ret.cantidad_stock += 1

        # 2. Disminuir Stock del producto nuevo que sale
        if new.get('variant_id'):
            var_new = ProductVariant.query.get(new['variant_id'])
            if var_new: 
                if var_new.cantidad_stock < 1:
                    return jsonify({'error': 'No hay stock suficiente de la prenda nueva.'}), 400
                var_new.cantidad_stock -= 1
        else:
            prod_new = Product.query.get(new['id'])
            if prod_new:
                if prod_new.cantidad_stock < 1:
                    return jsonify({'error': 'No hay stock suficiente de la prenda nueva.'}), 400
                prod_new.cantidad_stock -= 1

        # 3. Registrar el Cambio en la base de datos
        exchange = ProductExchange(
            sale_id=sale_id,
            admin_id=current_user.id,
            product_returned_id=returned['product_id'],
            variant_returned_id=returned['variant_id'],
            product_new_id=new['id'],
            variant_new_id=new['variant_id'],
            reason=reason,
            excedente_pagado=excedente,
            metodo_pago_excedente=metodo_pago if excedente > 0 else None
        )
        db.session.add(exchange)
        
        db.session.commit()
        return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@sales_bp.route('/api/exchanges-history', methods=['GET'])
@login_required
def exchanges_history():
    exchanges = ProductExchange.query.order_by(ProductExchange.created_at.desc()).all()
    results = []
    for ex in exchanges:
        results.append({
            'fecha': ex.created_at.strftime('%d/%m/%Y %H:%M'),
            'sale_consecutivo': f"{ex.venta.consecutivo:05d}" if ex.venta.consecutivo else f"{ex.venta.id:05d}",
            'admin': ex.admin.nombre,
            'devuelto_nombre': ex.producto_devuelto.nombre if ex.producto_devuelto else 'Producto Externo',
            'devuelto_variante': ex.variante_devuelta.nombre_variante if ex.variante_devuelta else 'Base',
            'nuevo_nombre': ex.producto_nuevo.nombre if ex.producto_nuevo else 'Producto Externo',
            'nuevo_variante': ex.variante_nueva.nombre_variante if ex.variante_nueva else 'Base',
            'motivo': ex.reason,
            'excedente': float(ex.excedente_pagado)
        })
    return jsonify(results)


