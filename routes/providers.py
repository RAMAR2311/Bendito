import os
import time

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from decorators import admin_required
from models import Provider, ProviderInvoice, ProviderPayment, db

providers_bp = Blueprint('providers_bp', __name__)

# Configuración de subida de comprobantes
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'static', 'uploads', 'providers')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@providers_bp.route('/', methods=['GET'])
@login_required
@admin_required
def index():
    proveedores = Provider.query.order_by(Provider.nombre).all()
    return render_template('providers/index.html', proveedores=proveedores)

@providers_bp.route('/crear', methods=['POST'])
@login_required
@admin_required
def crear():
    nombre = request.form.get('nombre')
    empresa = request.form.get('empresa')
    telefono = request.form.get('telefono')

    if not nombre or not nombre.strip():
        flash('El nombre del proveedor es obligatorio.', 'danger')
        return redirect(url_for('providers_bp.index'))

    nuevo_proveedor = Provider(
        nombre=nombre.strip(),
        empresa=empresa.strip() if empresa else None,
        telefono=telefono.strip() if telefono else None
    )
    
    db.session.add(nuevo_proveedor)
    db.session.commit()
    flash(f'Proveedor {nombre} registrado exitosamente.', 'success')
    return redirect(url_for('providers_bp.index'))

@providers_bp.route('/<int:id>', methods=['GET'])
@login_required
@admin_required
def cuenta(id):
    proveedor = Provider.query.get_or_404(id)
    
    # Ordenar historial
    facturas = ProviderInvoice.query.filter_by(provider_id=id).order_by(ProviderInvoice.fecha_factura.desc()).all()
    abonos = ProviderPayment.query.filter_by(provider_id=id).order_by(ProviderPayment.fecha_pago.desc()).all()
    
    # Cálculo Principal de la Cuenta Corriente
    total_facturado = sum(f.monto_total for f in facturas)
    total_abonos = sum(a.monto_abonado for a in abonos)
    saldo_pendiente = float(total_facturado) - float(total_abonos)
    
    return render_template('providers/cuenta.html', 
                           proveedor=proveedor, 
                           facturas=facturas, 
                           abonos=abonos,
                           total_facturado=total_facturado,
                           total_abonos=total_abonos,
                           saldo_pendiente=saldo_pendiente)

@providers_bp.route('/<int:id>/invoice', methods=['POST'])
@login_required
@admin_required
def registrar_factura(id):
    Provider.query.get_or_404(id)
    
    monto_total = float(request.form.get('monto_total', '0').replace(',', ''))
    numero_factura = request.form.get('numero_factura')
    descripcion = request.form.get('descripcion')
    
    if monto_total <= 0:
        flash('El monto de la factura debe ser mayor a 0.', 'danger')
        return redirect(url_for('providers_bp.cuenta', id=id))
        
    filename = None
    if 'comprobante' in request.files:
        file = request.files['comprobante']
        if file and allowed_file(file.filename):
            ext = file.filename.rsplit('.', 1)[1].lower()
            filename = f"prov_{id}_{int(time.time())}.{ext}"
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            file.save(filepath)

    nueva_factura = ProviderInvoice(
        provider_id=id,
        monto_total=monto_total,
        numero_factura=numero_factura.strip() if numero_factura else None,
        descripcion=descripcion.strip() if descripcion else None,
        comprobante=filename
    )
    
    db.session.add(nueva_factura)
    db.session.commit()
    flash('¡Factura cargada! La cuenta por pagar ha aumentado.', 'warning')
    return redirect(url_for('providers_bp.cuenta', id=id))

@providers_bp.route('/<int:id>/payment', methods=['POST'])
@login_required
@admin_required
def registrar_abono(id):
    proveedor = Provider.query.get_or_404(id)
    
    monto_abonado = float(request.form.get('monto_abonado', '0').replace(',', ''))
    observacion = request.form.get('observacion')
    
    if monto_abonado <= 0:
        flash('El monto a abonar debe ser mayor a 0.', 'danger')
        return redirect(url_for('providers_bp.cuenta', id=id))
        
    nuevo_abono = ProviderPayment(
        provider_id=id,
        monto_abonado=monto_abonado,
        observacion=observacion.strip() if observacion else None
    )
    
    db.session.add(nuevo_abono)
    db.session.commit()
    flash(f'Abono de ${monto_abonado} registrado exitosamente a favor de {proveedor.nombre}.', 'success')
    return redirect(url_for('providers_bp.cuenta', id=id))

@providers_bp.route('/eliminar/<int:id>', methods=['POST'])
@login_required
@admin_required
def eliminar_proveedor(id):
    proveedor = Provider.query.get_or_404(id)
    nombre = proveedor.nombre
    
    try:
        db.session.delete(proveedor)
        db.session.commit()
        flash(f'Proveedor "{nombre}" y todo su historial eliminados correctamente.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al intentar eliminar el proveedor: {e!s}', 'danger')
        
    return redirect(url_for('providers_bp.index'))
