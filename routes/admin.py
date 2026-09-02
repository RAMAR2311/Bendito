import random
import string
from datetime import timedelta
from decimal import Decimal

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.sql import func
from werkzeug.security import generate_password_hash

from decorators import admin_required
from models import (
    ClienteCartera,
    DynamicKey,
    Expense,
    FacturaCredito,
    Importacion,
    Loss,
    Maneo,
    Product,
    ProductVariant,
    Sale,
    SaleDetail,
    SalePayment,
    StockAdjustment,
    User,
    Warranty,
    db,
    obtener_hora_bogota,
)

admin_bp = Blueprint('admin_bp', __name__)

@admin_bp.route('/generar-clave', methods=['POST'])
@login_required
@admin_required
def generar_clave():
    # Generar un código alfanumérico random de 6 caracteres
    codigo = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    
    # Expiración: 10 minutos
    ahora = obtener_hora_bogota()
    expira = ahora + timedelta(minutes=10)
    
    nueva_clave = DynamicKey(
        key_code=codigo,
        admin_id=current_user.id,
        created_at=ahora,
        expires_at=expira
    )
    db.session.add(nueva_clave)
    db.session.commit()
    
    return jsonify({'success': True, 'codigo': codigo})

@admin_bp.route('/vendedores', methods=['GET', 'POST'])
@login_required
@admin_required
def vendedores():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email = request.form.get('email')
        telefono = request.form.get('telefono')
        password = request.form.get('password')
        
        # Se previene registrar vendedores con un mismo email para preservar la unicidad de las credenciales de acceso
        if User.query.filter_by(email=email).first():
            flash('Acción Denegada: Ese correo ya le pertenece a otro vendedor.', 'danger')
        else:
            try:
                # Se aplica un hash a la contraseña para evitar guardar texto plano, previniendo exposición en caso de brechas
                nuevo_vendedor = User(
                    nombre=nombre.strip(),
                    email=email.strip(),
                    telefono=telefono.strip() if telefono else None,
                    password_hash=generate_password_hash(password),
                    rol='vendedor'
                )
                db.session.add(nuevo_vendedor)
                db.session.commit()
                flash(f"¡Vendedor '{nombre}' registrado y autorizado para Cajas!", "success")
            except Exception:
                db.session.rollback()
                flash('Ocurrió un error en la base de datos al intentar registrar al vendedor.', 'danger')
            
        return redirect(url_for('admin_bp.vendedores'))
        
    # Se pasa la lista para poblar la tabla HTML de gestión de personal
    lista_vendedores = User.query.filter_by(rol='vendedor').order_by(User.nombre).all()
    return render_template('admin/vendedores.html', vendedores=lista_vendedores)

@admin_bp.route('/vendedores/editar/<int:id>', methods=['POST'])
@login_required
@admin_required
def editar_vendedor(id):
    vendedor = User.query.get_or_404(id)
    nombre = request.form.get('nombre')
    email = request.form.get('email')
    telefono = request.form.get('telefono')
    password = request.form.get('password')
    
    # Validar email único si cambió
    if email != vendedor.email and User.query.filter_by(email=email).first():
        flash('Error: El nuevo correo ya está en uso por otro usuario.', 'danger')
        return redirect(url_for('admin_bp.vendedores'))
 
    vendedor.nombre = nombre.strip()
    vendedor.email = email.strip()
    vendedor.telefono = telefono.strip() if telefono else None
    
    if password and password.strip():
        vendedor.password_hash = generate_password_hash(password)
        
    try:
        db.session.commit()
        flash(f'Vendedor "{nombre}" actualizado correctamente.', 'success')
    except Exception:
        db.session.rollback()
        flash('Error al actualizar el vendedor.', 'danger')
        
    return redirect(url_for('admin_bp.vendedores'))

