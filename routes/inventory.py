import os
from io import BytesIO

import pandas as pd
from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from decorators import admin_or_bodega_required
from models import Product, ProductVariant, StockAdjustment, db

inventory_bp = Blueprint('inventory_bp', __name__)

MAX_PRICE_LIMIT = 99999999999.99

def validate_prices(*prices):
    """Retorna True si todos los precios están dentro del rango permitido por la DB."""
    for price in prices:
        if price > MAX_PRICE_LIMIT:
            return False
    return True

@inventory_bp.route('/', methods=['GET'])
@login_required
@admin_or_bodega_required
def index():
    tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
    productos = Product.query.filter_by(tipo_inventario=tipo, activo=True).order_by(Product.nombre).all()
    
    total_unidades = 0
    valor_total_costo = 0.0
    valor_total_venta = 0.0
    
    for p in productos:
        # Calcular sumatorias para productos con variantes activas
        variantes_activas = [v for v in p.variantes if v.activo]
        if variantes_activas:
            for v in variantes_activas:
                total_unidades += v.cantidad_stock
                valor_total_costo += (v.cantidad_stock * float(v.precio_costo or p.precio_costo))
                valor_total_venta += (v.cantidad_stock * float(v.precio_sugerido or p.precio_sugerido))
        else:
            # Producto simple (sin variantes)
            total_unidades += p.cantidad_stock
            valor_total_costo += (p.cantidad_stock * float(p.precio_costo))
            valor_total_venta += (p.cantidad_stock * float(p.precio_sugerido))
            
    return render_template('inventory/index.html', 
                           productos=productos, 
                           total_unidades=total_unidades, 
                           valor_total_costo=valor_total_costo, 
                           valor_total_venta=valor_total_venta)

@inventory_bp.route('/nuevo', methods=['GET', 'POST'])
@login_required
@admin_or_bodega_required
def nuevo():
    if request.method == 'POST':
        # --- Manejo de Imagen ---
        imagen_filename = None
        if 'imagen' in request.files:
            file = request.files['imagen']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join('/var/www/bendito/static/uploads', filename))
                imagen_filename = filename

        # La instanciación agrupa todos los parámetros del nuevo producto
        tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
        nuevo_costo = float(request.form.get('precio_costo', '0').replace(',', ''))
        nuevo_minimo = float(request.form.get('precio_minimo', '0').replace(',', ''))
        nuevo_sugerido = float(request.form.get('precio_sugerido', '0').replace(',', ''))

        if not validate_prices(nuevo_costo, nuevo_minimo, nuevo_sugerido):
            flash('Uno de los precios ingresados es demasiado alto. El máximo permitido es 99,999,999.99.', 'danger')
            return render_template('inventory/form.html')

        nuevo_prod = Product(
            sku=request.form.get('sku').strip(),
            nombre=request.form.get('nombre').strip(),
            tipo_inventario=tipo,
            cantidad_stock=int(request.form.get('cantidad_stock', 0)),
            precio_costo=nuevo_costo,
            precio_minimo=nuevo_minimo,
            precio_sugerido=nuevo_sugerido,
            imagen=imagen_filename,
            observacion=request.form.get('observacion')
        )
        try:
            db.session.add(nuevo_prod)
            db.session.commit()
            
            # Crear ajuste inicial automáticamente en el Kardex
            ajuste_inicial = StockAdjustment(
                product_id=nuevo_prod.id,
                admin_id=current_user.id,
                tipo_movimiento='Creación Inicial',
                stock_anterior=0,
                stock_nuevo=nuevo_prod.cantidad_stock
            )
            db.session.add(ajuste_inicial)
            db.session.commit()

            flash('Producto creado exitosamente.', 'success')
            return redirect(url_for('inventory_bp.index'))
        except Exception:
            db.session.rollback()
            flash('Error al intentar guardar el producto en la base de datos.', 'danger')
            
    return render_template('inventory/form.html')

