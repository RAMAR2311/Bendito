import os

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from decorators import bodega_required
from models import (
    AbonoBodega,
    Cliente,
    FacturaBodega,
    FacturaBodegaDetalle,
    Product,
    StockAdjustment,
    db,
)

bodega_bp = Blueprint('bodega_bp', __name__)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@bodega_bp.route('/dashboard')
@login_required
@bodega_required
def dashboard():
    total_clientes = Cliente.query.count()
    facturas_recientes = FacturaBodega.query.order_by(FacturaBodega.fecha_subida.desc()).limit(10).all()
    return render_template('bodega/dashboard.html', clientes_count=total_clientes, facturas=facturas_recientes)

@bodega_bp.route('/clientes/nuevo', methods=['GET', 'POST'])
@login_required
@bodega_required
def nuevo_cliente():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        documento = request.form.get('documento')
        telefono = request.form.get('telefono')
        email = request.form.get('email')
        direccion = request.form.get('direccion')

        if not nombre or not documento or not telefono:
            flash('Por favor completa los campos obligatorios: Nombre, Documento y Teléfono.', 'danger')
            return redirect(url_for('bodega_bp.nuevo_cliente'))

        if Cliente.query.filter_by(documento_o_nit=documento.strip()).first():
            flash('Ya existe un cliente registrado con ese Documento/NIT.', 'warning')
            return redirect(url_for('bodega_bp.nuevo_cliente'))

        nuevo = Cliente(
            nombre_o_razon_social=nombre.strip(),
            documento_o_nit=documento.strip(),
            telefono=telefono.strip(),
            email=email.strip() if email else None,
            direccion=direccion.strip() if direccion else None
        )
        try:
            db.session.add(nuevo)
            db.session.commit()
            flash(f'Cliente {nombre} registrado exitosamente.', 'success')
            return redirect(url_for('bodega_bp.dashboard'))
        except Exception:
            db.session.rollback()
            flash('Error al intentar registrar el cliente.', 'danger')

    return render_template('bodega/cliente_nuevo.html')

@bodega_bp.route('/facturas/nueva', methods=['GET', 'POST'])
@login_required
@bodega_required
def nueva_factura():
    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id')
        num_factura = request.form.get('numero_factura')
        monto_total = request.form.get('monto_total', 0.0)
        
        # Arrays of products and quantities
        productos_ids = request.form.getlist('producto_id[]')
        cantidades = request.form.getlist('cantidad[]')
        precios_unitarios = request.form.getlist('precio_unitario[]')
        
        if 'archivo_factura' not in request.files:
            flash('No se subió ningún archivo PDF/Imagen.', 'danger')
            return redirect(url_for('bodega_bp.nueva_factura'))
            
        archivo = request.files['archivo_factura']
        
        if archivo.filename == '':
            flash('Ningún archivo seleccionado.', 'danger')
            return redirect(url_for('bodega_bp.nueva_factura'))

        if not productos_ids or not cantidades or not precios_unitarios:
            flash('Debes agregar al menos un producto a la factura.', 'danger')
            return redirect(url_for('bodega_bp.nueva_factura'))

        if len(productos_ids) != len(cantidades) or len(productos_ids) != len(precios_unitarios):
            flash('Error en los datos de los productos enviados.', 'danger')
            return redirect(url_for('bodega_bp.nueva_factura'))

        if archivo and allowed_file(archivo.filename):
            filename = secure_filename(archivo.filename)
            unique_filename = f"fact_{cliente_id}_{num_factura}_{filename}"
            upload_path = os.path.join('/var/www/bendito/static/uploads', 'facturas')
            
            os.makedirs(upload_path, exist_ok=True)
            file_path = os.path.join(upload_path, unique_filename)
            archivo.save(file_path)

            try:
                nueva_fact = FacturaBodega(
                    cliente_id=cliente_id,
                    usuario_id=current_user.id,
                    numero_factura=num_factura,
                    monto_total=float(str(monto_total).replace(',', '')),
                    archivo_ruta=f"uploads/facturas/{unique_filename}"
                )
                db.session.add(nueva_fact)
                db.session.flush() # Para obtener el ID de nueva_fact
                
                # Procesar productos y descontar el stock
                for p_id, cant, precio_uni in zip(productos_ids, cantidades, precios_unitarios):
                    cant = int(cant)
                    precio_uni = float(str(precio_uni).replace(',', ''))
                    producto = Product.query.get(p_id)
                    
                    if not producto or producto.cantidad_stock < cant:
                        db.session.rollback()
                        flash(f'No hay stock suficiente para el producto: {producto.nombre if producto else "Desconocido"}. Stock actual: {producto.cantidad_stock if producto else 0}', 'danger')
                        return redirect(url_for('bodega_bp.nueva_factura'))
                    
                    # 1. Crear el Detalle
                    detalle = FacturaBodegaDetalle(
                        factura_id=nueva_fact.id,
                        producto_id=producto.id,
                        cantidad=cant,
                        precio_venta=precio_uni
                    )
                    db.session.add(detalle)
                    
                    # 2. Descontar Stock
                    stock_anterior = producto.cantidad_stock
                    producto.cantidad_stock -= cant
                    
                    # 3. Registrar Historial de Ajuste
                    ajuste = StockAdjustment(
                        product_id=producto.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f"Salida por Factura Bodega #{num_factura}",
                        stock_anterior=stock_anterior,
                        stock_nuevo=producto.cantidad_stock
                    )
                    db.session.add(ajuste)

                db.session.commit()
                flash('Factura guardada y stock de inventario descontado correctamente.', 'success')
                return redirect(url_for('bodega_bp.dashboard'))
                
            except Exception:
                db.session.rollback()
                flash('Ocurrió un error en la base de datos al guardar la factura o afectar el stock.', 'danger')
        else:
            flash('Tipo de archivo no permitido. Solo se permiten PDF e imágenes.', 'danger')
            return redirect(url_for('bodega_bp.nueva_factura'))

    clientes = Cliente.query.order_by(Cliente.nombre_o_razon_social).all()
    # Enviamos los productos disponibles a la vista, restringidos al inventario de bodega
    productos_disp = Product.query.filter_by(tipo_inventario='bodega').filter(Product.cantidad_stock > 0).order_by(Product.nombre).all()
    return render_template('bodega/factura_nueva.html', clientes=clientes, productos=productos_disp)

