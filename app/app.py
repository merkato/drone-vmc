import os
import time
import shutil
import psutil
import httpx
import logging
import requests
import json
from urllib.parse import parse_qs
from pathlib import Path
from collections import defaultdict
from datetime import datetime

from fastapi import Request, HTTPException, Response, responses
from fastapi.staticfiles import StaticFiles
from nicegui import app, ui
from sqlalchemy import create_engine, Column, String, Boolean, Table, ForeignKey, Integer, Text
from sqlalchemy.orm import Session, sessionmaker, relationship, declarative_base

# --- KONFIGURACJA ŚRODOWISKA ---
DOMAIN = os.getenv('DOMAIN', 'localhost')
STORAGE_SECRET = os.getenv('STORAGE_SECRET', 'super_secret_firanka')
MEDIAMTX_API = "http://mediamtx:9997/v3"
MEDIAMTX_WEBRTC = f"https://stream.{DOMAIN}"
RECORDINGS_DIR = Path("/recordings")

# Inicjalizacja folderów i plików statycznych
RECORDINGS_DIR.mkdir(exist_ok=True)
app.add_static_files('/download', str(RECORDINGS_DIR))

if not os.path.exists('/recordings'):
    os.makedirs('/recordings', exist_ok=True)
app.mount("/recordings", StaticFiles(directory="/recordings"), name="recordings")
app.add_static_files('/static', 'static')

Base = declarative_base()
# Wymuszamy ścieżkę absolutną wewnątrz kontenera
DB_PATH = "/app/vms.db"
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

# Dodatkowe parametry dla SQLite, aby lepiej radził sobie z blokowaniem plików
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False},
    pool_pre_ping=True
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# --- TABELE ASOCJACYJNE (Uprawnienia) ---
# Muszą być zdefiniowane przed klasami, które ich używają w 'secondary'

stream_permissions = Table(
    'stream_permissions', Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('stream_path', String, ForeignKey('streams.path_name', ondelete='CASCADE'), primary_key=True)
)

stream_viewers = Table(
    'stream_viewers', Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('stream_path', String, ForeignKey('streams.path_name', ondelete='CASCADE'), primary_key=True)
)

# --- KLASY MODELI ---

class User(Base):
    __tablename__ = 'users'
    # TU BYŁ BŁĄD: Upewnij się, że kolumna nazywa się dokładnie 'id'
    id = Column(Integer, primary_key=True) 
    username = Column(String, unique=True)
    password = Column(String)
    role = Column(String) # 'admin', 'operator', 'viewer'

    # Relacje (SQLAlchemy połączy je z tabelami powyżej)
    allowed_streams = relationship("StreamPath", secondary=stream_permissions, back_populates="authorized_publishers")
    visible_streams = relationship("StreamPath", secondary=stream_viewers, back_populates="authorized_viewers")

class StreamPath(Base):
    __tablename__ = "streams"
    path_name = Column(String, primary_key=True)
    description = Column(String)
    owner_username = Column(String)
    is_recording = Column(Boolean, default=False)
    
    authorized_publishers = relationship("User", secondary=stream_permissions, back_populates="allowed_streams")
    authorized_viewers = relationship("User", secondary=stream_viewers, back_populates="visible_streams")

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
from fastapi import Request, Response

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

def get_active_streams():
    """Pobiera listę nazw aktywnych strumieni bezpośrednio z API MediaMTX."""
    try:
        # Zakładamy, że mediamtx jest w tej samej sieci dockera (host: mediamtx)
        # Jeśli nie, użyj adresu IP serwera
        response = requests.get('http://mediamtx:9997/v3/paths/list', timeout=1)
        if response.status_code == 200:
            data = response.json()
            # Wyciągamy nazwy ścieżek, które mają flagę 'ready: True'
            return [item['name'] for item in data.get('items', []) if item.get('ready')]
    except Exception as e:
        print(f"Błąd API MediaMTX: {e}")
        return []
    return []

def sync_recording_state(path_name: str, should_record: bool):
    """Informuje MediaMTX czy ma nagrywać konkretną ścieżkę."""
    try:
        # API MediaMTX: PATCH /v3/config/paths/patch/{name}
        url = f"http://mediamtx:9997/v3/config/paths/patch/{path_name}"
        payload = {"record": should_record}
        response = requests.patch(url, json=payload, timeout=2)
        
        if response.status_code == 200:
            status_text = "WŁĄCZONE" if should_record else "WYŁĄCZONE"
            ui.notify(f"Nagrywanie dla {path_name}: {status_text}", color='info')
        else:
            ui.notify("Błąd synchronizacji z MediaMTX", color='negative')
    except Exception as e:
        print(f"Błąd API MediaMTX (Recording): {e}")

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

