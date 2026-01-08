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
app.config.max_http_buffer_size = 20_000_000

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
    institution_name = Column(String, default="INSTYTUT MONITORINGU")
    unit_name = Column(String, default="JEDNOSTKA OPERACYJNA")
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
    if not is_authenticated() and request.url.path not in ['/login', '/auth']:
        return responses.RedirectResponse('/login')
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
                ui.label(cfg.institution_name if cfg else "INSTYTUT").classes('text-[10px] tracking-widest text-zinc-500 font-bold uppercase')
                ui.label(cfg.unit_name if cfg else "JEDNOSTKA").classes('text-xl font-black text-white uppercase')
        
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

    with ui.tab_panels(tabs, value=t_grid).classes('w-full bg-transparent p-4'):
        
        # --- PANEL: GRID ---
        with ui.tab_panel(t_grid):
            grid_el = ui.element('div').classes('w-full')
            
            @ui.refreshable
            async def refresh_grid():
                grid_el.clear()
                try:
                    async with httpx.AsyncClient() as client:
                        r = await client.get(f"{MEDIAMTX_API}/paths/list")
                        streams = r.json().get('items', {})
                except: streams = {}

                with grid_el:
                    with ui.grid(columns='repeat(auto-fill, minmax(500px, 1fr))').classes('w-full gap-4'):
                        for name, data in streams.items():
                            if data.get('ready'):
                                with ui.card().classes('bg-zinc-900 border-none p-0 overflow-hidden'):
                                    # WebRTC iframe z auth w URL
                                    u, p = app.storage.user.get('username'), app.storage.user.get('password')
                                    src = f"{MEDIAMTX_WEBRTC}/{name}?user={u}&password={p}"
                                    ui.html(f'<iframe src="{src}" style="width:100%; aspect-ratio:16/9; border:none;"></iframe>')
                                    with ui.row().classes('p-3 justify-between items-center w-full'):
                                        ui.label(name).classes('font-bold text-blue-400 uppercase text-xs')
                                        ui.badge('LIVE', color='red').classes('animate-pulse')

            ui.timer(5.0, refresh_grid)
            await refresh_grid()

        # --- PANEL: ARCHIWUM ---
        with ui.tab_panel(t_archive):
            ui.label('NAGRANIA MP4 (30 DNI)').classes('text-zinc-500 text-xs mb-4 tracking-widest')
            # Tutaj logika skanowania folderu i ui.video (jak omawialiśmy wcześniej)
            ui.label('Moduł archiwum gotowy do przeglądania plików.').classes('italic text-zinc-600')

        # --- PANEL: ADMIN ---
        if user_role == 'admin':
            with ui.tab_panel(t_admin):
                with ui.column().classes('w-full gap-8'):
                    ui.label('PANEL ADMINISTRATORSKI').classes('text-xl font-black text-blue-500')
                    # Tutaj CRUD użytkowników i Many-to-Many
                    ui.label('Konfiguracja retencji i uprawnień dostępna w bazie danych.').classes('text-zinc-400')


# START SYSTEMU
if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        host='0.0.0.0', 
        port=8080, 
        title='Wiejski Drone VMS do podglądania sąsiadek',
        storage_secret='PesaToNajgorszyProducentTaboruNaSwiecie',
        favicon='static/logo.png',
        reload=False  # Wyłączamy reload wewnątrz Dockera dla większej stabilności
    )