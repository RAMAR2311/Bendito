# Rescan - Modulo Cartera POS v1.0
from datetime import datetime

import pytz
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

def obtener_hora_bogota():
    """Inyecta el uso de red horario en Colombia a nivel de sistema operativo."""
    return datetime.now(pytz.timezone('America/Bogota')).replace(tzinfo=None)

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    telefono = db.Column(db.String(20)) # Nuevo Campo de Contacto (Nullable por Defecto)
    password_hash = db.Column(db.String(256), nullable=False)
    rol = db.Column(db.String(50), nullable=False, default='vendedor')
    
    ventas = db.relationship('Sale', backref='vendedor', lazy=True)
    ajustes_stock = db.relationship('StockAdjustment', backref='admin', lazy=True)
    arqueos = db.relationship('ArqueoCaja', backref='cajero', lazy=True)

class DynamicKey(db.Model):
    __tablename__ = 'dynamic_keys'
    
    id = db.Column(db.Integer, primary_key=True)
    key_code = db.Column(db.String(6), nullable=False, unique=True, index=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=obtener_hora_bogota)
    expires_at = db.Column(db.DateTime, nullable=False)
    
    admin = db.relationship('User', backref='claves_generadas', lazy=True)

    def is_valid(self):
        ahora = obtener_hora_bogota()
        return not self.is_used and self.expires_at > ahora

class Product(db.Model):
    __tablename__ = 'products'
    
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    sku = db.Column(db.String(50), unique=True, nullable=False, index=True)
    tipo_inventario = db.Column(db.String(50), nullable=False, server_default='tienda') # 'tienda' o 'bodega'
    cantidad_stock = db.Column(db.Integer, nullable=False, default=0)
    precio_costo = db.Column(db.Numeric(14, 2), nullable=False, default=0.00) # El Costo de Bodega
    precio_minimo = db.Column(db.Numeric(14, 2), nullable=False)
    precio_sugerido = db.Column(db.Numeric(14, 2), nullable=False)
    imagen = db.Column(db.String(255), nullable=True) # Nombre de la foto subida
    observacion = db.Column(db.Text, nullable=True) # Nota descriptiva
    fecha_creacion = db.Column(db.DateTime, default=obtener_hora_bogota)
    
    detalles_venta = db.relationship('SaleDetail', backref='producto', lazy=True)
    ajustes_stock = db.relationship('StockAdjustment', backref='producto_rel', lazy=True)
    variantes = db.relationship('ProductVariant', backref='producto', lazy=True, cascade="all, delete-orphan")

    @property
    def total_stock(self):
        if self.variantes:
            return sum(v.cantidad_stock for v in self.variantes)
        return self.cantidad_stock

    @property
    def rango_precios(self):
        if not self.variantes:
            return None
        precios = [v.precio_sugerido for v in self.variantes]
        if not precios:
            return None
        min_p = min(precios)
        max_p = max(precios)
        if min_p == max_p:
            return min_p
        return (min_p, max_p)

class ProductVariant(db.Model):
    __tablename__ = 'product_variants'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    nombre_variante = db.Column(db.String(100), nullable=False)
    cantidad_stock = db.Column(db.Integer, nullable=False, default=0)
    
    # Nuevos precios específicos para variantes
    precio_costo = db.Column(db.Numeric(14, 2), nullable=True) 
    precio_minimo = db.Column(db.Numeric(14, 2), nullable=True)
    precio_sugerido = db.Column(db.Numeric(14, 2), nullable=True)

class Loss(db.Model):
    __tablename__ = 'losses'
    
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True) # Quién registró la pérdida
    quantity = db.Column(db.Integer, nullable=False)
    cost_at_loss = db.Column(db.Numeric(14, 2), nullable=False)
    reason = db.Column(db.String(255), nullable=True)
    date = db.Column(db.DateTime, default=obtener_hora_bogota)
    
    producto = db.relationship('Product', backref='perdidas', lazy=True)
    usuario = db.relationship('User', backref='perdidas_registradas', lazy=True)