def get_my_grid_streams():
    u_id = app.storage.user.get('user_id')
    u_role = app.storage.user.get('role')
    
    with SessionLocal() as db:
        if u_role == 'admin':
            return db.query(StreamPath).all() # Admin widzi wszystko
        
        # Pobieramy tylko te streamy, gdzie użytkownik jest na liście authorized_viewers
        user = db.query(User).filter(User.id == u_id).first()
        return user.visible_streams # NiceGUI / SQLAlchemy automatycznie to przefiltruje

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

async def save_stream_backend(path_name, description, publisher_ids, viewer_ids):
    """Zapisuje strumień i relacje, dbając o poprawne typy danych."""
    current_admin = app.storage.user.get('username')
    
    # Konwersja ID na int (na wypadek gdyby NiceGUI przesłało str)
    pub_ids = [int(i) for i in publisher_ids] if publisher_ids else []
    view_ids = [int(i) for i in viewer_ids] if viewer_ids else []

    try:
        with SessionLocal() as db:
            # 1. Pobierz lub stwórz strumień
            stream = db.query(StreamPath).filter(StreamPath.path_name == path_name).first()
            if not stream:
                stream = StreamPath(path_name=path_name, description=description, owner_username=current_admin)
                db.add(stream)
                db.flush() # Pobierz obiekt do sesji
            else:
                stream.description = description

            # 2. Pobierz obiekty użytkowników na podstawie ID
            publishers = db.query(User).filter(User.id.in_(pub_ids)).all() if pub_ids else []
            viewers = db.query(User).filter(User.id.in_(view_ids)).all() if view_ids else []

            # 3. Zaktualizuj relacje many-to-many
            stream.authorized_publishers = publishers
            stream.authorized_viewers = viewers
            
            db.commit()

            # 4. Generowanie linków
            links = []
            for pub in publishers:
                l = f"rtmp://stream.giswgorach.pl/{path_name}?user={pub.username}&password={pub.password}"
                links.append({'user': pub.username, 'link': l})
            return links
    except Exception as e:
        print(f"BŁĄD ZAPISU: {e}")
        return None

