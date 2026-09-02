from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required, login_user, logout_user
from werkzeug.security import check_password_hash

from models import User

auth_bp = Blueprint('auth_bp', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Consultamos el usuario
        user = User.query.filter_by(email=email).first()
        
        # Validar contraseña cifrada
        if user and check_password_hash(user.password_hash, password):
            login_user(user) # Iniciar sesión del lado del servidor persistente
            flash('¡Sesión iniciada con éxito!', 'success')
            
            # Redirección inteligente basada en ROL o hacia la raíz para delegar el control
            return redirect(url_for('index'))
        
        flash('Tus credenciales (Correo o Contraseña) son inválidas.', 'error')
        return redirect(url_for('auth_bp.login'))
        
    # GET: Mostrar Formulario normal
    return render_template('auth/login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user() # Rompe la sesión
    flash('¡Has cerrado tu sesión de manera segura!', 'success')
    return redirect(url_for('auth_bp.login'))
