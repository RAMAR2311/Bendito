# Application entry point - Sanitized
import os

from dotenv import load_dotenv
from flask import Flask, render_template, request
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect

from models import User, db, obtener_hora_bogota

load_dotenv()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-bendito')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost/benditoencanto')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)
    Migrate(app, db)
    CSRFProtect(app)
    
    login_manager = LoginManager()
    login_manager.login_view = 'auth_bp.login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # Registro de Blueprints
    from routes.arqueo import arqueo_bp
    from routes.auth import auth_bp
    from routes.clientes import clientes_bp
    from routes.gastos import gastos_bp
    from routes.importaciones import importaciones_bp
    from routes.inventory import inventory_bp
    from routes.sales import sales_bp
    from routes.warranties import warranties_bp
    
    app.register_blueprint(sales_bp, url_prefix='/sales')
    app.register_blueprint(inventory_bp, url_prefix='/inventory')
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(arqueo_bp, url_prefix='/arqueo')
    app.register_blueprint(gastos_bp, url_prefix='/gastos')
    app.register_blueprint(warranties_bp, url_prefix='/garantias')
    app.register_blueprint(importaciones_bp, url_prefix='/importaciones')
    app.register_blueprint(clientes_bp, url_prefix='/clientes')
    
    # Registro de Blueprint Admin
    from routes.admin import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Registro de Blueprint Bodega
    from routes.bodega import bodega_bp
    app.register_blueprint(bodega_bp, url_prefix='/bodega')

    @app.template_filter('cop')
    def cop_filter(value):
        if value is None:
            return "0"
        try:
            return f"{float(value):,.0f}"
        except (ValueError, TypeError):
            return value

    @app.context_processor
    def inject_pago_servidor():
        try:
            import calendar
            from datetime import date
            from urllib.parse import quote

            from itsdangerous import URLSafeTimedSerializer

            from models import ServerPayment

            VALOR_MENSUALIDAD = 70000
            VALOR_FMT = "$70.000 COP"
            DIA_VENCIMIENTO = 30
            TELEFONO_ZENIC = "573115643557"
            MESES = [
                "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
            ]

            ahora = obtener_hora_bogota()
            hoy = ahora.date()

            # Determinar el mes y año que se están evaluando.
            # Si el mes previo no ha sido marcado como pagado, la prioridad es cobrar el mes previo.
            prev_mes = 12 if hoy.month == 1 else hoy.month - 1
            prev_anio = hoy.year - 1 if hoy.month == 1 else hoy.year

            pago_prev = ServerPayment.query.filter_by(anio=prev_anio, mes=prev_mes).first()

            if pago_prev and pago_prev.estado == 'pagado':
                target_anio = hoy.year
                target_mes = hoy.month
            else:
                target_anio = prev_anio
                target_mes = prev_mes

            pago = ServerPayment.query.filter_by(anio=target_anio, mes=target_mes).first()

            if pago and pago.estado == 'pagado':
                estado = 'al_dia'
                dias_restantes = 0
                mensaje_estado = "Al día"
            else:
                _, max_dias_mes = calendar.monthrange(target_anio, target_mes)
                max_dia = min(DIA_VENCIMIENTO, max_dias_mes)
                fecha_venc = date(target_anio, target_mes, max_dia)
                diff_days = (hoy - fecha_venc).days

                if diff_days > 5:
                    estado = 'vencido'
                    dias_restantes = diff_days
                    mensaje_estado = f"Vencido por {diff_days} días"
                elif 1 <= diff_days <= 5:
                    estado = 'gabela'
                    dias_restantes = 5 - (diff_days - 1)
                    mensaje_estado = f"Período de gracia: {dias_restantes} día(s) restante(s)"
                elif diff_days == 0:
                    estado = 'hoy'
                    dias_restantes = 0
                    mensaje_estado = "Hoy vence la mensualidad del servidor"
                else:
                    dias_faltantes = abs(diff_days)
                    if dias_faltantes <= 8:
                        estado = 'preventivo'
                        dias_restantes = dias_faltantes
                        mensaje_estado = f"Vence en {dias_faltantes} día(s)"
                    else:
                        estado = 'al_dia'
                        dias_restantes = dias_faltantes
                        mensaje_estado = "Al día"

            s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
            token = s.dumps({'anio': target_anio, 'mes': target_mes}, salt='servidor-pago-salt')
            
            try:
                base_url = request.host_url.rstrip('/')
            except Exception:
                base_url = "http://localhost:5000"

            approval_url = f"{base_url}/servidor/confirmar-pago?token={token}"

            nombre_mes = MESES[target_mes] if 1 <= target_mes <= 12 else str(target_mes)
            whatsapp_msg = (
                f"Hola Zenic, adjunto la aprobación del pago de la mensualidad del servidor "
                f"correspondiente a {nombre_mes} {target_anio} por {VALOR_FMT}.\n\n"
                f"Confirmar pago con 1-clic aquí:\n{approval_url}"
            )
            whatsapp_url = f"https://wa.me/{TELEFONO_ZENIC}?text={quote(whatsapp_msg)}"

            pago_servidor = {
                'estado': estado,
                'dias_restantes': dias_restantes,
                'mensaje_estado': mensaje_estado,
                'valor_fmt': VALOR_FMT,
                'valor': VALOR_MENSUALIDAD,
                'mes_nombre': nombre_mes,
                'anio': target_anio,
                'mes': target_mes,
                'whatsapp_url': whatsapp_url,
                'approval_url': approval_url,
                'nu_tag': '@QEI910',
                'nequi': '3505422186'
            }

            return {'pago_servidor': pago_servidor}
        except Exception:
            return {'pago_servidor': None}

    @app.route('/servidor/confirmar-pago')
    def confirmar_pago_servidor():
        from itsdangerous import URLSafeTimedSerializer

        from models import ServerPayment
        token = request.args.get('token')
        if not token:
            return render_template('servidor/pago_confirmado.html', exito=False, error="Token de confirmación no proporcionado.")
        
        s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
        try:
            data = s.loads(token, salt='servidor-pago-salt')
            anio = data.get('anio')
            mes = data.get('mes')
            
            pago = ServerPayment.query.filter_by(anio=anio, mes=mes).first()
            if not pago:
                pago = ServerPayment(anio=anio, mes=mes, estado='pagado', fecha_pago=obtener_hora_bogota())
                db.session.add(pago)
            else:
                pago.estado = 'pagado'
                pago.fecha_pago = obtener_hora_bogota()
                
            db.session.commit()
            MESES = [
                "", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
            ]
            nombre_mes = MESES[mes] if 1 <= mes <= 12 else str(mes)
            return render_template('servidor/pago_confirmado.html', exito=True, mes_nombre=nombre_mes, anio=anio)
        except Exception:
            return render_template('servidor/pago_confirmado.html', exito=False, error="El enlace de confirmación no es válido o ha expirado.")

    from flask_login import login_required

    @app.route('/')
    @login_required
    def index():
        return render_template('index.html')

    return app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
