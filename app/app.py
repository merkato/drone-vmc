import os
import time
import shutil
import psutil
import httpx
import logging
from urllib.parse import parse_qs
from pathlib import Path
from collections import defaultdict
from datetime import datetime

from fastapi import Request, HTTPException, responses
from nicegui import app, ui, core
from sqlalchemy import create_engine, Column, String, Boolean, Table, ForeignKey, Integer, Text
from sqlalchemy.orm import sessionmaker, relationship, declarative_base

# --- KONFIGURACJA ŚRODOWISKA ---
DOMAIN = os.getenv('DOMAIN', 'localhost')
STORAGE_SECRET = os.getenv('STORAGE_SECRET', 'super_secret_firanka')
MEDIAMTX_API = "http://mediamtx:9997/v3"
MEDIAMTX_WEBRTC = f"https://stream.{DOMAIN}"
RECORDINGS_DIR = Path("/recordings")

# Inicjalizacja folderów i plików statycznych
RECORDINGS_DIR.mkdir(exist_ok=True)
app.add_static_files('/download', str(RECORDINGS_DIR))
app.add_static_files('/static', 'static')

# --- BAZA DANYCH (SQLAlchemy) ---
DB_URL = "sqlite:///./streaming_v3.db"
Base = declarative_base()
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)



# Tabela asocjacyjna Uprawnień (Many-to-Many)
stream_permissions = Table('stream_permissions', Base.metadata,
    Column('user_id', String, ForeignKey('users.username', ondelete="CASCADE")),
    Column('stream_id', String, ForeignKey('streams.path_name', ondelete="CASCADE"))
)

class User(Base):
    __tablename__ = "users"
    username = Column(String, primary_key=True)
    password = Column(String)
    role = Column(String) # 'admin', 'publisher', 'widz'
    allowed_streams = relationship("StreamPath", secondary=stream_permissions, back_populates="authorized_publishers")

class StreamPath(Base):
    __tablename__ = "streams"
    path_name = Column(String, primary_key=True)
    description = Column(String)
    is_recording = Column(Boolean, default=False)
    authorized_publishers = relationship("User", secondary=stream_permissions, back_populates="allowed_streams")

class SystemConfig(Base):
    __tablename__ = "system_config"
    id = Column(Integer, primary_key=True)
    institution_name = Column(String, default="Ochotnicza Straż Pożarna Istebna-Centrum")
    unit_name = Column(String, default="Zespół BSP")
    retention_policy = Column(String, default="DELETE") # "DELETE" lub "BACKUP"
    gdrive_folder_id = Column(String, nullable=True)

Base.metadata.create_all(bind=engine)

# --- LOGIKA BACKENDU: MONITORING I STATYSTYKI ---
stats_history = defaultdict(lambda: [])
last_bytes = {}
alert_logs = []

def add_alert(drone, msg, level='warning'):
    alert = {'time': datetime.now().strftime('%H:%M:%S'), 'drone': drone, 'msg': msg, 'level': level}
    alert_logs.insert(0, alert)
    if len(alert_logs) > 20: alert_logs.pop()
    ui.notify(f"[{drone}] {msg}", type='negative' if level=='critical' else 'warning')

def get_sys_resources():
    total, used, free = shutil.disk_usage(RECORDINGS_DIR)
    return {
        'cpu': psutil.cpu_percent(),
        'ram': psutil.virtual_memory().percent,
        'disk_pct': (used/total)*100,
        'disk_free': free // (2**30)
    }

