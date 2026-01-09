# app.py
import os
from pathlib import Path
from nicegui import app, ui
from fastapi.staticfiles import StaticFiles

# Modele i inicjalizacja bazy
from models import init_db
from uzytkownicy import create_default_user

# Interfejsy
from uzytkownicy import user_management_interface, is_authenticated, change_my_password_ui
from strumienie import streams_management_interface
from wideo import archive_interface, live_grid_interface
from backend import system_info_ui, run_retention_task

# 1. KONFIGURACJA ŚRODOWISKA (Globalna dla serwera)
DOMAIN = os.getenv('DOMAIN', 'localhost')
STORAGE_SECRET = os.getenv('STORAGE_SECRET', 'super_secret_firanka')
RECORDINGS_DIR = Path("/recordings")

# 2. INICJALIZACJA SYSTEMOWA
init_db()            # Tworzy tabele
create_default_user() # Tworzy admina

# 3. MOUNTOWANIE PLIKÓW (Serwer musi wiedzieć, skąd brać wideo i ikony)
RECORDINGS_DIR.mkdir(exist_ok=True)
if not os.path.exists('static'):
    os.makedirs('static', exist_ok=True)

# NiceGUI Static Files (Zalecane)
app.add_static_files('/recordings', str(RECORDINGS_DIR))
app.add_static_files('/static', 'static')

@app.post('/auth')
async def mediamtx_auth(request: Request):
    try:
        body = await request.body()
        data = json.loads(body)
        
        # 1. Pobieramy dane z głównych pól
        user_login = data.get('user')
        user_pass = data.get('password')
        stream_path = data.get('path')
        action = data.get('action')
        
        # 2. Jeśli pola są puste, szukamy w 'query' (kluczowe dla RTMP!)
        query_str = data.get('query', '')
        if query_str:
            params = parse_qs(query_str)
            if not user_login and 'user' in params:
                user_login = params['user'][0]
            if not user_pass and 'password' in params:
                user_pass = params['password'][0]

        print(f"--- PRÓBA AUTORYZACJI ---", flush=True)
        print(f"User: {user_login}, Pass: {'****' if user_pass else 'BRAK'}, Path: {stream_path}", flush=True)

        with SessionLocal() as db:
            # Weryfikacja użytkownika
            user = db.query(User).filter(User.username == user_login, User.password == user_pass).first()
            if not user:
                print(f"AUTH: Błędne poświadczenia dla {user_login}", flush=True)
                return Response(status_code=401)

            # Weryfikacja strumienia
            # Używamy func.lower, aby uniknąć problemów z wielkością liter (istebna vs Istebna)
            from sqlalchemy import func
            stream = db.query(StreamPath).filter(func.lower(StreamPath.path_name) == func.lower(stream_path)).first()
            
            if not stream:
                if user.role == 'admin':
                    print(f"AUTH: Admin tworzy nową ścieżkę: {stream_path}", flush=True)
                    return Response(status_code=200)
                print(f"AUTH: Strumień {stream_path} nie istnieje w bazie!", flush=True)
                return Response(status_code=401)

            # Sprawdzenie uprawnień
            if action == 'publish':
                if user.role == 'admin' or user in stream.authorized_publishers or stream.owner_username == user.username:
                    print(f"AUTH: PUBLISH OK: {user_login}", flush=True)
                    return Response(status_code=200)
            
            elif action == 'read':
                if user.role == 'admin' or user in stream.authorized_viewers or user in stream.authorized_publishers:
                    print(f"AUTH: READ OK: {user_login}", flush=True)
                    return Response(status_code=200)

        return Response(status_code=401)

    except Exception as e:
        print(f"BŁĄD AUTH: {str(e)}", flush=True)
        return Response(status_code=401)

# --- INTERFEJS UŻYTKOWNIKA (NiceGUI) ---
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    
    # DEBUG: Zobaczymy w logach co się dzieje
    is_authenticated = app.storage.user.get('authenticated', False)
    
    # Sprawdzamy wyjątki
    if path.startswith('/_nicegui') or path.startswith('/static') or path in ['/login', '/auth']:
        print(f"[DEBUG] ALLOWED: {path} (Auth: {is_authenticated})")
        return await call_next(request)

    if not is_authenticated:
        print(f"[DEBUG] REDIRECTING: {path} -> /login")
        return responses.RedirectResponse('/login')

    print(f"[DEBUG] PASSING: {path}")
    return await call_next(request)