class Sale(db.Model):
    __tablename__ = 'sales'
    
    id = db.Column(db.Integer, primary_key=True)
    consecutivo = db.Column(db.Integer, nullable=True) # Número lógico de ticket (1, 2, 3...)
    vendedor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    cliente_nombre = db.Column(db.String(150), nullable=True) # Nombre del cliente para facturación POS
    fecha_venta = db.Column(db.DateTime, default=obtener_hora_bogota)
    monto_total = db.Column(db.Numeric(14, 2), nullable=False, default=0.0)
    metodo_pago = db.Column(db.String(50), nullable=False, default='efectivo')
    
    detalles = db.relationship('SaleDetail', backref='venta', lazy=True, cascade="all, delete-orphan")
    pagos = db.relationship('SalePayment', backref='venta', lazy=True, cascade="all, delete-orphan")

    @property
    def metodo_pago_display(self):
        """Retorna un resumen legible del método de pago.
        Si es pago único, retorna el nombre del método.
        Si es mixto, retorna 'Pago Mixto' con desglose."""
        if not self.pagos:
            # Retrocompatibilidad con ventas antiguas que solo tienen metodo_pago
            return self.metodo_pago.capitalize() if self.metodo_pago else 'Efectivo'
        if len(self.pagos) == 1:
            return self.pagos[0].metodo_pago.capitalize()
        return 'Pago Mixto'

class SalePayment(db.Model):
    """Modelo para soportar pagos mixtos/parciales por venta.
    Permite registrar múltiples métodos de pago en una sola venta.
    Ej: $50.000 en efectivo + $30.000 por Nequi = $80.000 total."""
    __tablename__ = 'sale_payments'

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=False)
    metodo_pago = db.Column(db.String(50), nullable=False)  # efectivo, nequi, bancolombia, daviplata
    monto = db.Column(db.Numeric(14, 2), nullable=False)

class SaleDetail(db.Model):
    __tablename__ = 'sale_details'
    
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    variant_id = db.Column(db.Integer, db.ForeignKey('product_variants.id'), nullable=True)
    cantidad_vendida = db.Column(db.Integer, nullable=False)
    precio_venta_final = db.Column(db.Numeric(14, 2), nullable=False)
    # Campos para productos manuales (prestados de otros locales)
    nombre_manual = db.Column(db.String(200), nullable=True)
    precio_costo_manual = db.Column(db.Numeric(14, 2), nullable=True)

    variante = db.relationship('ProductVariant', backref='ventas_rel', lazy=True)

class StockAdjustment(db.Model):
    __tablename__ = 'stock_adjustments'
    
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    tipo_movimiento = db.Column(db.String(100), nullable=True) # Ej: Creación Inicial, Ajuste Manual
    stock_anterior = db.Column(db.Integer, nullable=False)
    stock_nuevo = db.Column(db.Integer, nullable=False)
    fecha_ajuste = db.Column(db.DateTime, default=obtener_hora_bogota)

class ArqueoCaja(db.Model):
    __tablename__ = 'arqueo_caja'
    
    id = db.Column(db.Integer, primary_key=True)
    vendedor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    fecha_arqueo = db.Column(db.Date, nullable=False)
    base_inicial = db.Column(db.Numeric(14, 2), nullable=False, default=0.0)
    gastos_del_dia = db.Column(db.Numeric(14, 2), nullable=False, default=0.0)
    observaciones_gastos = db.Column(db.String(255), nullable=True)
    total_efectivo_sistema = db.Column(db.Numeric(14, 2), nullable=False, default=0.0)
    total_transferencia_sistema = db.Column(db.Numeric(14, 2), nullable=False, default=0.0)
    fecha_creacion = db.Column(db.DateTime, default=obtener_hora_bogota)