@bodega_bp.route('/api/producto/<path:sku>', methods=['GET'])
@login_required
@bodega_required
def api_buscar_producto_bodega(sku):
    producto = Product.query.filter_by(sku=sku, tipo_inventario='bodega').first()
    
    if not producto:
        return jsonify({'error': 'Código SKU no encontrado en bodega'}), 404
        
    return jsonify({
        'id': producto.id,
        'nombre': producto.nombre,
        'sku': producto.sku,
        'cantidad_stock': producto.cantidad_stock,
        'precio_sugerido': float(producto.precio_sugerido)
    })

@bodega_bp.route('/clientes')
@login_required
@bodega_required
def clientes():
    lista_clientes = Cliente.query.order_by(Cliente.nombre_o_razon_social).all()
    return render_template('bodega/clientes.html', clientes=lista_clientes)

@bodega_bp.route('/clientes/<int:id>')
@login_required
@bodega_required
def cliente_detalle(id):
    cliente = Cliente.query.get_or_404(id)
    return render_template('bodega/cliente_detalle.html', cliente=cliente)

@bodega_bp.route('/facturas/<int:factura_id>/abono', methods=['POST'])
@login_required
@bodega_required
def nuevo_abono(factura_id):
    factura = FacturaBodega.query.get_or_404(factura_id)
    monto_abono = float(request.form.get('monto_abono', '0').replace(',', ''))
    metodo_pago = request.form.get('metodo_pago', 'efectivo')
    observacion = request.form.get('observacion', '')

    if monto_abono <= 0:
        flash('El monto del abono debe ser mayor a cero.', 'danger')
        return redirect(url_for('bodega_bp.cliente_detalle', id=factura.cliente_id))

    if monto_abono > factura.saldo_pendiente:
        flash(f'El monto supera el saldo pendiente (${factura.saldo_pendiente}).', 'danger')
        return redirect(url_for('bodega_bp.cliente_detalle', id=factura.cliente_id))

    abono = AbonoBodega(
        factura_id=factura.id,
        usuario_id=current_user.id,
        monto=monto_abono,
        metodo_pago=metodo_pago,
        observacion=observacion
    )
    
    try:
        db.session.add(abono)
        db.session.commit()
        
        # Validar si el saldo quedó en cero
        if factura.saldo_pendiente <= 0:
            factura.estado = 'Pagado'
        else:
            factura.estado = 'Parcial'
        db.session.commit()

        flash(f'Abono de ${monto_abono} registrado correctamente a la factura #{factura.numero_factura}.', 'success')
    except Exception:
        db.session.rollback()
        flash('Hubo un error al registrar el abono.', 'danger')

    return redirect(url_for('bodega_bp.cliente_detalle', id=factura.cliente_id))