def streams_management_interface(username, role):
    """
    Kompletny interfejs zarządzania strumieniami z obsługą nagrywania (REC).
    """
    
    # --- FUNKCJE POMOCNICZE (WEWNĘTRZNE) ---
    def get_current_users():
        with SessionLocal() as db:
            return {u.id: u.username for u in db.query(User).all()}

    def get_streams():
        with SessionLocal() as db:
            if role == 'admin':
                streams = db.query(StreamPath).all()
            else:
                user = db.query(User).filter(User.username == username).first()
                owned = db.query(StreamPath).filter(StreamPath.owner_username == username).all()
                published = user.allowed_streams if user else []
                streams = list({s.path_name: s for s in (owned + published)}.values())
            
            return [{
                'path': s.path_name, 
                'desc': s.description, 
                'owner': s.owner_username,
                'rec': s.is_recording_enabled # Stan nagrywania z bazy
            } for s in streams]

    with ui.column().classes('w-full max-w-6xl mx-auto p-4 gap-6 bg-black'):
        
        # --- PANEL 1: FORMULARZ KONFIGURACJI ---
        with ui.card().classes('bg-zinc-900 border border-zinc-800 p-8 text-white w-full shadow-2xl relative'):
            ui.button(icon='refresh', on_click=lambda: refresh_user_lists()) \
                .props('flat round size=sm color=zinc-500') \
                .classes('absolute right-4 top-4') \
                .tooltip('Odśwież listę osób')

            ui.label('KONFIGURACJA STRUMIENIA').classes('text-xl font-black text-orange-500 mb-6')

            with ui.row().classes('w-full gap-4 mb-4'):
                s_path = ui.input('ID Strumienia (np. istebna/mini1)', placeholder='np. dron-1').classes('flex-1').props('dark filled')
                s_desc = ui.input('Opis / Lokalizacja').classes('flex-1').props('dark filled')

            u_opts = get_current_users()
            with ui.row().classes('w-full gap-4 mb-4'):
                p_sel = ui.select(u_opts, multiple=True, label='PILOCI (NADAWANIE)').classes('flex-1').props('dark filled')
                v_sel = ui.select(u_opts, multiple=True, label='WIDZOWIE (PODGLĄD)').classes('flex-1').props('dark filled')

            # PANEL LINKÓW RTMP
            rtmp_box = ui.column().classes('w-full mt-6 p-4 bg-black rounded border border-zinc-800 shadow-inner')
            rtmp_box.set_visibility(False)

            def show_links_in_box(links):
                rtmp_box.clear()
                rtmp_box.set_visibility(True)
                with rtmp_box:
                    ui.label('AKTYWNE LINKI RTMP DLA PILOTÓW:').classes('text-orange-500 font-bold mb-2 text-[10px]')
                    for item in links:
                        with ui.row().classes('w-full bg-zinc-900 p-2 rounded mb-1 items-center border border-zinc-800'):
                            ui.label(item['user']).classes('text-xs font-bold w-24 text-zinc-300')
                            ui.label(item['link']).classes('text-[10px] text-zinc-500 truncate flex-1 px-4 font-mono')
                            ui.button(icon='content_copy', on_click=lambda l=item['link']: ui.run_javascript(f'navigator.clipboard.writeText("{l}")')) \
                                .props('flat round size=sm color=orange-4')
                    ui.button('ZAMKNIJ PANEL', on_click=lambda: rtmp_box.set_visibility(False)).props('flat dense color=zinc-600').classes('self-end mt-2 text-[10px]')

            async def handle_save():
                if not s_path.value:
                    ui.notify('BŁĄD: Podaj ID strumienia!', color='negative')
                    return
                # Tu wywołujesz swoją funkcję backendową zapisu
                links = await save_stream_backend(s_path.value, s_desc.value, p_sel.value, v_sel.value)
                if links:
                    show_links_in_box(links)
                    ui.notify(f'Zapisano: {s_path.value}', color='positive')
                    stream_table.rows = get_streams()
                else:
                    ui.notify('Wystąpił błąd zapisu!', color='negative')

            def refresh_user_lists():
                new_opts = get_current_users()
                p_sel.options = new_opts
                v_sel.options = new_opts
                ui.notify('Zaktualizowano listę osób', color='info')

            ui.button('ZAPISZ I GENERUJ LINKI RTMP', on_click=handle_save) \
                .classes('w-full mt-6 bg-orange-700 hover:bg-orange-600 font-bold py-4 text-lg shadow-lg')

        # --- PANEL 2: TABELA STRUMIENI ---
        ui.label('TWOJE STRUMIENIE OPERACYJNE').classes('text-sm font-bold text-zinc-500 mt-6 tracking-widest uppercase')
        
        columns = [
            {'name': 'path', 'label': 'ID (PATH)', 'field': 'path', 'align': 'left', 'sortable': True},
            {'name': 'desc', 'label': 'OPIS', 'field': 'desc', 'align': 'left'},
            {'name': 'rec', 'label': 'REC', 'field': 'rec', 'align': 'center'}, # Kolumna na suwak
            {'name': 'owner', 'label': 'WŁAŚCICIEL', 'field': 'owner', 'align': 'left'},
            {'name': 'actions', 'label': 'AKCJE', 'field': 'actions', 'align': 'right'},
        ]

        stream_table = ui.table(columns=columns, rows=get_streams(), row_key='path') \
            .classes('w-full bg-zinc-950 border border-zinc-900 shadow-2xl').props('dark flat border')

        # SLOT DLA SUWAKA REC
        stream_table.add_slot('body-cell-rec', '''
            <q-td :props="props">
                <q-toggle 
                    v-model="props.row.rec" 
                    color="red" 
                    keep-color
                    @update:model-value="val => $parent.$emit('toggle_rec', {path: props.row.path, state: val})"
                />
            </q-td>
        ''')

        # SLOT DLA PRZYCISKÓW AKCJI
        stream_table.add_slot('body-cell-actions', '''
            <q-td :props="props">
                <q-btn flat round icon="vpn_key" color="orange" size="sm" @click="$parent.$emit('show_rtmp', props.row.path)">
                    <q-tooltip class="bg-orange-9">Pokaż poświadczenia RTMP</q-tooltip>
                </q-btn>
                <q-btn flat round icon="delete" color="red-8" size="sm" @click="$parent.$emit('delete_st', props.row.path)">
                    <q-tooltip class="bg-red-9">Usuń strumień</q-tooltip>
                </q-btn>
            </q-td>
        ''')

        # --- HANDLERY ZDARZEŃ TABELI ---
        
        async def handle_toggle_rec(args):
            p_name = args['path']
            new_state = args['state']
            
            # 1. Zapis do bazy
            with SessionLocal() as db:
                s = db.query(StreamPath).filter(StreamPath.path_name == p_name).first()
                if s:
                    s.is_recording_enabled = new_state
                    db.commit()
            
            # 2. Synchronizacja z MediaMTX
            success = await sync_recording_state(p_name, new_state)
            if success:
                msg = "Nagrywanie AKTYWNE" if new_state else "Nagrywanie ZATRZYMANE"
                ui.notify(f"{p_name}: {msg}", color='red-9' if new_state else 'grey-8')
            else:
                ui.notify("Błąd MediaMTX!", color='negative')
                stream_table.rows = get_streams() # Odśwież, by cofnąć suwak

        async def fetch_and_show_links(p_name):
            with SessionLocal() as db:
                s = db.query(StreamPath).filter(StreamPath.path_name == p_name).first()
                if s:
                    links = []
                    for pub in s.authorized_publishers:
                        l = f"rtmp://stream.giswgorach.pl/{s.path_name}?user={pub.username}&password={pub.password}"
                        links.append({'user': pub.username, 'link': l})
                    if not links:
                        ui.notify('Brak przypisanych pilotów!', color='warning')
                        return
                    show_links_in_box(links)
                    ui.run_javascript('window.scrollTo({top: 0, behavior: "smooth"})')

        async def delete_stream_logic(p_name):
            with SessionLocal() as db:
                s = db.query(StreamPath).filter(StreamPath.path_name == p_name).first()
                if not s: return
                if role == 'admin' or s.owner_username == username:
                    db.delete(s)
                    db.commit()
                    ui.notify(f'Strumień {p_name} usunięty', color='positive', icon='delete')
                    stream_table.rows = get_streams()
                    rtmp_box.set_visibility(False)
                else:
                    ui.notify('Brak uprawnień!', color='negative')

        # Rejestracja zdarzeń wysyłanych z Vue (slotów)
        stream_table.on('toggle_rec', lambda e: handle_toggle_rec(e.args))
        stream_table.on('show_rtmp', lambda e: fetch_and_show_links(e.args))
        stream_table.on('delete_st', lambda e: delete_stream_logic(e.args))