# --- LOGIKA BACKENDU: AUTORYZACJA MEDIAMTX ---
@app.post('/auth')
async def media_mtx_auth(request: Request):
    try:
        data = await request.json()
    except:
        return responses.JSONResponse(content={"error": "Invalid JSON"}, status_code=400)

    # 1. Sprawdź użytkownika w głównym polu 'user'
    u_val = data.get('user')
    p_val = data.get('password')

    # 2. Jeśli puste, szukaj w polu 'query' (które jest stringiem)
    query_str = data.get('query')
    if query_str and (not u_val or not p_val):
        # Zamieniamy string "user=jacek&password=123" na słownik
        parsed_query = parse_qs(query_str)
        u_val = u_val or parsed_query.get('user', [None])[0]
        p_val = p_val or parsed_query.get('password', [None])[0]

    # Tutaj Twoja dalsza logika weryfikacji w bazie danych...
    # if check_user(u_val, p_val): return responses.Response(status_code=200)
    
    print(f"Auth attempt for user: {u_val}") # Debug w logach
    
    # Dla testów, jeśli chcesz wpuścić każdego (usuń to później!):
    # return responses.Response(status_code=200)
    
    # Prawidłowa weryfikacja (uproszczona):
    if u_val == "admin" and p_val == "admin": # Przykład
         return responses.Response(status_code=200)
         
    return responses.Response(status_code=401)

def management_page():
    ui.label('Zarządzanie Systemem').classes('text-3xl font-bold mb-6 text-white')

    with ui.row().classes('w-full items-stretch'):
        
        # --- SEKCJA 1: ZMIANA MOJEGO HASŁA ---
        with ui.card().classes('bg-zinc-900 border border-zinc-800 text-white p-6 w-full md:w-1/3'):
            ui.label('Zmień moje hasło').classes('text-xl font-bold mb-4 text-blue-400')
            new_pass = ui.input('Nowe hasło', password=True).classes('w-full').props('dark')
            
            async def update_my_password():
                with SessionLocal() as db:
                    user = db.query(User).filter(User.username == app.storage.user['username']).first()
                    if user:
                        user.password = new_pass.value
                        db.commit()
                        ui.notify('Hasło zmienione pomyślnie!', color='positive')
                        new_pass.value = ''
            
            ui.button('ZAKTUALIZUJ', on_click=update_my_password).classes('w-full mt-4 bg-blue-600')

        # --- SEKCJA 2: DODAWANIE UŻYTKOWNIKA (TYLKO DLA ADMINA) ---
        if app.storage.user.get('role') == 'admin':
            with ui.card().classes('bg-zinc-900 border border-zinc-800 text-white p-6 w-full md:w-1/3'):
                ui.label('Dodaj użytkownika').classes('text-xl font-bold mb-4 text-green-400')
                new_user = ui.input('Nazwa użytkownika').classes('w-full').props('dark')
                new_user_pass = ui.input('Hasło', password=True).classes('w-full').props('dark')
                new_user_role = ui.select(['admin', 'operator'], value='operator').classes('w-full').props('dark')

                async def add_user():
                    with SessionLocal() as db:
                        # Sprawdź czy już istnieje
                        if db.query(User).filter(User.username == new_user.value).first():
                            ui.notify('Użytkownik już istnieje!', color='negative')
                            return
                        
                        db.add(User(username=new_user.value, password=new_user_pass.value, role=new_user_role.value))
                        db.commit()
                        ui.notify(f'Dodano użytkownika {new_user.value}', color='positive')
                        # Odśwież tabelę
                        user_table.update_rows()
                        new_user.value = new_user_pass.value = ''

                ui.button('DODAJ UŻYTKOWNIKA', on_click=add_user).classes('w-full mt-4 bg-green-600')

    # --- SEKCJA 3: TABELA UŻYTKOWNIKÓW (TYLKO DLA ADMINA) ---
    if app.storage.user.get('role') == 'admin':
        ui.label('Lista użytkowników').classes('text-xl font-bold mt-8 mb-4 text-white')
        
        columns = [
            {'name': 'username', 'label': 'Użytkownik', 'field': 'username', 'required': True, 'align': 'left'},
            {'name': 'role', 'label': 'Rola', 'field': 'role', 'align': 'left'},
            {'name': 'action', 'label': 'Akcje', 'field': 'action'},
        ]

        def get_users():
            with SessionLocal() as db:
                return [{'username': u.username, 'role': u.role} for u in db.query(User).all()]

        user_table = ui.table(columns=columns, rows=get_users(), row_key='username').classes('w-full bg-zinc-900 text-white').props('dark')
        
        # Dodanie przycisku usuwania do tabeli
        user_table.add_slot('body-cell-action', '''
            <q-td :props="props">
                <q-btn flat icon="delete" color="red" @click="$parent.$emit('delete', props.row)" />
            </q-td>
        ''')

        async def delete_user(msg):
            target_user = msg['username']
            if target_user == app.storage.user['username']:
                ui.notify('Nie możesz usunąć samego siebie!', color='negative')
                return
            with SessionLocal() as db:
                db.query(User).filter(User.username == target_user).delete()
                db.commit()
                ui.notify(f'Usunięto {target_user}')
                user_table.rows = get_users()

        user_table.on('delete', lambda msg: delete_user(msg.args))