@inventory_bp.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_or_bodega_required
def editar_producto(id):
    # get_or_404 protege la ruta en caso de que se envíe un ID inexistente en la URL
    producto = Product.query.get_or_404(id)
    tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
    if producto.tipo_inventario != tipo:
        abort(403)
    
    if request.method == 'POST':
        stock_anterior = producto.cantidad_stock
        cantidad_stock_nueva = int(request.form.get('cantidad_stock', 0))
        
        # Actualizar Imagen si se sube una nueva
        if 'imagen' in request.files:
            file = request.files['imagen']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join('/var/www/bendito/static/uploads', filename))
                producto.imagen = filename
                
        # Se capturan los nuevos precios
        nuevo_costo = float(request.form.get('precio_costo', '0').replace(',', ''))
        nuevo_minimo = float(request.form.get('precio_minimo', '0').replace(',', ''))
        nuevo_sugerido = float(request.form.get('precio_sugerido', '0').replace(',', ''))

        if not validate_prices(nuevo_costo, nuevo_minimo, nuevo_sugerido):
            flash('Uno de los precios ingresados es demasiado alto. El máximo permitido es 99,999,999.99.', 'danger')
            return render_template('inventory/form.html', producto=producto)

        # Actualizar producto principal
        producto.sku = request.form.get('sku').strip()
        producto.nombre = request.form.get('nombre').strip()
        producto.cantidad_stock = cantidad_stock_nueva
        producto.precio_costo = nuevo_costo
        producto.precio_minimo = nuevo_minimo
        producto.precio_sugerido = nuevo_sugerido
        producto.observacion = request.form.get('observacion')

        # PROPAGACIÓN: Actualizar precios en todas las variantes vinculadas
        for var in producto.variantes:
            var.precio_costo = nuevo_costo
            var.precio_minimo = nuevo_minimo
            var.precio_sugerido = nuevo_sugerido
        
        try:
            if stock_anterior != cantidad_stock_nueva:
                ajuste = StockAdjustment(
                    product_id=producto.id,
                    admin_id=current_user.id,
                    tipo_movimiento='Ajuste Manual',
                    stock_anterior=stock_anterior,
                    stock_nuevo=cantidad_stock_nueva
                )
                db.session.add(ajuste)
                
            db.session.commit()
            flash('Producto actualizado correctamente en base de datos.', 'success')
            return redirect(url_for('inventory_bp.index'))
        except Exception:
            db.session.rollback()
            flash('Error en la base de datos al actualizar el producto.', 'danger')

    # El objeto producto se pasa a Jinja para auto-poblar (pre-llenar) el formulario en modo edición
    return render_template('inventory/form.html', producto=producto)

@inventory_bp.route('/historial-ajustes')
@login_required
@admin_or_bodega_required
def historial_ajustes():
    tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
    ajustes = StockAdjustment.query.join(Product).filter(Product.tipo_inventario == tipo).order_by(StockAdjustment.fecha_ajuste.desc()).all()
    return render_template('inventory/historial_ajustes.html', ajustes=ajustes)

@inventory_bp.route('/ver/<int:id>', methods=['GET'])
@login_required
@admin_or_bodega_required
def ver_producto(id):
    producto = Product.query.get_or_404(id)
    tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
    if producto.tipo_inventario != tipo:
        abort(403)
    ajustes = StockAdjustment.query.filter_by(product_id=id).order_by(StockAdjustment.fecha_ajuste.desc()).all()
    return render_template('inventory/ver.html', producto=producto, ajustes=ajustes)

@inventory_bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_or_bodega_required
def eliminar_producto(id):
    producto = db.session.get(Product, id)
    if not producto or not producto.activo:
        flash('El producto ya ha sido removido del inventario.', 'info')
        return redirect(url_for('inventory_bp.index'))
    
    if current_user.rol != 'admin':
        tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
        if producto.tipo_inventario != tipo:
            abort(403)
            
    try:
        nombre = producto.nombre
        producto.activo = False
        for v in producto.variantes:
            v.activo = False
        db.session.commit()
        flash(f'Producto "{nombre}" fue removido del inventario activo (conservando todo su historial contable y de ventas).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ocurrió un error al remover el producto: {e!s}', 'danger')
        
    return redirect(url_for('inventory_bp.index'))