class Maneo(db.Model):
    __tablename__ = 'maneos'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    variant_id = db.Column(db.Integer, db.ForeignKey('product_variants.id'), nullable=True)
    local_vecino = db.Column(db.String(150), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    estado = db.Column(db.String(50), nullable=False, default='PENDIENTE') # PENDIENTE, FACTURADO, DEVUELTO
    fecha_prestamo = db.Column(db.DateTime, default=obtener_hora_bogota)
    fecha_resolucion = db.Column(db.DateTime, nullable=True)

    producto = db.relationship('Product', backref='maneos', lazy=True)
    variante = db.relationship('ProductVariant', backref='maneos_rel', lazy=True)

class Expense(db.Model):
    __tablename__ = 'expenses'
    
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    tipo_gasto = db.Column(db.String(50), nullable=False) # 'Gasto Diario' o 'Costo Indirecto'
    categoria = db.Column(db.String(100), nullable=False)
    descripcion = db.Column(db.String(255), nullable=True)
    monto = db.Column(db.Numeric(14, 2), nullable=False)
    fecha_gasto = db.Column(db.DateTime, default=obtener_hora_bogota)

    usuario = db.relationship('User', backref='gastos', lazy=True)

class Cliente(db.Model):
    __tablename__ = 'clientes'

    id = db.Column(db.Integer, primary_key=True)
    nombre_o_razon_social = db.Column(db.String(150), nullable=False)
    documento_o_nit = db.Column(db.String(50), unique=True, nullable=False, index=True)
    telefono = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    direccion = db.Column(db.String(255), nullable=True)
    fecha_registro = db.Column(db.DateTime, default=obtener_hora_bogota)

    facturas = db.relationship('FacturaBodega', backref='cliente', lazy=True)

    @property
    def deuda_total(self):
        return sum(f.saldo_pendiente for f in self.facturas)

    @property
    def estado_global(self):
        return "Con Deuda" if self.deuda_total > 0 else "Al Día"

class FacturaBodega(db.Model):
    __tablename__ = 'facturas_bodega'

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clientes.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    numero_factura = db.Column(db.String(100), nullable=False)
    archivo_ruta = db.Column(db.String(255), nullable=False)
    monto_total = db.Column(db.Numeric(14, 2), nullable=False, default=0.0)
    estado = db.Column(db.String(50), nullable=False, default='Pendiente') # Pendiente, Parcial, Pagado
    fecha_subida = db.Column(db.DateTime, default=obtener_hora_bogota)

    usuario = db.relationship('User', backref='facturas_subidas', lazy=True)
    abonos = db.relationship('AbonoBodega', backref='factura', lazy=True, cascade="all, delete-orphan")
    detalles = db.relationship('FacturaBodegaDetalle', backref='factura', lazy=True, cascade="all, delete-orphan")

    @property
    def saldo_pendiente(self):
        total_abonado = sum(abono.monto for abono in self.abonos)
        return float(self.monto_total) - float(total_abonado)

class FacturaBodegaDetalle(db.Model):
    __tablename__ = 'facturas_bodega_detalles'

    id = db.Column(db.Integer, primary_key=True)
    factura_id = db.Column(db.Integer, db.ForeignKey('facturas_bodega.id'), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_venta = db.Column(db.Numeric(14, 2), nullable=True) # Opcional para futuros análisis

    producto = db.relationship('Product', backref='detalles_factura_bodega', lazy=True)

class AbonoBodega(db.Model):
    __tablename__ = 'abonos_bodega'

    id = db.Column(db.Integer, primary_key=True)
    factura_id = db.Column(db.Integer, db.ForeignKey('facturas_bodega.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    monto = db.Column(db.Numeric(14, 2), nullable=False)
    metodo_pago = db.Column(db.String(50), nullable=False, default='efectivo')
    observacion = db.Column(db.String(255), nullable=True)
    fecha_abono = db.Column(db.DateTime, default=obtener_hora_bogota)

    usuario = db.relationship('User', backref='abonos_registrados', lazy=True)

# ====== MÓDULO PROVEEDORES (CUENTAS POR PAGAR) ======
class Provider(db.Model):
    __tablename__ = 'providers'

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    empresa = db.Column(db.String(150), nullable=True)
    telefono = db.Column(db.String(50), nullable=True)
    fecha_creacion = db.Column(db.DateTime, default=obtener_hora_bogota)

    # Relaciones nativas con facturas y abonos
    facturas = db.relationship('ProviderInvoice', backref='provider', lazy=True, cascade='all, delete-orphan')
    abonos = db.relationship('ProviderPayment', backref='provider', lazy=True, cascade='all, delete-orphan')

class ProviderInvoice(db.Model):
    __tablename__ = 'provider_invoices'

    id = db.Column(db.Integer, primary_key=True)
    provider_id = db.Column(db.Integer, db.ForeignKey('providers.id'), nullable=False)
    monto_total = db.Column(db.Numeric(14, 2), nullable=False)
    numero_factura = db.Column(db.String(100), nullable=True)
    descripcion = db.Column(db.String(255), nullable=True)
    comprobante = db.Column(db.String(255), nullable=True) # Archivo subido
    fecha_factura = db.Column(db.DateTime, default=obtener_hora_bogota)

class ProviderPayment(db.Model):
    __tablename__ = 'provider_payments'

    id = db.Column(db.Integer, primary_key=True)
    provider_id = db.Column(db.Integer, db.ForeignKey('providers.id'), nullable=False)
    monto_abonado = db.Column(db.Numeric(14, 2), nullable=False)
    observacion = db.Column(db.String(255), nullable=True)
    fecha_pago = db.Column(db.DateTime, default=obtener_hora_bogota)

# ====== MÓDULO GARANTÍAS ======
class Warranty(db.Model):
    __tablename__ = 'warranties'

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    nombre_manual = db.Column(db.String(200), nullable=True) # Para items manuales sin product_id
    quantity = db.Column(db.Integer, nullable=False, default=1)
    reason = db.Column(db.String(500), nullable=False) # Motivo del cliente
    resolution = db.Column(db.String(50), nullable=False, default='Pendiente') # Pendiente, Reparacion, Cambio, Reembolso
    created_at = db.Column(db.DateTime, default=obtener_hora_bogota)

    # Relaciones
    venta = db.relationship('Sale', backref='garantias', lazy=True)
    producto = db.relationship('Product', backref='garantias', lazy=True)

    @property
    def tiempo_transcurrido(self):
        """Retorna una cadena legible del tiempo que ha pasado desde el ingreso."""
        ahora = obtener_hora_bogota()
        diferencia = ahora - self.created_at
        
        dias = diferencia.days
        segundos = diferencia.seconds
        horas = segundos // 3600
        minutos = (segundos % 3600) // 60
        
        if dias > 0:
            return f"Hace {dias} día(s)"
        if horas > 0:
            return f"Hace {horas} hora(s)"
        if minutos > 0:
            return f"Hace {minutos} min"
        return "Hace un momento"

# ====== MÓDULO CAMBIOS ======
class ProductExchange(db.Model):
    __tablename__ = 'product_exchanges'

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=False)
    admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    # Producto devuelto
    product_returned_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    variant_returned_id = db.Column(db.Integer, db.ForeignKey('product_variants.id'), nullable=True)
    
    # Producto nuevo (el que sale de la tienda)
    product_new_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    variant_new_id = db.Column(db.Integer, db.ForeignKey('product_variants.id'), nullable=True)
    
    reason = db.Column(db.String(500), nullable=False)
    excedente_pagado = db.Column(db.Numeric(14, 2), default=0.0)
    metodo_pago_excedente = db.Column(db.String(50), nullable=True) # Nequi, Efectivo, etc.
    created_at = db.Column(db.DateTime, default=obtener_hora_bogota)

    # Relaciones para facilitar reportes
    venta = db.relationship('Sale', backref='cambios', lazy=True)
    admin = db.relationship('User', backref='cambios_procesados', lazy=True)
    producto_devuelto = db.relationship('Product', foreign_keys=[product_returned_id], lazy=True)
    variante_devuelta = db.relationship('ProductVariant', foreign_keys=[variant_returned_id], lazy=True)
    producto_nuevo = db.relationship('Product', foreign_keys=[product_new_id], lazy=True)
    variante_nueva = db.relationship('ProductVariant', foreign_keys=[variant_new_id], lazy=True)

class Importacion(db.Model):
    __tablename__ = 'importaciones'

    id = db.Column(db.Integer, primary_key=True)
    proveedor_id = db.Column(db.Integer, db.ForeignKey('providers.id'), nullable=False)
    numero_contenedor = db.Column(db.String(100), nullable=False)
    valor_contenedor = db.Column(db.Numeric(14, 2), nullable=False, default=0.00)
    valor_flete = db.Column(db.Numeric(14, 2), nullable=False, default=0.00)
    pedido_completo = db.Column(db.Boolean, default=True)
    observaciones = db.Column(db.Text, nullable=True)
    fecha_registro = db.Column(db.DateTime, default=obtener_hora_bogota)

    proveedor = db.relationship('Provider', backref='importaciones_rel', lazy=True)

    @property
    def pago_total(self):
        return float(self.valor_contenedor) + float(self.valor_flete)

class SaldoImportacion(db.Model):
    """Registro único del saldo de capital disponible para importaciones.
    Se abona manualmente (capital inicial) y automáticamente (ganancias de ventas).
    Se descuenta al registrar cada importación (valor_contenedor + valor_flete).
    """
    __tablename__ = 'saldo_importacion'

    id = db.Column(db.Integer, primary_key=True)
    saldo_actual = db.Column(db.Numeric(14, 2), nullable=False, default=0.00)
    ultima_actualizacion = db.Column(db.DateTime, default=obtener_hora_bogota)

    def __init__(self, saldo_actual=0.00, ultima_actualizacion=None, **kwargs):
        super().__init__(**kwargs)
        if saldo_actual is not None:
            self.saldo_actual = saldo_actual
        if ultima_actualizacion is not None:
            self.ultima_actualizacion = ultima_actualizacion

    @classmethod
    def obtener(cls):
        """Retorna el registro único, creándolo si no existe."""
        registro = cls.query.first()
        if not registro:
            registro = cls(saldo_actual=0.00)
            db.session.add(registro)
            db.session.commit()
        return registro

# ====== MÓDULO CARTERA / CLIENTES CRÉDITO POS ======
class ClienteCartera(db.Model):
    __tablename__ = 'cliente_cartera'
    
    id = db.Column(db.Integer, primary_key=True)
    nombre_completo = db.Column(db.String(150), nullable=False)
    telefono = db.Column(db.String(50), nullable=False)
    fecha_registro = db.Column(db.DateTime, default=obtener_hora_bogota)
    
    facturas = db.relationship('FacturaCredito', backref='cliente', lazy=True, cascade='all, delete-orphan')

    @property
    def saldo_total(self):
        return sum(f.saldo_pendiente for f in self.facturas)

    @property
    def estado_cartera(self):
        """Calcula el estado: Al día (sin deuda), Pendiente (con deuda reciente), En mora (>15 días sin abonos)."""
        ahora = obtener_hora_bogota()
        tiene_deuda = False
        
        for factura in self.facturas:
            if factura.saldo_pendiente > 0:
                tiene_deuda = True
                # Buscar último abono
                ultimo_abono = AbonoCredito.query.filter_by(factura_id=factura.id).order_by(AbonoCredito.fecha_abono.desc()).first()
                fecha_referencia = ultimo_abono.fecha_abono if ultimo_abono else factura.fecha_emision
                
                # Calcular diferencia de días
                dias_inactivo = (ahora - fecha_referencia).days
                if dias_inactivo > 15:
                    return 'En mora'
        
        if tiene_deuda:
            return 'Pendiente'
            
        return 'Al día'

    @property
    def tiene_acuerdos_vencidos(self):
        """Verifica si el cliente tiene compromisos de pago incumplidos a la fecha."""
        ahora = obtener_hora_bogota().date()
        for factura in self.facturas:
            for acuerdo in factura.acuerdos:
                if not acuerdo.cumplido and acuerdo.fecha_acordada < ahora:
                    return True
        return False

class FacturaCredito(db.Model):
    __tablename__ = 'factura_credito'
    
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente_cartera.id'), nullable=False)
    total_factura = db.Column(db.Numeric(14, 2), nullable=False)
    saldo_pendiente = db.Column(db.Numeric(14, 2), nullable=False)
    fecha_emision = db.Column(db.DateTime, default=obtener_hora_bogota)
    
    detalles = db.relationship('DetalleFacturaCredito', backref='factura', lazy=True, cascade='all, delete-orphan')
    abonos = db.relationship('AbonoCredito', backref='factura', lazy=True, cascade='all, delete-orphan')
    acuerdos = db.relationship('AcuerdoPago', backref='factura', lazy=True, cascade='all, delete-orphan')

    @property
    def dias_sin_abono(self):
        ahora = obtener_hora_bogota()
        ultimo_abono = AbonoCredito.query.filter_by(factura_id=self.id).order_by(AbonoCredito.fecha_abono.desc()).first()
        fecha_referencia = ultimo_abono.fecha_abono if ultimo_abono else self.fecha_emision
        return (ahora - fecha_referencia).days

class DetalleFacturaCredito(db.Model):
    __tablename__ = 'detalle_factura_credito'
    
    id = db.Column(db.Integer, primary_key=True)
    factura_id = db.Column(db.Integer, db.ForeignKey('factura_credito.id'), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=True)
    variant_id = db.Column(db.Integer, db.ForeignKey('product_variants.id'), nullable=True)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Numeric(14, 2), nullable=False)
    subtotal = db.Column(db.Numeric(14, 2), nullable=False)
    nombre_manual = db.Column(db.String(200), nullable=True)

    producto = db.relationship('Product', backref='detalles_factura_cartera', lazy=True)
    variante = db.relationship('ProductVariant', backref='detalles_factura_cartera', lazy=True)

class AbonoCredito(db.Model):
    __tablename__ = 'abono_credito'
    
    id = db.Column(db.Integer, primary_key=True)
    factura_id = db.Column(db.Integer, db.ForeignKey('factura_credito.id'), nullable=False)
    monto_abono = db.Column(db.Numeric(14, 2), nullable=False)
    fecha_abono = db.Column(db.DateTime, default=obtener_hora_bogota)
    
    # Relación inversa con el movimiento de caja para trazabilidad
    movimiento_caja = db.relationship('MovimientoCajaCartera', backref='abono', uselist=False, lazy=True, cascade="all, delete-orphan")

class AcuerdoPago(db.Model):
    __tablename__ = 'acuerdo_pago'
    
    id = db.Column(db.Integer, primary_key=True)
    factura_id = db.Column(db.Integer, db.ForeignKey('factura_credito.id'), nullable=False)
    fecha_acordada = db.Column(db.Date, nullable=False)
    monto_esperado = db.Column(db.Numeric(14, 2), nullable=True)
    cumplido = db.Column(db.Boolean, default=False)

class MovimientoCajaCartera(db.Model):
    """Integra los abonos de cartera con el flujo de caja diario."""
    __tablename__ = 'movimiento_caja_cartera'
    
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), default='Ingreso')
    monto = db.Column(db.Numeric(14, 2), nullable=False)
    concepto = db.Column(db.String(255), nullable=False)
    fecha_movimiento = db.Column(db.DateTime, default=obtener_hora_bogota)
    abono_id = db.Column(db.Integer, db.ForeignKey('abono_credito.id'), nullable=False)

class ServerPayment(db.Model):
    """Modelo para registrar el historial y estado del pago mensual del servidor (Zenic)."""
    __tablename__ = 'server_payments'
    
    id = db.Column(db.Integer, primary_key=True)
    anio = db.Column(db.Integer, nullable=False)
    mes = db.Column(db.Integer, nullable=False)
    estado = db.Column(db.String(20), nullable=False, default='pagado') # 'pagado' | 'pendiente'
    fecha_pago = db.Column(db.DateTime, default=obtener_hora_bogota)
    observacion = db.Column(db.String(255), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('anio', 'mes', name='uq_server_payment_anio_mes'),
    )

    def __init__(self, anio=None, mes=None, estado='pagado', fecha_pago=None, observacion=None, **kwargs):
        super().__init__(**kwargs)
        if anio is not None:
            self.anio = anio
        if mes is not None:
            self.mes = mes
        if estado is not None:
            self.estado = estado
        if fecha_pago is not None:
            self.fecha_pago = fecha_pago
        else:
            self.fecha_pago = obtener_hora_bogota()
        if observacion is not None:
            self.observacion = observacion