def archive_interface(username, role):
    """
    Interfejs przeglądania nagrań wideo.
    - Skanuje folder /recordings
    - Pozwala każdemu oglądać
    - Pozwala tylko adminowi usuwać
    """

    def get_recordings_list():
        """Skanuje system plików i zwraca listę nagrań."""
        base_path = '/recordings'
        recordings = []
        
        if not os.path.exists(base_path):
            return []

        # Przeszukujemy foldery: /recordings/ścieżka/plik.mp4
        for root, dirs, files in os.walk(base_path):
            for file in files:
                if file.endswith(('.mp4', '.m4v')):
                    full_path = os.path.join(root, file)
                    relative_path = os.path.relpath(full_path, base_path)
                    
                    stats = os.stat(full_path)
                    size_mb = round(stats.st_size / (1024 * 1024), 2)
                    ctime = datetime.fromtimestamp(stats.st_ctime).strftime('%Y-%m-%d %H:%M:%S')
                    
                    # Wyciągamy nazwę drona z nazwy folderu
                    drone_name = relative_path.split(os.sep)[0]
                    
                    recordings.append({
                        'id': relative_path,
                        'drone': drone_name,
                        'filename': file,
                        'date': ctime,
                        'size': f"{size_mb} MB",
                        'path': f"/recordings/{relative_path}" # URL do odtwarzacza
                    })
        
        # Sortujemy od najnowszych
        return sorted(recordings, key=lambda x: x['date'], reverse=True)

    with ui.column().classes('w-full max-w-6xl mx-auto p-4 gap-4'):
        ui.label('ARCHIWUM NAGRAŃ OPERACYJNYCH').classes('text-xl font-black text-orange-500 mb-4')

        # --- ODTWARZACZ WIDEO (Ukryty domyślnie) ---
        video_container = ui.card().classes('w-full bg-black p-0 overflow-hidden border border-orange-900 shadow-2xl')
        video_container.set_visibility(False)
        
        def close_player():
            video_container.clear()
            video_container.set_visibility(False)

        def play_video(path, title):
            video_container.clear()
            video_container.set_visibility(True)
            with video_container:
                with ui.row().classes('w-full p-2 bg-zinc-900 justify-between items-center'):
                    ui.label(f'ODTWARZANIE: {title}').classes('text-xs font-bold text-orange-500')
                    ui.button(icon='close', on_click=close_player).props('flat round color=white')
                ui.video(path).classes('w-full').props('controls autoplay')
            ui.run_javascript('window.scrollTo({top: 0, behavior: "smooth"})')

        # --- TABELA NAGRAŃ ---
        columns = [
            {'name': 'date', 'label': 'DATA I GODZINA', 'field': 'date', 'align': 'left', 'sortable': True},
            {'name': 'drone', 'label': 'DRON (PATH)', 'field': 'drone', 'align': 'left', 'sortable': True},
            {'name': 'size', 'label': 'ROZMIAR', 'field': 'size', 'align': 'center'},
            {'name': 'actions', 'label': 'AKCJE', 'field': 'actions', 'align': 'right'},
        ]

        table = ui.table(columns=columns, rows=get_recordings_list(), row_key='id') \
            .classes('w-full bg-zinc-950 border border-zinc-900').props('dark flat')

        # Slot dla przycisków
        del_btn_html = '<q-btn flat round icon="delete" color="red" @click="$parent.$emit(\'delete\', props.row)"><q-tooltip>Usuń trwale</q-tooltip></q-btn>'
        actions_slot = f'''
    <q-td :props="props">
        <q-btn flat round icon="play_circle" color="orange" @click="$parent.$emit('play', props.row)">
            <q-tooltip>Odtwórz nagranie</q-tooltip>
        </q-btn>
        <q-btn flat round icon="download" color="blue" @click="$parent.$emit('download', props.row.path)">
            <q-tooltip>Pobierz na dysk</q-tooltip>
        </q-btn>
        {del_btn_html if role == 'admin' else ''}
    </q-td>
'''
        # 3. Dodaj slot do tabeli
        table.add_slot('body-cell-actions', actions_slot)
        # Dodaj to wewnątrz archive_interface, nad tabelą:
        with ui.row().classes('w-full justify-between items-center bg-zinc-900 p-4 rounded-lg border border-zinc-800'):
            with ui.column():
                ui.label('REPOZYTORIUM NAGRAŃ').classes('text-orange-500 font-bold')
                ui.label('Pliki zapisane bezpośrednio na serwerze VPS').classes('text-[10px] text-zinc-500')
    
            ui.button('ODŚWIEŻ LISTĘ', icon='sync', on_click=lambda: update_table()) \
                .props('outline color=orange')

        def update_table():
            table.rows = get_recordings_list()
            ui.notify('Zaktualizowano listę plików z dysku', color='info')

        # --- HANDLERY ---
        table.on('play', lambda e: play_video(e.args['path'], e.args['filename']))
        
        table.on('download', lambda e: ui.download(e.args))

        async def delete_file(row):
            result = await ui.dialog() \
                .with_fields([ui.label(f"Czy na pewno chcesz usunąć nagranie {row['filename']}?")]) \
                .props('persistent')
            
            # W NiceGUI 1.4+ lepiej użyć prostego potwierdzenia:
            with ui.dialog() as dialog, ui.card():
                ui.label(f"Usunąć nagranie {row['filename']}?")
                with ui.row():
                    ui.button('TAK', on_click=lambda: dialog.submit(True)).props('color=red')
                    ui.button('NIE', on_click=lambda: dialog.submit(False))
            
            if await dialog:
                try:
                    full_path = os.path.join('/recordings', row['id'])
                    os.remove(full_path)
                    ui.notify('Plik usunięty', color='positive')
                    table.rows = get_recordings_list()
                    if video_container.is_visible: close_player()
                except Exception as ex:
                    ui.notify(f'Błąd usuwania: {ex}', color='negative')

        table.on('delete', lambda e: delete_file(e.args))

        # Przycisk odświeżania listy
        ui.button('ODŚWIEŻ LISTĘ NAGRAŃ', on_click=lambda: setattr(table, 'rows', get_recordings_list())) \
            .classes('w-full mt-4 bg-zinc-800 text-zinc-400')
        