@inventory_bp.route('/producto/<int:id>/agregar_variante', methods=['POST'])
@login_required
@admin_or_bodega_required
def agregar_variante(id):
    producto = Product.query.get_or_404(id)
    nombre_variante = request.form.get('nombre_variante')
    cantidad_stock = int(request.form.get('cantidad_stock', 0))
    
    precio_costo_req = request.form.get('precio_costo')
    precio_minimo_req = request.form.get('precio_minimo')
    precio_sugerido_req = request.form.get('precio_sugerido')

    if not nombre_variante:
        flash('El nombre de la variante es obligatorio.', 'danger')
        return redirect(url_for('inventory_bp.index'))

    v_costo = float(str(precio_costo_req).replace(',', '')) if precio_costo_req else producto.precio_costo
    v_minimo = float(str(precio_minimo_req).replace(',', '')) if precio_minimo_req else producto.precio_minimo
    v_sugerido = float(str(precio_sugerido_req).replace(',', '')) if precio_sugerido_req else producto.precio_sugerido

    if not validate_prices(v_costo, v_minimo, v_sugerido):
        flash('Uno de los precios para la variante es demasiado alto.', 'danger')
        return redirect(url_for('inventory_bp.index'))

    nueva_variante = ProductVariant(
        product_id=producto.id,
        nombre_variante=nombre_variante,
        cantidad_stock=cantidad_stock,
        precio_costo=v_costo,
        precio_minimo=v_minimo,
        precio_sugerido=v_sugerido
    )
    try:
        db.session.add(nueva_variante)
        # Opcionalmente descontar o trackear en Kardex? La instrucción solo dice: "crea la ruta para añadir la subcategoría"
        db.session.commit()
        flash(f'Variante "{nombre_variante}" agregada con éxito.', 'success')
    except Exception:
        db.session.rollback()
        flash('Error al agregar la variante.', 'danger')

    return redirect(url_for('inventory_bp.index'))

@inventory_bp.route('/variante/<int:id>/editar', methods=['POST'])
@login_required
@admin_or_bodega_required
def editar_variante(id):
    variante = ProductVariant.query.get_or_404(id)
    
    variante.nombre_variante = request.form.get('nombre_variante')
    variante.cantidad_stock = int(request.form.get('cantidad_stock', variante.cantidad_stock))
    
    precio_costo_req = request.form.get('precio_costo')
    precio_minimo_req = request.form.get('precio_minimo')
    precio_sugerido_req = request.form.get('precio_sugerido')
    
    v_costo = float(str(precio_costo_req).replace(',', '')) if precio_costo_req else variante.precio_costo
    v_minimo = float(str(precio_minimo_req).replace(',', '')) if precio_minimo_req else variante.precio_minimo
    v_sugerido = float(str(precio_sugerido_req).replace(',', '')) if precio_sugerido_req else variante.precio_sugerido

    if not validate_prices(v_costo, v_minimo, v_sugerido):
        flash('Uno de los precios para la variante es demasiado alto.', 'danger')
        return redirect(url_for('inventory_bp.index'))

    variante.precio_costo = v_costo
    variante.precio_minimo = v_minimo
    variante.precio_sugerido = v_sugerido
    
    try:
        db.session.commit()
        flash('Variante editada con éxito.', 'success')
    except Exception:
        db.session.rollback()
        flash('Error al editar la variante.', 'danger')
        
    return redirect(url_for('inventory_bp.index'))

