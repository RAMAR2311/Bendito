from datetime import datetime

import pytz
from werkzeug.security import generate_password_hash

from app import create_app
from models import (
    AbonoBodega,
    ArqueoCaja,
    Cliente,
    Expense,
    FacturaBodega,
    FacturaBodegaDetalle,
    Loss,
    Maneo,
    Product,
    ProductVariant,
    Provider,
    ProviderInvoice,
    ProviderPayment,
    Sale,
    SaleDetail,
    SalePayment,
    StockAdjustment,
    User,
    Warranty,
    db,
)


def obtener_hora_bogota():
    return datetime.now(pytz.timezone('America/Bogota')).replace(tzinfo=None)

def seed_test_data():
    app = create_app()
    with app.app_context():
        print("Iniciando inyección de datos de prueba...")
        
        # 1. Crear Usuarios de Prueba si no existen
        roles = [
            {'email': 'vendedor@bendito.com', 'nombre': 'Vendedor Oficial', 'rol': 'vendedor', 'pass': 'Bendito1234'},
            {'email': 'bodega@bendito.com', 'nombre': 'Encargado Bodega', 'rol': 'bodega', 'pass': 'Bendito1234'},
            {'email': 'admin@bendito.com', 'nombre': 'Administrador General', 'rol': 'admin', 'pass': 'Bendito2026*'}
        ]
        test_users = {}
        for r in roles:
            user = User.query.filter_by(email=r['email']).first()
            if not user:
                user = User(
                    nombre=r['nombre'],
                    email=r['email'],
                    password_hash=generate_password_hash(r['pass']),
                    rol=r['rol'],
                    telefono='3000000000'
                )
                db.session.add(user)
                db.session.commit()
            test_users[r['rol']] = user
        print("[OK] Usuarios creados/verificados (admin@tekfix, bodega@tekfix, vendedor@tekfix).")

        # 2. Crear Productos y Variantes
        if Product.query.filter(Product.sku.like('DEMO-%')).count() == 0:
            prod_tienda = Product(nombre='Funda Protectora Demo', sku='DEMO-FUNDA-01', tipo_inventario='tienda', cantidad_stock=50, precio_costo=5000, precio_minimo=15000, precio_sugerido=20000, observacion='Producto demo tienda')
            prod_bodega = Product(nombre='Pantalla iPhone 13 Demo', sku='DEMO-PAN-IP13', tipo_inventario='bodega', cantidad_stock=10, precio_costo=150000, precio_minimo=250000, precio_sugerido=280000, observacion='Producto demo bodega')
            prod_variantes = Product(nombre='Audífonos Inalámbricos Demo', sku='DEMO-AUDI-01', tipo_inventario='tienda', cantidad_stock=0, precio_costo=30000, precio_minimo=50000, precio_sugerido=60000)
            
            db.session.add_all([prod_tienda, prod_bodega, prod_variantes])
            db.session.commit()
            
            # Variantes
            var1 = ProductVariant(product_id=prod_variantes.id, nombre_variante='Negro', cantidad_stock=20, precio_costo=30000, precio_minimo=50000, precio_sugerido=60000)
            var2 = ProductVariant(product_id=prod_variantes.id, nombre_variante='Blanco', cantidad_stock=15, precio_costo=30000, precio_minimo=50000, precio_sugerido=60000)
            db.session.add_all([var1, var2])
            db.session.commit()
            print("[OK] Productos y Variantes Demo creados.")
        else:
            prod_tienda = Product.query.filter_by(sku='DEMO-FUNDA-01').first()
            prod_bodega = Product.query.filter_by(sku='DEMO-PAN-IP13').first()
            prod_variantes = Product.query.filter_by(sku='DEMO-AUDI-01').first()

        # 3. Proveedores, Facturas y Pagos
        prov = Provider.query.filter_by(nombre='Proveedor Demo S.A.').first()
        if not prov:
            prov = Provider(nombre='Proveedor Demo S.A.', empresa='Tech Supplies Demo', telefono='3120000000')
            db.session.add(prov)
            db.session.commit()
            
            p_invoice = ProviderInvoice(provider_id=prov.id, monto_total=500000, numero_factura='FAC-PROV-001', descripcion='Compra de repuestos demo', comprobante='')
            db.session.add(p_invoice)
            db.session.commit()
            
            p_payment = ProviderPayment(provider_id=prov.id, monto_abonado=200000, observacion='Abono inicial demo')
            db.session.add(p_payment)
            db.session.commit()
            print("[OK] Proveedor, Factura de Proveedor y Abono creados.")

        # 4. Bodega (Clientes, Facturas, Abonos)
        cliente = Cliente.query.filter_by(documento_o_nit='900000000-1').first()
        if not cliente:
            cliente = Cliente(nombre_o_razon_social='Cliente Mayorista Demo', documento_o_nit='900000000-1', telefono='3200000000', email='mayorista@demo.com', direccion='Calle Falsa 123')
            db.session.add(cliente)
            db.session.commit()
            
            # Simulamos un archivo pdf faltante
            f_bodega = FacturaBodega(cliente_id=cliente.id, usuario_id=test_users['bodega'].id, numero_factura='BOD-DEMO-001', archivo_ruta='N/A', monto_total=300000, estado='Parcial')
            db.session.add(f_bodega)
            db.session.commit()
            
            fb_detalle = FacturaBodegaDetalle(factura_id=f_bodega.id, producto_id=prod_bodega.id, cantidad=1, precio_venta=280000)
            db.session.add(fb_detalle)
            
            a_bodega = AbonoBodega(factura_id=f_bodega.id, usuario_id=test_users['bodega'].id, monto=150000, metodo_pago='nequi', observacion='Abono por Nequi demo')
            db.session.add(a_bodega)
            db.session.commit()
            print("[OK] Cliente Bodega, Factura Bodega y Abono creados.")

        # 5. Ventas (Sales, Sales Payment, Sale Detail)
        venta = Sale(vendedor_id=test_users['vendedor'].id, monto_total=40000, metodo_pago='efectivo')
        db.session.add(venta)
        db.session.commit()
        
        # Pagos de Venta (Pago simple y mixto)
        vp_efectivo = SalePayment(sale_id=venta.id, metodo_pago='efectivo', monto=40000)
        db.session.add(vp_efectivo)
        
        # Detalles de Venta
        v_det1 = SaleDetail(sale_id=venta.id, product_id=prod_tienda.id, cantidad_vendida=2, precio_venta_final=20000)
        db.session.add(v_det1)
        db.session.commit()

        venta2 = Sale(vendedor_id=test_users['vendedor'].id, monto_total=60000, metodo_pago='mixto')
        db.session.add(venta2)
        db.session.commit()

        # Pago mixto
        vp2_1 = SalePayment(sale_id=venta2.id, metodo_pago='efectivo', monto=30000)
        vp2_2 = SalePayment(sale_id=venta2.id, metodo_pago='bancolombia', monto=30000)
        db.session.add_all([vp2_1, vp2_2])

        var_demo = ProductVariant.query.filter_by(product_id=prod_variantes.id, nombre_variante='Negro').first()
        v_det2 = SaleDetail(sale_id=venta2.id, product_id=prod_variantes.id, variant_id=var_demo.id, cantidad_vendida=1, precio_venta_final=60000)
        db.session.add(v_det2)
        db.session.commit()
        print("[OK] Ventas, Pagos Mixtos y Detalles creados.")

        # 6. Garantías
        garantia = Warranty(sale_id=venta.id, product_id=prod_tienda.id, quantity=1, reason='Rayón de fábrica (Demo)', resolution='Cambio')
        db.session.add(garantia)
        db.session.commit()
        print("[OK] Garantía creada.")

        # 7. Gastos
        gasto1 = Expense(usuario_id=test_users['vendedor'].id, tipo_gasto='Gasto Diario', categoria='Alimentación', descripcion='Almuerzo vendedor demo', monto=15000)
        gasto2 = Expense(usuario_id=test_users['admin'].id, tipo_gasto='Costo Indirecto', categoria='Servicios', descripcion='Pago internet demo', monto=80000)
        db.session.add_all([gasto1, gasto2])
        db.session.commit()
        print("[OK] Gastos registrados.")

        # 8. Pérdidas
        perdida = Loss(product_id=prod_tienda.id, quantity=1, cost_at_loss=5000, reason='Producto roto accidentalmente (Demo)')
        db.session.add(perdida)
        db.session.commit()
        print("[OK] Pérdida registrada.")

        # 9. Maneos (Préstamos Locales)
        maneo = Maneo(product_id=prod_tienda.id, local_vecino='Local 102 (Demo)', cantidad=2, estado='PENDIENTE')
        db.session.add(maneo)
        db.session.commit()
        print("[OK] Maneo (Préstamo a local vecino) creado.")

        # 10. Ajustes de Stock
        ajuste = StockAdjustment(product_id=prod_tienda.id, admin_id=test_users['admin'].id, tipo_movimiento='Ajuste Manual Demo', stock_anterior=50, stock_nuevo=48)
        db.session.add(ajuste)
        db.session.commit()
        print("[OK] Ajuste de Stock registrado.")

        # 11. Arqueo de Caja
        arqueo = ArqueoCaja(vendedor_id=test_users['vendedor'].id, fecha_arqueo=obtener_hora_bogota().date(), base_inicial=100000, gastos_del_dia=15000, observaciones_gastos='Almuerzo', total_efectivo_sistema=170000, total_transferencia_sistema=30000)
        db.session.add(arqueo)
        db.session.commit()
        print("[OK] Arqueo de Caja registrado.")

        print("[EXITO] ¡Datos de prueba generados exitosamente! Ya puedes realizar la demostración.")

if __name__ == '__main__':
    seed_test_data()