@ui.page('/login')
def login_page():
    # To ustawia czarne tło dla całej strony
    ui.query('body').style('background: #000;')
    
    with ui.card().classes('absolute-center bg-zinc-900 border border-zinc-800 p-8 text-white w-96'):
        ui.label('Istebna Drone VMS').classes('text-2xl font-black mb-4 text-center text-blue-500')
        
        u = ui.input('Użytkownik').classes('w-full').props('dark')
        p = ui.input('Hasło', password=True).classes('w-full').props('dark')
        
        async def do_login():
            with SessionLocal() as db:
            # Szukamy użytkownika w bazie
                user = db.query(User).filter(
                    User.username == u.value, 
                    User.password == p.value
                ).first()
        
                if user:
                    # Zapisujemy komplet danych w bezpiecznym magazynie sesji
                    app.storage.user.update({
                        'authenticated': True, 
                        'username': user.username, 
                        'role': user.role,
                        'user_id': user.id  # Kluczowe dla filtrowania streamów w Gridzie
                    })
            
                    ui.notify(f'Witaj {user.username}! System operacyjny gotowy.', color='positive', icon='check_circle')
            
                     # Przekierowanie NiceGUI - czyści stan starej strony i ładuje '/' z nową sesją
                    ui.navigate.to('/')
                else:
                    ui.notify('Błędne dane – sąsiadka Cię nie wpuści!', color='negative', icon='warning')
        
        # Obsługa Entera na obu polach
        u.on('keydown.enter', do_login)
        p.on('keydown.enter', do_login)
        
        ui.button('ZALOGUJ', on_click=do_login).classes('w-full mt-6 bg-blue-600 hover:bg-blue-700 text-white font-bold')
     
# Główna strona
@ui.page('/')
def main_page():
    # 1. Strażnik sesji - jeśli nie ma kompletu danych, wyrzuca do logowania
    if not is_authenticated():
        return ui.navigate.to('/login')

    # 2. Jednokrotne pobranie danych (wiemy, że istnieją dzięki is_authenticated)
    username = app.storage.user.get('username')
    role = app.storage.user.get('role')

    # 3. Stylizacja globalna (raz, konkretnie)
    ui.query('body').style('background-color: #000000;')
    ui.query('.q-page').style('background-color: #000000;')

    # --- NAGŁÓWEK SYSTEMU ---
    with ui.header().classes('bg-zinc-900 border-b border-zinc-800 items-center justify-between p-4'):
        with ui.row().classes('items-center gap-4'):
            # Twoje logo 38kB z app/static/logo.png
            ui.image('/static/logo.png').classes('w-12 h-12')
            with ui.column().classes('gap-0'):
                ui.label('Ochotnicza Straż Pożarna Istebna-Centrum').classes('text-l font-black text-white leading-none')
                ui.label('Centrum Monitoringu Wizyjnego BSP').classes('text-[12px] text-blue-500 font-bold tracking-widest uppercase')

        with ui.row().classes('items-center gap-3'):
            ui.label(f"OPERATOR: {username.upper()}").classes('text-[12px] text-zinc-500 font-mono')
            ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props('flat round color=red-8')
    # 2. Zakładki (Tabs)
    with ui.tabs().classes('w-full bg-zinc-900 text-zinc-400 border-b border-zinc-800') as tabs:
        t_grid = ui.tab('GRID OPERACYJNY', icon='grid_view')
        if user_role in ['admin', 'operator']:
            t_archive = ui.tab('ARCHIWUM', icon='history')
            t_streams = ui.tab('STRUMIENIE', icon='videocam')
        if user_role == 'admin':
            t_admin = ui.tab('ZARZĄDZANIE', icon='settings')

    # 3. Panele (Tab Panels)
    with ui.tab_panels(tabs, value=t_grid).classes('w-full bg-black text-zinc-300'):
        
        with ui.tab_panel(t_grid).classes('p-0'):
            live_grid_interface(username, role)

        if role in ['admin', 'operator']:
            with ui.tab_panel(t_archive):
                archive_interface(username, role)
            with ui.tab_panel(t_streams).classes('gap-8'):
                system_info_ui()
                ui.separator().classes('bg-zinc-800 my-4')
                streams_management_interface(username, role)
                if role == 'admin':
                    ui.separator().classes('bg-zinc-800 my-4')
                    ui.label('ADMINISTRACJA DANYMI').classes('text-zinc-500 text-xs font-bold tracking-widest')
                    retention_settings_ui() # <--- TU DOPINAMY NOWY PANEL
            with ui.tab_panel(t_admin):
                # 2. MOJE KONTO (Dla każdego zalogowanego - zmiana własnego hasła)
                change_my_password_ui(username)
                if role == 'admin':
                    ui.separator().classes('bg-zinc-800 my-4')
                    user_management_interface()

# Wykonaj retencję danych nagrań na dysk GDrive co 5 dni
ui.timer(432000.0, run_retention_task)

# START SYSTEMU
if __name__ in {"__main__", "__mp_main__"}:
# Wywołaj to przed ui.run()
    create_default_user()
    ui.run(
        host='0.0.0.0', 
        port=8080, 
        title='Wiejski Drone VMS do podglądania sąsiadek',
        storage_secret='PesaToNajgorszyProducentTaboruNaSwiecie',
        favicon='static/logo.png',
        reload=False  # Wyłączamy reload wewnątrz Dockera dla większej stabilności
    )