@inventory_bp.route('/variante/<int:id>/eliminar', methods=['POST'])
@login_required
@admin_or_bodega_required
def eliminar_variante(id):
    variante = db.session.get(ProductVariant, id)
    if not variante or not variante.activo:
        flash('La subcategoría ya ha sido removida del inventario.', 'info')
        return redirect(url_for('inventory_bp.index'))
    try:
        nombre = variante.nombre_variante
        variante.activo = False
        db.session.commit()
        flash(f'La subcategoría "{nombre}" fue removida del inventario activo (conservando su historial).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al remover la variante: {e!s}', 'danger')
        
    return redirect(url_for('inventory_bp.index'))

@inventory_bp.route('/plantilla-importacion')
@login_required
@admin_or_bodega_required
def descargar_plantilla():
    # Crear la estructura de datos
    cols = ['sku', 'nombre', 'subcategoria', 'cantidad_stock', 'precio_costo', 'precio_minimo', 'precio_sugerido', 'observacion']
    df = pd.DataFrame(columns=cols)
    
    # Filas de ejemplo instructivas
    df.loc[0] = ['SKU-001', 'Camiseta Polo', 'Azul / M', 50, 15000, 25000, 35000, 'Algodón Premium']
    df.loc[1] = ['SKU-001', 'Camiseta Polo', 'Rojo / L', 30, 15000, 25000, 35000, 'Algodón Premium']
    df.loc[2] = ['SKU-002', 'Protector Pantalla G7', '', 100, 2000, 5000, 8000, 'Sin subcategorías']
    
    output = BytesIO()
    
    try:
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Plantilla Bendito')
            
            workbook  = writer.book
            worksheet = writer.sheets['Plantilla Bendito']
            
            # Formato para el encabezado (Lila Bendito Encanto)
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'vcenter',
                'align': 'center',
                'fg_color': '#C794C7',
                'font_color': '#FFFFFF',
                'border': 1
            })
            
            # Aplicar formato a los encabezados
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)
                column_len = max(len(str(value)), 15)
                worksheet.set_column(col_num, col_num, column_len)
    except Exception:
        output = BytesIO()
        df.to_excel(output, index=False, sheet_name='Plantilla Bendito')

    output.seek(0)
    return send_file(output, download_name="plantilla_importacion_bendito_encanto.xlsx", as_attachment=True)