def user_management_interface():
    def get_users():
        with SessionLocal() as db:
            users = db.query(User).all()
            return [{'id': u.id, 'username': u.username, 'role': u.role, 'password': u.password} for u in users]

    # --- FUNKCJA EDYCJI ---
    async def edit_user(user_data):
        with ui.dialog() as dialog, ui.card().classes('w-96 bg-zinc-900 border border-zinc-800'):
            ui.label(f'EDYCJA UŻYTKOWNIKA: {user_data["username"]}').classes('text-orange-500 font-bold mb-4')
            
            # Wypełniamy pola aktualnymi danymi
            new_pass = ui.input('Hasło', value=user_data['password']).props('dark filled password-toggle').classes('w-full mb-2')
            new_role = ui.select(
                ['admin', 'pilot', 'viewer'], 
                label='Rola systemowa', 
                value=user_data['role']
            ).props('dark filled').classes('w-full')
            
            with ui.row().classes('w-full justify-end mt-4'):
                ui.button('ANULUJ', on_click=dialog.close).props('flat color=white')
                ui.button('ZAPISZ ZMIANY', on_click=lambda: dialog.submit({
                    'password': new_pass.value,
                    'role': new_role.value
                })).props('color=orange')

        result = await dialog
        if result:
            with SessionLocal() as db:
                db_user = db.query(User).filter(User.id == user_data['id']).first()
                if db_user:
                    db_user.password = result['password']
                    db_user.role = result['role']
                    db.commit()
                    ui.notify(f'Zaktualizowano profil: {db_user.username}', color='positive')
                    user_table.rows = get_users()

    # --- FUNKCJA USUWANIA ---
    async def delete_user(user_data):
        with ui.dialog() as dialog, ui.card().classes('bg-zinc-900 border border-red-900 p-6'):
            ui.label(f'CZY USUNĄĆ KONTO: {user_data["username"]}?').classes('text-white mb-4')
            with ui.row().classes('w-full justify-center gap-4'):
                ui.button('TAK, USUŃ', on_click=lambda: dialog.submit(True)).props('color=red')
                ui.button('ANULUJ', on_click=lambda: dialog.submit(False)).props('flat color=white')
        
        if await dialog:
            with SessionLocal() as db:
                db_user = db.query(User).filter(User.id == user_data['id']).first()
                if db_user:
                    db.delete(db_user)
                    db.commit()
                    ui.notify(f'Użytkownik {user_data["username"]} został usunięty', color='warning')
                    user_table.rows = get_users()

    with ui.column().classes('w-full max-w-4xl mx-auto p-4 gap-4'):
        ui.label('ZARZĄDZANIE PERSONELEM').classes('text-xl font-black text-orange-500')

        columns = [
            {'name': 'username', 'label': 'LOGIN', 'field': 'username', 'align': 'left', 'sortable': True},
            {'name': 'role', 'label': 'ROLA', 'field': 'role', 'align': 'left', 'sortable': True},
            {'name': 'actions', 'label': 'OPERACJE', 'field': 'actions', 'align': 'right'},
        ]

        user_table = ui.table(columns=columns, rows=get_users(), row_key='id') \
            .classes('w-full bg-zinc-950 border border-zinc-900 shadow-2xl').props('dark flat')

        # --- FIX DLA SYNTAX ERROR ---
        # Zamiast f-stringa z backslashem, używamy czystego HTML dla slotu
        user_table.add_slot('body-cell-actions', '''
            <q-td :props="props">
                <q-btn flat round icon="edit" color="orange" size="sm" @click="$parent.$emit('edit_user', props.row)">
                    <q-tooltip>Edytuj hasło i rolę</q-tooltip>
                </q-btn>
                <q-btn flat round icon="person_remove" color="red-8" size="sm" @click="$parent.$emit('delete_user', props.row)">
                    <q-tooltip>Usuń użytkownika z systemu</q-tooltip>
                </q-btn>
            </q-td>
        ''')

        # Mapowanie zdarzeń
        user_table.on('edit_user', lambda e: edit_user(e.args))
        user_table.on('delete_user', lambda e: delete_user(e.args))