@admin_bp.route('/vendedores/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_vendedor(id):
    vendedor = User.query.get_or_404(id)
    nombre = vendedor.nombre
    
    # No permitir que un admin borre a otro admin desde aquí o a sí mismo
    if vendedor.rol == 'admin':
        flash('No se pueden eliminar cuentas de administrador desde este panel.', 'danger')
        return redirect(url_for('admin_bp.vendedores'))

    try:
        db.session.delete(vendedor)
        db.session.commit()
        flash(f'Vendedor "{nombre}" eliminado con éxito.', 'success')
    except Exception:
        db.session.rollback()
        flash('Error: No se pudo eliminar el vendedor (puede tener ventas u operaciones registradas).', 'danger')
        
    return redirect(url_for('admin_bp.vendedores'))

@admin_bp.route('/dashboard')
@login_required
@admin_required
def dashboard():
    # Se obtienen métricas clave para que el administrador tenga un resumen rápido de las operaciones del negocio
    # Filtramos por 'tienda' para que coincida con lo que el usuario ve al entrar al Inventario desde aquí
    total_productos = Product.query.filter_by(tipo_inventario='tienda').count()
    
    # Se calcula el stock bajo considerando tanto productos planos como con variantes
    # Unificamos la regla a 3 unidades según el nuevo requerimiento de la boutique (Solo para Tienda)
    productos_tienda = Product.query.filter_by(tipo_inventario='tienda').all()
    productos_bajo_stock = sum(1 for p in productos_tienda if p.total_stock <= 3)
    
    maneos_activos = Maneo.query.filter_by(estado='PENDIENTE').count()
    
    # Se delega la suma al motor de base de datos para no saturar la memoria de la aplicación con registros a medida que crecen las ventas
    total_ventas = db.session.query(func.sum(Sale.monto_total)).scalar() or Decimal('0.0')
    
    # Cálculos para modulo de Pérdidas (Mermas) del mes actual
    hoy = obtener_hora_bogota()
    mes_actual = hoy.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    perdidas_valor = db.session.query(func.sum(Loss.cost_at_loss * Loss.quantity)).filter(Loss.date >= mes_actual).scalar() or Decimal('0.0')
    ventas_mes_actual = db.session.query(func.sum(Sale.monto_total)).filter(Sale.fecha_venta >= mes_actual).scalar() or Decimal('0.0')
    
    porcentaje_perdidas = 0
    if ventas_mes_actual > 0:
        porcentaje_perdidas = round(float((perdidas_valor / ventas_mes_actual) * 100), 2)
        
    # Cálculos modulo Garantías
    total_garantias_mes = Warranty.query.filter(Warranty.created_at >= mes_actual).count()
    garantias_pendientes = Warranty.query.filter(Warranty.resolution == 'Pendiente').count()

    # Cálculos modulo Importaciones
    importaciones_mes = Importacion.query.filter(Importacion.fecha_registro >= mes_actual).count()
    valor_importaciones_mes = db.session.query(func.sum(Importacion.valor_contenedor + Importacion.valor_flete)).filter(Importacion.fecha_registro >= mes_actual).scalar() or 0

    # Cálculos modulo Cartera (Clientes)
    total_cartera = db.session.query(func.sum(FacturaCredito.saldo_pendiente)).scalar() or 0
    clientes_cartera = ClienteCartera.query.all()
    clientes_mora = sum(1 for c in clientes_cartera if c.estado_cartera == 'En mora')
        
    return render_template('admin/dashboard.html', 
                           total_productos=total_productos,
                           productos_bajo_stock=productos_bajo_stock,
                           total_ventas=total_ventas,
                           maneos_activos=maneos_activos,
                           total_perdidas=perdidas_valor,
                           porcentaje_perdidas=porcentaje_perdidas,
                           total_garantias_mes=total_garantias_mes,
                           garantias_pendientes=garantias_pendientes,
                           importaciones_mes=importaciones_mes,
                           valor_importaciones_mes=valor_importaciones_mes,
                           total_cartera=total_cartera,
                           clientes_mora=clientes_mora)

# --- ENDPOINTS MODULO PERDIDAS ---
@admin_bp.route('/perdidas')
@login_required
@admin_required
def perdidas():
    ultimas_perdidas = Loss.query.order_by(Loss.date.desc()).all()
    return render_template('admin/perdidas.html', ultimas_perdidas=ultimas_perdidas)
@admin_bp.route('/api/product/<sku>')
@login_required
@admin_required
def api_producto_codigo(sku):
    producto = Product.query.filter_by(sku=sku.strip()).first()
    if not producto:
        return jsonify({'error': 'Producto no encontrado'}), 404
        
    return jsonify({
        'id': producto.id,
        'nombre': producto.nombre,
        'precio_costo': float(producto.precio_costo)
    })

@admin_bp.route('/perdidas/registrar', methods=['POST'])
@login_required
@admin_required
def registrar_perdida():
    product_id = request.form.get('product_id')
    cantidad = int(request.form.get('cantidad', 0))
    motivo = request.form.get('motivo', '').strip()
    
    if not product_id or cantidad <= 0:
        flash('Datos inválidos para registrar la pérdida.', 'danger')
        return redirect(url_for('admin_bp.dashboard'))
        
    producto = Product.query.get(product_id)
    if not producto:
        flash('El producto seleccionado no existe en el sistema.', 'danger')
        return redirect(url_for('admin_bp.dashboard'))
        
    if producto.cantidad_stock < cantidad:
        flash(f'Stock insuficiente. No puedes registrar una pérdida de {cantidad} si el sistema solo registra {producto.cantidad_stock} unidades.', 'danger')
        return redirect(url_for('admin_bp.dashboard'))
        
    try:
        # Descuento en stock central
        stock_anterior = producto.cantidad_stock
        producto.cantidad_stock -= cantidad
        
        costo_actual = producto.precio_costo
        
        # Registrar pérdida
        nueva_perdida = Loss(
            product_id=producto.id,
            user_id=current_user.id,
            quantity=cantidad,
            cost_at_loss=costo_actual,
            reason=motivo
        )
        db.session.add(nueva_perdida)
        
        # Rastreabilidad en Kardex
        ajuste = StockAdjustment(
            product_id=producto.id,
            admin_id=current_user.id,
            tipo_movimiento=f'Merma/Pérdida Registrada ({motivo})',
            stock_anterior=stock_anterior,
            stock_nuevo=producto.cantidad_stock
        )
        db.session.add(ajuste)
        
        db.session.commit()
        flash(f'Pérdida por {cantidad} unidades registrada con éxito. Inventario deducido.', 'success')
    except Exception:
        db.session.rollback()
        flash('No se pudo registrar la pérdida por un error interno.', 'danger')
        
    return redirect(url_for('admin_bp.perdidas'))
# ---------------------------------


@admin_bp.route('/maneos')
@login_required
def maneos():
    lista_maneos = Maneo.query.order_by(Maneo.fecha_prestamo.desc()).all()
    # Priorizar PENDIENTE temporalmente
    lista_maneos.sort(key=lambda m: 0 if m.estado == 'PENDIENTE' else 1)
    
    productos = Product.query.order_by(Product.nombre).all()
    return render_template('admin/maneos.html', maneos=lista_maneos, productos=productos)

@admin_bp.route('/maneos/prestar', methods=['POST'])
@login_required
def maneos_prestar():
    sku = request.form.get('sku')
    cantidad = int(request.form.get('cantidad', 0))
    local_vecino = request.form.get('local_vecino')
    variant_id_str = request.form.get('variant_id')

    if not sku:
        flash('Asegúrate de escanear o ingresar un SKU válido.', 'danger')
        return redirect(url_for('admin_bp.maneos'))

    producto = Product.query.filter_by(sku=sku.strip()).first()
    if not producto:
        flash(f'Error: El producto con SKU "{sku}" no existe en el catálogo.', 'danger')
        return redirect(url_for('admin_bp.maneos'))

    # Determinar si se seleccionó una variante
    variante = None
    if variant_id_str and variant_id_str.strip():
        variante = ProductVariant.query.get(int(variant_id_str))
        if not variante or variante.product_id != producto.id:
            flash('La subcategoría seleccionada no pertenece a este producto.', 'danger')
            return redirect(url_for('admin_bp.maneos'))
        
        if variante.cantidad_stock < cantidad:
            flash(f'Stock insuficiente en la subcategoría "{variante.nombre_variante}" para prestar {cantidad} uds. (Stock actual: {variante.cantidad_stock}).', 'danger')
            return redirect(url_for('admin_bp.maneos'))
    else:
        if producto.cantidad_stock < cantidad:
            flash(f'Stock insuficiente para prestar {cantidad} unids. (Stock actual: {producto.cantidad_stock}).', 'danger')
            return redirect(url_for('admin_bp.maneos'))

    try:
        # Descontar stock de la variante o del producto base
        if variante:
            stock_anterior = variante.cantidad_stock
            variante.cantidad_stock -= cantidad
        else:
            stock_anterior = producto.cantidad_stock
            producto.cantidad_stock -= cantidad

        nuevo_maneo = Maneo(
            product_id=producto.id,
            variant_id=variante.id if variante else None,
            local_vecino=local_vecino.strip(),
            cantidad=cantidad,
            estado='PENDIENTE'
        )
        db.session.add(nuevo_maneo)

        # Registro en el Kardex
        ajuste = StockAdjustment(
            product_id=producto.id,
            admin_id=current_user.id,
            tipo_movimiento=f'Préstamo (Maneo) a {local_vecino}' + (f' [{variante.nombre_variante}]' if variante else ''),
            stock_anterior=stock_anterior,
            stock_nuevo=variante.cantidad_stock if variante else producto.cantidad_stock
        )
        db.session.add(ajuste)

        db.session.commit()
        flash('Maneo registrado y stock descontado exitosamente.', 'success')
    except Exception:
        db.session.rollback()
        flash('Error al registrar el maneo. Transacción revertida.', 'danger')

    return redirect(url_for('admin_bp.maneos'))

@admin_bp.route('/maneos/facturar/<int:id>', methods=['POST'])
@login_required
def maneos_facturar(id):
    maneo = Maneo.query.get_or_404(id)
    if maneo.estado != 'PENDIENTE':
        flash('Este maneo ya fue resuelto.', 'warning')
        return redirect(url_for('admin_bp.maneos'))
    
    # Determinar precios según variante o producto base
    if maneo.variante:
        precio_sugerido_ref = float(maneo.variante.precio_sugerido or maneo.producto.precio_sugerido)
        precio_costo_ref = float(maneo.variante.precio_costo or maneo.producto.precio_costo)
        precio_minimo_ref = float(maneo.variante.precio_minimo or maneo.producto.precio_minimo)
    else:
        precio_sugerido_ref = float(maneo.producto.precio_sugerido)
        precio_costo_ref = float(maneo.producto.precio_costo)
        precio_minimo_ref = float(maneo.producto.precio_minimo)

    precio_venta = float(request.form.get('precio_venta', str(precio_sugerido_ref)).replace(',', ''))
    cantidad_vendida = int(request.form.get('cantidad_vendida', maneo.cantidad))

    if cantidad_vendida <= 0 or cantidad_vendida > maneo.cantidad:
        flash(f'Operación rechazada: La cantidad vendida ({cantidad_vendida}) es inválida.', 'danger')
        return redirect(url_for('admin_bp.maneos'))

    precio_limite = precio_costo_ref if current_user.rol == 'admin' else precio_minimo_ref

    if float(precio_venta) < float(precio_limite):
        flash(f'Operación rechazada: El precio ingresado (${precio_venta}) es menor al límite autorizado para tu perfil de usuario (${precio_limite}).', 'danger')
        return redirect(url_for('admin_bp.maneos'))

    try:
        cantidad_no_vendida = maneo.cantidad - cantidad_vendida

        maneo.estado = 'FACTURADO'
        maneo.fecha_resolucion = obtener_hora_bogota()

        # Si hubo un cobro parcial, las unidades restantes vuelven al inventario
        if cantidad_no_vendida > 0:
            if maneo.variante:
                stock_anterior = maneo.variante.cantidad_stock
                maneo.variante.cantidad_stock += cantidad_no_vendida
                stock_nuevo = maneo.variante.cantidad_stock
            else:
                stock_anterior = maneo.producto.cantidad_stock
                maneo.producto.cantidad_stock += cantidad_no_vendida
                stock_nuevo = maneo.producto.cantidad_stock

            variante_label = f' [{maneo.variante.nombre_variante}]' if maneo.variante else ''
            ajuste_retorno = StockAdjustment(
                product_id=maneo.product_id,
                admin_id=current_user.id,
                tipo_movimiento=f'Dev. Parcial de Maneo ({maneo.local_vecino}){variante_label}',
                stock_anterior=stock_anterior,
                stock_nuevo=stock_nuevo
            )
            db.session.add(ajuste_retorno)
            
            # Actualizamos la cantidad del maneo a la realmente facturada para que el historial sea claro
            maneo.cantidad = cantidad_vendida

        metodo_pago_seleccionado = request.form.get('metodo_pago', 'efectivo')
        
        # Registrar la venta real del Maneo
        nueva_venta = Sale(
            vendedor_id=current_user.id,
            monto_total=(precio_venta * cantidad_vendida),
            metodo_pago=metodo_pago_seleccionado
        )
        db.session.add(nueva_venta)
        db.session.flush() # forzar DB a darnos un ID para nueva_venta
        
        detalle = SaleDetail(
            sale_id=nueva_venta.id,
            product_id=maneo.product_id,
            variant_id=maneo.variant_id,
            cantidad_vendida=cantidad_vendida,
            precio_venta_final=precio_venta
        )
        db.session.add(detalle)

        # Registrar el pago en SalePayment para consistencia con pagos mixtos
        pago = SalePayment(
            sale_id=nueva_venta.id,
            metodo_pago=metodo_pago_seleccionado,
            monto=(precio_venta * cantidad_vendida)
        )
        db.session.add(pago)
        
        db.session.commit()

        if cantidad_no_vendida > 0:
            flash(f'Maneo facturado parcialmente. Se registró la venta de ${precio_venta * cantidad_vendida} y se devolvieron {cantidad_no_vendida} uds al inventario.', 'success')
        else:
            flash(f'Maneo facturado totalmente. Se registró la venta de ${precio_venta * cantidad_vendida} en la caja.', 'success')
    except Exception:
        db.session.rollback()
        flash('Error al facturar el maneo.', 'danger')

    return redirect(url_for('admin_bp.maneos'))

@admin_bp.route('/maneos/devolver/<int:id>', methods=['POST'])
@login_required
def maneos_devolver(id):
    maneo = Maneo.query.get_or_404(id)
    if maneo.estado != 'PENDIENTE':
        flash('Este maneo ya fue resuelto.', 'warning')
        return redirect(url_for('admin_bp.maneos'))

    cantidad_devuelta = int(request.form.get('cantidad_devuelta', maneo.cantidad))

    if cantidad_devuelta <= 0:
        flash('La cantidad a devolver debe ser mayor a 0.', 'danger')
        return redirect(url_for('admin_bp.maneos'))

    if cantidad_devuelta > maneo.cantidad:
        flash(f'No puedes devolver más de {maneo.cantidad} unidades (las que están prestadas).', 'danger')
        return redirect(url_for('admin_bp.maneos'))

    try:
        # Devolver stock a la variante o al producto base
        if maneo.variante:
            stock_anterior = maneo.variante.cantidad_stock
            maneo.variante.cantidad_stock += cantidad_devuelta
            stock_nuevo = maneo.variante.cantidad_stock
        else:
            stock_anterior = maneo.producto.cantidad_stock
            maneo.producto.cantidad_stock += cantidad_devuelta
            stock_nuevo = maneo.producto.cantidad_stock

        variante_label = f' [{maneo.variante.nombre_variante}]' if maneo.variante else ''

        # Registro en el Kardex del retorno
        ajuste = StockAdjustment(
            product_id=maneo.product_id,
            admin_id=current_user.id,
            tipo_movimiento=f'Devolución de Maneo ({maneo.local_vecino}){variante_label}',
            stock_anterior=stock_anterior,
            stock_nuevo=stock_nuevo
        )
        db.session.add(ajuste)

        # Determinar si es devolución total o parcial
        if cantidad_devuelta >= maneo.cantidad:
            # Devolución total: se cierra el maneo
            maneo.estado = 'DEVUELTO'
            maneo.fecha_resolucion = obtener_hora_bogota()
            db.session.commit()
            flash(f'Maneo cerrado. Se devolvieron {cantidad_devuelta} unidades al inventario.', 'success')
        else:
            # Devolución parcial: se reduce la cantidad y el maneo sigue PENDIENTE
            unidades_restantes = maneo.cantidad - cantidad_devuelta
            maneo.cantidad = unidades_restantes
            db.session.commit()
            flash(f'Devolución parcial registrada. Se devolvieron {cantidad_devuelta} uds al inventario. Quedan {unidades_restantes} uds pendientes de cobrar.', 'info')

    except Exception:
        db.session.rollback()
        flash('Error al procesar la devolución.', 'danger')

    return redirect(url_for('admin_bp.maneos'))

@admin_bp.route('/balance-financiero', methods=['GET', 'POST'])
@login_required
@admin_required
def balance_financiero():
    if request.method == 'POST':
        fecha_inicio_str = request.form.get('fecha_inicio')
        fecha_fin_str = request.form.get('fecha_fin')
    else:
        fecha_inicio_str = request.args.get('fecha_inicio')
        fecha_fin_str = request.args.get('fecha_fin')

    hoy = obtener_hora_bogota()
    import calendar
    if not fecha_inicio_str or not fecha_fin_str:
        # Por defecto, el mes actual
        primer_dia = hoy.replace(day=1)
        ultimo_dia_mes = calendar.monthrange(hoy.year, hoy.month)[1]
        ultimo_dia = hoy.replace(day=ultimo_dia_mes)
        
        fecha_inicio_str = primer_dia.strftime('%Y-%m-%d')
        fecha_fin_str = ultimo_dia.strftime('%Y-%m-%d')

    from datetime import datetime, timedelta
    try:
        inicio_dt = datetime.strptime(fecha_inicio_str, '%Y-%m-%d')
        fin_dt = datetime.strptime(fecha_fin_str, '%Y-%m-%d')
        # Avanzamos límite al inicio del siguiente día matemáticamente
        fin_dt_query = fin_dt + timedelta(days=1)
    except ValueError:
        flash("Formato de fecha inválido.", "danger")
        return redirect(url_for('admin_bp.dashboard'))

    # 1. Ventas Totales
    ventas_query = Sale.query.filter(Sale.fecha_venta >= inicio_dt, Sale.fecha_venta < fin_dt_query).all()
    
    ventas_efectivo = sum(v.monto_total for v in ventas_query if v.metodo_pago == 'efectivo')
    ventas_transferencia = sum(v.monto_total for v in ventas_query if v.metodo_pago in ['transferencia', 'nequi', 'bancolombia', 'daviplata', 'tarjeta'])
    total_ingresos = ventas_efectivo + ventas_transferencia

    # 2. Costo de Mercancía Vendida (COGS)
    detalles_vendidos = db.session.query(SaleDetail, Product).join(Product, SaleDetail.product_id == Product.id).join(Sale, SaleDetail.sale_id == Sale.id).filter(
        Sale.fecha_venta >= inicio_dt,
        Sale.fecha_venta < fin_dt_query
    ).all()
    
    costos_directos = sum((detalle.SaleDetail.cantidad_vendida * (detalle.Product.precio_costo or 0)) for detalle in detalles_vendidos)

    # 3. Costos Indirectos y Gastos Operativos
    gastos_query = Expense.query.filter(Expense.fecha_gasto >= inicio_dt, Expense.fecha_gasto < fin_dt_query).all()
    
    costos_indirectos = sum(g.monto for g in gastos_query if g.tipo_gasto == 'Costo Indirecto')
    gastos_operacionales = sum(g.monto for g in gastos_query if g.tipo_gasto == 'Gasto Diario')
    
    # 4. Mermas y Pérdidas
    perdidas_query = Loss.query.filter(Loss.date >= inicio_dt, Loss.date < fin_dt_query).all()
    costo_perdidas = sum((p.cost_at_loss * p.quantity) for p in perdidas_query)
    
    total_salidas = float(costos_directos) + float(costos_indirectos) + float(gastos_operacionales) + float(costo_perdidas)
    balance_neto = float(total_ingresos) - total_salidas

    datos_financieros = {
        'ventas_efectivo': float(ventas_efectivo),
        'ventas_transferencia': float(ventas_transferencia),
        'total_ingresos': float(total_ingresos),
        'costos_directos': float(costos_directos),
        'costos_indirectos': float(costos_indirectos),
        'gastos_operacionales': float(gastos_operacionales),
        'costo_perdidas': float(costo_perdidas),
        'total_salidas': total_salidas,
        'balance_neto': balance_neto
    }

    return render_template(
        'admin/balance_reporte.html',
        fecha_inicio=fecha_inicio_str,
        fecha_fin=fecha_fin_str,
        fecha_generacion=hoy.strftime('%Y-%m-%d %H:%M'),
        datos=datos_financieros
    )