@inventory_bp.route('/importar', methods=['POST'])
@login_required
@admin_or_bodega_required
def importar_inventario():
    if 'archivo' not in request.files:
        flash('No se seleccionó ningún archivo.', 'danger')
        return redirect(url_for('inventory_bp.index'))
        
    archivo = request.files['archivo']
    if archivo.filename == '':
        flash('Ningún archivo seleccionado.', 'danger')
        return redirect(url_for('inventory_bp.index'))
        
    if not (archivo.filename.endswith('.xlsx') or archivo.filename.endswith('.csv')):
        flash('Formato no válido. Solo debes subir archivos .xlsx o .csv', 'warning')
        return redirect(url_for('inventory_bp.index'))
        
    try:
        # Lectura con pandas según la extensión
        if archivo.filename.endswith('.csv'):
            df = pd.read_csv(archivo)
        else:
            df = pd.read_excel(archivo)
            
        required_cols = ['sku', 'nombre', 'cantidad_stock', 'precio_costo', 'precio_minimo', 'precio_sugerido', 'observacion']
        # 'subcategoria' es opcional pero la normalizamos si existe
        
        # Limpieza de encabezados para evitar problemas por mayúsculas o espacios accidentales
        df.columns = [str(c).strip().lower() for c in df.columns]
        
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            flash(f"El archivo rechazado. Faltan las siguientes columnas: {', '.join(missing)}", 'danger')
            return redirect(url_for('inventory_bp.index'))
            
        tipo = 'bodega' if current_user.rol == 'bodega' else 'tienda'
        creados = 0
        actualizados = 0
        variantes_procesadas = 0
        
        for idx, row in df.iterrows():
            sku_raw = str(row['sku']).strip()
            if not sku_raw or sku_raw.lower() == 'nan':
                continue
                
            # Limpiar cantidades para evitar errores NaN o Nulls
            cant = int(row['cantidad_stock']) if pd.notna(row['cantidad_stock']) else 0
            costo = float(row['precio_costo']) if pd.notna(row['precio_costo']) else 0.0
            minimo = float(row['precio_minimo']) if pd.notna(row['precio_minimo']) else 0.0
            sugerido = float(row['precio_sugerido']) if pd.notna(row['precio_sugerido']) else 0.0
            nombre_val = str(row['nombre']).strip()
            obs_val = str(row['observacion']).strip() if pd.notna(row['observacion']) else ''
            if obs_val.lower() == 'nan':
                obs_val = ''

            sub_raw = str(row['subcategoria']).strip() if 'subcategoria' in row else ''
            if sub_raw.lower() in ['nan', 'none', '']:
                sub_raw = None

            prod = Product.query.filter_by(sku=sku_raw, tipo_inventario=tipo).first()
            
            if not prod:
                # CREAR PRODUCTO BASE PRIMERO
                prod = Product(
                    sku=sku_raw,
                    nombre=nombre_val,
                    tipo_inventario=tipo,
                    cantidad_stock=0, # Empezamos en 0, se llenará abajo
                    precio_costo=costo,
                    precio_minimo=minimo,
                    precio_sugerido=sugerido,
                    observacion=obs_val
                )
                db.session.add(prod)
                db.session.flush()
                creados += 1
            else:
                # Actualizar información general del producto existente
                prod.nombre = nombre_val
                prod.observacion = obs_val
                if not sub_raw: # Si es producto base, actualizamos precios y PROPAGAMOS a variantes
                    prod.precio_costo = costo
                    prod.precio_minimo = minimo
                    prod.precio_sugerido = sugerido
                    
                    # Sincronizar variantes existentes con los nuevos precios del Excel
                    for var in prod.variantes:
                        var.precio_costo = costo
                        var.precio_minimo = minimo
                        var.precio_sugerido = sugerido
                actualizados += 1

            if sub_raw:
                # PROCESAR COMO VARIANTE
                var = ProductVariant.query.filter_by(product_id=prod.id, nombre_variante=sub_raw).first()
                if var:
                    stock_ant = var.cantidad_stock
                    var.cantidad_stock += cant
                    # Actualizar precios específicos de la variante si vienen en el excel
                    var.precio_costo = costo
                    var.precio_minimo = minimo
                    var.precio_sugerido = sugerido
                else:
                    stock_ant = 0
                    var = ProductVariant(
                        product_id=prod.id,
                        nombre_variante=sub_raw,
                        cantidad_stock=cant,
                        precio_costo=costo,
                        precio_minimo=minimo,
                        precio_sugerido=sugerido
                    )
                    db.session.add(var)
                
                variantes_procesadas += 1
                
                # Kardex de variante
                if cant > 0:
                    db.session.add(StockAdjustment(
                        product_id=prod.id,
                        admin_id=current_user.id,
                        tipo_movimiento=f'Entrada Masiva (Subcat: {sub_raw})',
                        stock_anterior=stock_ant,
                        stock_nuevo=var.cantidad_stock
                    ))
            else:
                # PROCESAR COMO PRODUCTO BASE (Sin variante)
                stock_anterior = prod.cantidad_stock
                prod.cantidad_stock += cant
                
                if cant > 0:
                    db.session.add(StockAdjustment(
                        product_id=prod.id,
                        admin_id=current_user.id,
                        tipo_movimiento='Entrada Masiva (Base)',
                        stock_anterior=stock_anterior,
                        stock_nuevo=prod.cantidad_stock
                    ))
                
        db.session.commit()
        flash(f'Carga masiva completada. Productos: {creados} creados / {actualizados} actualizados. Subcategorías procesadas: {variantes_procesadas}.', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Ocurrió un error leyendo las filas de tu archivo: {e!s}', 'danger')
        
    return redirect(url_for('inventory_bp.index'))