# --- LOGIKA BACKENDU: GOOGLE DRIVE & RETENCJA ---
def upload_to_gdrive(file_path, folder_id):
    from googleapiclient.discovery import build
    from google.oauth2 import service_account
    from googleapiclient.http import MediaFileUpload
    try:
        if not os.path.exists('credentials.json'): return False
        creds = service_account.Credentials.from_service_account_file('credentials.json', scopes=['https://www.googleapis.com/auth/drive.file'])
        service = build('drive', 'v3', credentials=creds)
        file_metadata = {'name': file_path.name, 'parents': [folder_id] if folder_id else []}
        media = MediaFileUpload(str(file_path), mimetype='video/mp4', resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return True
    except Exception as e:
        logging.error(f"Gdrive Error: {e}")
        return False

async def run_retention_task():
    db = SessionLocal()
    config = db.query(SystemConfig).first()
    now = time.time()
    retention_secs = 30 * 24 * 3600

    for vid in RECORDINGS_DIR.rglob("*.mp4"):
        if now - vid.stat().st_mtime > retention_secs:
            if config and config.retention_policy == "BACKUP" and config.gdrive_folder_id:
                if upload_to_gdrive(vid, config.gdrive_folder_id): vid.unlink()
            else:
                vid.unlink()
    db.close()

# --- INTERFEJS UŻYTKOWNIKA (NiceGUI) ---

def is_authenticated():
    return app.storage.user.get('authenticated', False)

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
            # Używamy 'with', aby mieć pewność, że sesja DB zawsze się zamknie
            with SessionLocal() as db:
                user = db.query(User).filter(
                    User.username == u.value, 
                    User.password == p.value
                ).first()
                
                if user:
                    # Zapisujemy dane do sesji (storage_secret to podpisuje)
                    app.storage.user.update({
                        'authenticated': True, 
                        'username': user.username, 
                        'role': user.role
                    })
                    ui.notify(f'Witaj {user.username}!', color='positive')
                    # Krótkie opóźnienie, by sesja zdążyła się zapisać w przeglądarce
                    await ui.run_javascript('setTimeout(() => { window.location.href = "/" }, 500)')
                else:
                    ui.notify('Błędne dane – sąsiadka Cię nie wpuści!', color='negative')
        
        # Obsługa Entera na obu polach
        u.on('keydown.enter', do_login)
        p.on('keydown.enter', do_login)
        
        ui.button('ZALOGUJ', on_click=do_login).classes('w-full mt-6 bg-blue-600 hover:bg-blue-700 text-white font-bold')

@ui.page('/')
async def dashboard():
    user_role = app.storage.user.get('role')
    db = SessionLocal()
    cfg = db.query(SystemConfig).first()
    db.close()

    ui.query('body').style('background: #000; color: #fff;')

    # NAGŁÓWEK BRANDINGOWY
    with ui.header().classes('bg-zinc-950 border-b border-zinc-800 p-4 items-center justify-between'):
        with ui.row().classes('items-center gap-4'):
            ui.image('/static/logo.png').classes('w-12 h-12 border border-zinc-700 p-1 bg-zinc-900 rounded')
            with ui.column().classes('gap-0'):
                ui.label(cfg.institution_name if cfg else "OSP Istebns-Centrum").classes('text-[10px] tracking-widest text-zinc-500 font-bold uppercase')
                ui.label(cfg.unit_name if cfg else "Drone Team").classes('text-xl font-black text-white uppercase')
        
        with ui.row().classes('items-center gap-6'):
            sys = get_sys_resources()
            ui.label(f"CPU: {sys['cpu']}%").classes('text-xs font-mono text-zinc-500')
            ui.label(f"DISK: {sys['disk_free']}GB").classes('text-xs font-mono text-zinc-500')
            ui.button(icon='logout', on_click=lambda: (app.storage.user.clear(), ui.navigate.to('/login'))).props('flat color=white')

    with ui.tabs().classes('w-full bg-zinc-900 text-zinc-400') as tabs:
    t_grid = ui.tab('GRID OPERACYJNY', icon='grid_view')
    t_archive = ui.tab('ARCHIWUM', icon='history')
    if user_role == 'admin':
        t_admin = ui.tab('ZARZĄDZANIE', icon='settings')

# Panele zakładek (tu dzieje się magia)
with ui.tab_panels(tabs, value=t_grid).classes('w-full bg-black text-zinc-300'):
    
    # --- PANEL 1: GRID ---
    with ui.tab_panel(t_grid):
        ui.label('Widok operacyjny drona').classes('text-xl font-bold')
        # Tu później wstawimy Twój Live Grid

    # --- PANEL 2: ARCHIWUM ---
    with ui.tab_panel(t_archive):
        ui.label('NAGRANIA MP4 (30 DNI)').classes('text-zinc-500 text-xs mb-4 tracking-widest')
        # Tutaj logika skanowania folderu i ui.video (jak omawialiśmy wcześniej)
        ui.label('Moduł archiwum gotowy do przeglądania plików.').classes('italic text-zinc-600')

   if user_role == 'admin':
        with ui.tab_panel(t_admin):
            with ui.column().classes('w-full max-w-6xl mx-auto p-4 gap-8'):
                
                # --- NAGŁÓWEK SEKCJI ---
                with ui.row().classes('w-full items-center justify-between border-b border-zinc-800 pb-4'):
                    ui.label('ADMINISTRACJA SYSTEMEM').classes('text-2xl font-black text-white tracking-tighter')
                    ui.badge('UPRAWNIENIA: ADMINISTRATOR', color='blue-9').classes('px-4 py-1')

                with ui.row().classes('w-full items-stretch gap-6'):
                    
                    # --- KARTA 1: TWOJE KONTO (Zmiana własnego hasła) ---
                    with ui.card().classes('bg-zinc-900 border border-zinc-800 p-6 flex-1 text-white shadow-xl'):
                        with ui.row().classes('items-center gap-2 mb-4'):
                            ui.icon('manage_accounts', size='sm').classes('text-blue-500')
                            ui.label('TWOJE KONTO').classes('text-lg font-bold')
                        
                        ui.label(f'Zalogowany jako: {app.storage.user.get("username")}').classes('text-xs text-zinc-500 mb-4 uppercase')
                        
                        new_my_pass = ui.input('Nowe hasło administratora', password=True).classes('w-full').props('dark filled dense')
                        
                        async def update_own_password():
                            if not new_my_pass.value:
                                ui.notify('Wpisz nowe hasło!', color='warning')
                                return
                            with SessionLocal() as db:
                                u = db.query(User).filter(User.username == app.storage.user['username']).first()
                                if u:
                                    u.password = new_my_pass.value
                                    db.commit()
                                    ui.notify('Twoje hasło zostało zmienione', color='positive', icon='check')
                                    new_my_pass.value = ''

                        ui.button('ZAPISZ NOWE HASŁO', on_click=update_own_password).classes('w-full mt-4 bg-blue-700 hover:bg-blue-600 font-bold')

                    # --- KARTA 2: DODAWANIE OPERATORA ---
                    with ui.card().classes('bg-zinc-900 border border-zinc-800 p-6 flex-1 text-white shadow-xl'):
                        with ui.row().classes('items-center gap-2 mb-4'):
                            ui.icon('person_add', size='sm').classes('text-green-500')
                            ui.label('DODAJ UŻYTKOWNIKA').classes('text-lg font-bold')
                        
                        add_n = ui.input('Nazwa użytkownika (Login)').classes('w-full').props('dark filled dense')
                        add_p = ui.input('Hasło początkowe', password=True).classes('w-full').props('dark filled dense')
                        add_r = ui.select(['admin', 'operator'], value='operator', label='Rola systemowa').classes('w-full').props('dark filled dense text-white')

                        async def handle_add_user():
                            if not add_n.value or not add_p.value:
                                ui.notify('Uzupełnij wszystkie pola!', color='negative')
                                return
                            with SessionLocal() as db:
                                if db.query(User).filter(User.username == add_n.value).first():
                                    ui.notify('Taki użytkownik już istnieje!', color='negative')
                                    return
                                db.add(User(username=add_n.value, password=add_p.value, role=add_r.value))
                                db.commit()
                                ui.notify(f'Dodano użytkownika: {add_n.value}', color='positive', icon='person_add')
                                add_n.value = add_p.value = ''
                                # Odświeżenie tabeli
                                user_table.rows = get_user_data()

                        ui.button('UTWÓRZ KONTO', on_click=handle_add_user).classes('w-full mt-4 bg-zinc-800 hover:bg-zinc-700 text-green-500 font-bold border border-green-900/30')

                # --- SEKCJA: LISTA UŻYTKOWNIKÓW ---
                with ui.column().classes('w-full mt-4'):
                    ui.label('ZARZĄDZANIE OPERATORAMI').classes('text-sm font-bold text-zinc-500 tracking-widest mb-2')
                    
                    def get_user_data():
                        with SessionLocal() as db:
                            return [{'username': u.username, 'role': u.role} for u in db.query(User).all()]

                    columns = [
                        {'name': 'username', 'label': 'UŻYTKOWNIK', 'field': 'username', 'align': 'left', 'sortable': True},
                        {'name': 'role', 'label': 'ROLA', 'field': 'role', 'align': 'left'},
                        {'name': 'actions', 'label': 'OPERACJE', 'field': 'actions', 'align': 'right'},
                    ]

                    user_table = ui.table(columns=columns, rows=get_user_data(), row_key='username').classes('w-full bg-zinc-950 border border-zinc-900 shadow-2xl').props('dark flat border')
                    
                    # Dodanie przycisku usuwania w tabeli
                    user_table.add_slot('body-cell-actions', '''
                        <q-td :props="props">
                            <q-btn flat round icon="delete_forever" color="red-8" size="sm" @click="$parent.$emit('delete_req', props.row.username)">
                                <q-tooltip class="bg-red-9">USUŃ UŻYTKOWNIKA</q-tooltip>
                            </q-btn>
                        </q-td>
                    ''')

                    async def handle_delete_user(username_to_del):
                        if username_to_del == app.storage.user['username']:
                            ui.notify('Błąd: Nie możesz usunąć własnego konta administratora!', color='negative', icon='report')
                            return
                        
                        with SessionLocal() as db:
                            db.query(User).filter(User.username == username_to_del).delete()
                            db.commit()
                            ui.notify(f'Użytkownik {username_to_del} został usunięty', color='warning', icon='delete')
                            user_table.rows = get_user_data()

                    user_table.on('delete_req', lambda e: handle_delete_user(e.args))

def create_default_user():
    db = SessionLocal()
    # Sprawdzamy czy jest jakikolwiek admin
    admin = db.query(User).filter(User.username == "admin").first()
    if not admin:
        new_admin = User(
            username="admin", 
            password="123", # Zmień po pierwszym zalogowaniu!
            role="admin"
        )
        db.add(new_admin)
        db.commit()
        print("STWORZONO DOMYŚLNEGO ADMINA: admin / 123")
    db.close()

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