# --- LOGIKA GRIDU OPERACYJNEGO ---

@ui.refreshable
def live_grid_content():
    """Wewnętrzna część gridu, która się odświeża co 5 sekund."""
    u_id = app.storage.user.get('user_id')
    u_role = app.storage.user.get('role')

    # Pobieramy statusy LIVE z MediaMTX
    active_paths = get_active_streams()

    with SessionLocal() as db:
        current_u = db.query(User).filter(User.id == u_id).first()
        if not current_u:
            ui.label('Błąd sesji użytkownika. Zaloguj się ponownie.').classes('text-red-500')
            return

        if u_role == 'admin':
            my_streams = db.query(StreamPath).all()
        else:
            my_streams = current_u.visible_streams if current_u else []

    if not my_streams:
        with ui.column().classes('w-full items-center py-20 border-2 border-dashed border-zinc-900 rounded-xl'):
            ui.icon('videocam_off', size='lg', color='grey-9')
            ui.label('Brak przypisanych strumieni').classes('text-zinc-600 italic')
        return

    # GRID: 1 kolumna na mobile, 4 na desktopie
    with ui.grid(columns='1 md:2 lg:4').classes('w-full gap-4'):
        for s in my_streams:
            is_live = s.path_name in active_paths
            
            # Automatyczne logowanie do iFrame
            stream_url = f"https://stream.giswgorach.pl/{s.path_name}/?user={current_u.username}&password={current_u.password}"

            with ui.card().classes('bg-zinc-900 border border-zinc-800 p-0 overflow-hidden shadow-2xl relative'):
                # Pasek tytułowy
                with ui.row().classes('w-full p-2 items-center justify-between bg-zinc-950 border-b border-zinc-800'):
                    with ui.row().classes('items-center gap-2'):
                        if is_live:
                            ui.icon('fiber_manual_record', color='red').classes('animate-pulse')
                            ui.label('LIVE').classes('text-[10px] font-black text-red-500')
                        else:
                            ui.icon('fiber_manual_record', color='zinc-700')
                            ui.label('OFFLINE').classes('text-[10px] font-bold text-zinc-600')
                        
                        ui.label(s.path_name.upper()).classes('text-[10px] font-bold text-zinc-300 font-mono truncate')
                    
                    ui.button(icon='open_in_new', on_click=lambda url=stream_url: ui.navigate.to(url, new_tab=True)) \
                        .props('flat round size=sm color=blue-500')

                # Okno Wideo
                ui.html(f'''
        <div style="position:relative; padding-top:56.25%; background:#000;">
            <iframe src="{stream_url}" 
                    style="position:absolute; top:0; left:0; width:100%; height:100%; border:none;"
                    allowfullscreen>
            </iframe>
        </div>
    ''', sanitize=False).classes('w-full')

                # Stopka
                with ui.row().classes('w-full p-2 bg-zinc-900/50'):
                    ui.label(s.description or "Brak opisu").classes('text-[9px] text-zinc-500 truncate')

