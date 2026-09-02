import os
import sqlite3

db_path = 'instance/crm_inventory.db'

tables_to_clear = [
    'sale_details', 'sale_payments', 'warranties', 'sales',
    'expenses', 'losses', 'arqueo_caja', 'dynamic_keys',
    'facturas_bodega_detalles', 'abonos_bodega', 'facturas_bodega',
    'provider_invoices', 'provider_payments', 'stock_adjustments', 
    'maneos'
]

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for table in tables_to_clear:
        try:
            cursor.execute(f"DELETE FROM {table};")
            # Intenta resetear la secuencia de auto-increment (solo si existe)
            cursor.execute(f"UPDATE sqlite_sequence SET seq = 0 WHERE name = '{table}';")
        except Exception as e:
            print(f"Nota en {table}: {e}")

    conn.commit()
    conn.close()
    print("Limpieza completada. Todas las transacciones se han eliminado.")
else:
    print(f"No se encontró la base de datos en {db_path}.")