def live_grid_interface():
    """Tę funkcję wywołuje main_page w zakładce GRID OPERACYJNY."""
    with ui.column().classes('w-full p-4 bg-black'):
        # Inicjalne wywołanie zawartości
        live_grid_content()
        
        # Automatyczne odświeżanie statusów LIVE i listy strumieni co 5 sekund
        ui.timer(30.0, live_grid_content.refresh)

@ui.page('/')
def main_page():
    # 1. Pobieramy dane z sesji użytkownika
    username = app.storage.user.get('username')
    role = app.storage.user.get('role')

    # 2. Zabezpieczenie: jeśli nie ma danych, wracamy do logowania
    if not username or not role:
        ui.navigate.to('/login')
        return
    
    ui.query('body').style('background-color: #000000;')
    ui.query('.q-page').style('background-color: #000000;')
    # Pobieramy dane sesji
    user_role = app.storage.user.get('role', 'operator')
    username = app.storage.user.get('username', 'Niezalogowany')

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
        
        with ui.tab_panel(t_grid):
            live_grid_interface()

        if user_role in ['admin', 'operator']:
            with ui.tab_panel(t_archive):
                archive_interface(username, role)
            with ui.tab_panel(t_streams):
                streams_management_interface(username, user_role)

        if user_role == 'admin':
            with ui.tab_panel(t_admin):
                user_management_interface()


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