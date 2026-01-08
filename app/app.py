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
DB_URL = "sqlite:///./vms_db.db"
Base = declarative_base()
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

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
@app.post('/auth')
async def media_mtx_auth(request: Request):
    data = await request.json()
    user_val = data.get('user')
    pass_val = data.get('password')
    action = data.get('action')  # 'publish' lub 'read'

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == user_val, User.password == pass_val).first()
        
        if user:
            # Jeśli ktoś chce nadawać (publish), musi być adminem lub operatorem
            if action == 'publish' and user.role not in ['admin', 'operator']:
                print(f"Odmowa nadawania dla: {user_val} (Rola: {user.role})")
                return responses.Response(status_code=401)
            
            print(f"Autoryzacja pomyślna: {user_val} dla akcji {action}")
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
    Kompletny interfejs zarządzania strumieniami:
    - Dodawanie/Edycja dronów
    - Zarządzanie uprawnieniami (Piloci/Widzowie)
    - Generowanie poświadczeń RTMP
    - Tabela z weryfikacją uprawnień do usuwania
    """
    
    # --- FUNKCJE POMOCNICZE (WEWNĘTRZNE) ---
    def get_current_users():
        """Pobiera aktualną listę użytkowników do menu wyboru."""
        with SessionLocal() as db:
            return {u.id: u.username for u in db.query(User).all()}

    def get_streams():
        """Pobiera strumienie, które dany użytkownik może widzieć/zarządzać."""
        with SessionLocal() as db:
            if role == 'admin':
                streams = db.query(StreamPath).all()
            else:
                user = db.query(User).filter(User.username == username).first()
                owned = db.query(StreamPath).filter(StreamPath.owner_username == username).all()
                published = user.allowed_streams if user else []
                # Połączenie list i usunięcie duplikatów
                streams = list({s.path_name: s for s in (owned + published)}.values())
            
            return [{'path': s.path_name, 'desc': s.description, 'owner': s.owner_username} for s in streams]

    with ui.column().classes('w-full max-w-6xl mx-auto p-4 gap-6 bg-black'):
        
        # --- PANEL 1: FORMULARZ KONFIGURACJI ---
        with ui.card().classes('bg-zinc-900 border border-zinc-800 p-8 text-white w-full shadow-2xl relative'):
            # Przycisk odświeżania użytkowników
            ui.button(icon='refresh', on_click=lambda: refresh_user_lists()) \
                .props('flat round size=sm color=zinc-500') \
                .classes('absolute right-4 top-4') \
                .tooltip('Odśwież listę osób')

            ui.label('KONFIGURACJA STRUMIENIA').classes('text-xl font-black text-orange-500 mb-6')

            with ui.row().classes('w-full gap-4 mb-4'):
                s_path = ui.input('ID Strumienia (np. istebna/mini1)', placeholder='np. dron-1').classes('flex-1').props('dark filled')
                s_desc = ui.input('Opis / Lokalizacja').classes('flex-1').props('dark filled')

            # Wybór osób
            u_opts = get_current_users()
            with ui.row().classes('w-full gap-4 mb-4'):
                p_sel = ui.select(u_opts, multiple=True, label='PILOCI (NADAWANIE)').classes('flex-1').props('dark filled')
                v_sel = ui.select(u_opts, multiple=True, label='WIDZOWIE (PODGLĄD)').classes('flex-1').props('dark filled')

            # PANEL LINKÓW RTMP (Ukryty domyślnie)
            rtmp_box = ui.column().classes('w-full mt-6 p-4 bg-black rounded border border-zinc-800 shadow-inner')
            rtmp_box.set_visibility(False)

            # --- LOGIKA WYŚWIETLANIA LINKÓW ---
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
            {'name': 'owner', 'label': 'WŁAŚCICIEL', 'field': 'owner', 'align': 'left'},
            {'name': 'actions', 'label': 'AKCJE', 'field': 'actions', 'align': 'right'},
        ]

        stream_table = ui.table(columns=columns, rows=get_streams(), row_key='path') \
            .classes('w-full bg-zinc-950 border border-zinc-900 shadow-2xl').props('dark flat border')

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

        # --- HANDLERY TABELI ---
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
                
                u = db.query(User).filter(User.username == username).first()
                
                # Uprawnienia: Admin, Właściciel lub Pilot
                if role == 'admin' or s.owner_username == username or u in s.authorized_publishers:
                    db.delete(s)
                    db.commit()
                    ui.notify(f'Strumień {p_name} usunięty', color='positive', icon='delete')
                    stream_table.rows = get_streams()
                    rtmp_box.set_visibility(False)
                else:
                    ui.notify('Brak uprawnień do usunięcia!', color='negative')

        stream_table.on('show_rtmp', lambda e: fetch_and_show_links(e.args))
        stream_table.on('delete_st', lambda e: delete_stream_logic(e.args))

def users_management_interface():
    current_user = app.storage.user.get('username')
    
    with ui.column().classes('w-full max-w-6xl mx-auto p-4 gap-6'):
        ui.label('ZARZĄDZANIE UŻYTKOWNIKAMI').classes('text-2xl font-black text-white')

        with ui.row().classes('w-full items-stretch gap-6'):
            # KARTA: Twoje Hasło
            with ui.card().classes('bg-zinc-900 border border-zinc-800 p-6 flex-1 text-white'):
                ui.label('ZMIANA TWOJEGO HASŁA').classes('text-sm font-bold text-blue-400 mb-4')
                new_pw = ui.input('Nowe hasło', password=True).classes('w-full').props('dark filled dense')
                async def update_my_pw():
                    with SessionLocal() as db:
                        u = db.query(User).filter(User.username == current_user).first()
                        u.password = new_pw.value
                        db.commit()
                        ui.notify('Hasło zmienione!')
                        new_pw.value = ''
                ui.button('ZAPISZ', on_click=update_my_pw).classes('w-full mt-4 bg-blue-700 font-bold')

            # KARTA: Dodaj Nowego
            with ui.card().classes('bg-zinc-900 border border-zinc-800 p-6 flex-1 text-white'):
                ui.label('NOWY UŻYTKOWNIK').classes('text-sm font-bold text-green-500 mb-4')
                u_n = ui.input('Login').classes('w-full').props('dark filled dense')
                u_p = ui.input('Hasło', password=True).classes('w-full').props('dark filled dense')
                u_r = ui.select(['admin', 'operator', 'viewer'], value='viewer').classes('w-full').props('dark filled dense text-white')
                async def create_u():
                    with SessionLocal() as db:
                        db.add(User(username=u_n.value, password=u_p.value, role=u_r.value))
                        db.commit()
                        ui.notify(f'Utworzono: {u_n.value}')
                        u_n.value = u_p.value = ''
                        user_table.rows = get_users()
                ui.button('UTWÓRZ', on_click=create_u).classes('w-full mt-4 bg-zinc-800 text-green-500 font-bold')

        # Tabela użytkowników
        def get_users():
            with SessionLocal() as db:
                return [{'username': u.username, 'role': u.role} for u in db.query(User).all()]

        user_table = ui.table(
            columns=[
                {'name': 'username', 'label': 'LOGIN', 'field': 'username', 'align': 'left'},
                {'name': 'role', 'label': 'ROLA', 'field': 'role', 'align': 'left'},
                {'name': 'actions', 'label': 'AKCJE', 'field': 'actions', 'align': 'right'}
            ],
            rows=get_users(), row_key='username'
        ).classes('w-full bg-zinc-950 border border-zinc-900').props('dark flat border')

        user_table.add_slot('body-cell-actions', '''
            <q-td :props="props">
                <q-btn flat round icon="delete" color="red" size="sm" @click="$parent.$emit('del', props.row.username)" />
            </q-td>
        ''')
        user_table.on('del', lambda e: delete_user_logic(e.args, user_table, get_users))

def live_grid_interface():
    # Pobieramy dane z sesji (app.storage.user)
    u_id = app.storage.user.get('user_id')
    u_role = app.storage.user.get('role')
    
    with ui.column().classes('w-full p-4 bg-black'):
        ui.label('PODGLĄD OPERACYJNY').classes('text-2xl font-black text-white mb-6')

        with SessionLocal() as db:
            if u_role == 'admin':
                my_streams = db.query(StreamPath).all()
            else:
                # Pobieramy obiekt użytkownika, aby skorzystać z relacji visible_streams
                user = db.query(User).filter(User.id == u_id).first()
                my_streams = user.visible_streams if user else []

        if not my_streams:
            ui.label('Brak aktywnych strumieni dla Twojego konta.').classes('text-zinc-500 italic')
            return

        # Grid: 1 kolumna na mobile, 2 na tablecie, 3 na PC
        with ui.grid(columns='1 md:2 lg:3').classes('w-full gap-4'):
            for s in my_streams:
                with ui.card().classes('bg-zinc-900 border border-zinc-800 p-0 overflow-hidden shadow-2xl'):
                    # Pasek tytułowy kafelka
                    with ui.row().classes('w-full p-2 items-center justify-between bg-zinc-950'):
                        ui.label(s.path_name.upper()).classes('text-[10px] font-bold text-zinc-400 font-mono')
                        ui.button(icon='fullscreen', on_click=lambda p=s.path_name: ui.navigate.to(f'/stream/{p}')) \
                            .props('flat round size=sm color=blue-400')

                    # Iframe z obrazem (Z POPRAWKĄ sanitize=False)
                    ui.html(f'''
                        <iframe src="https://stream.giswgorach.pl/{s.path_name}" 
                        style="width:500%; height:500%; border:none;" 
                        allowfullscreen>
                        </iframe>
                        ''', sanitize=False).classes('w-full h-64')

                    # Opis na dole
                    with ui.row().classes('w-full p-2'):
                        ui.label(s.description or "Brak opisu").classes('text-[10px] text-zinc-600 truncate')

@ui.page('/')
def main_page():
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
                ui.label('Drone VMS').classes('text-l font-black text-white leading-none')
                ui.label('Ochotnicza Straż Pożarna Istebna-Centrum').classes('text-[12px] text-blue-500 font-bold tracking-widest uppercase')

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
                ui.label('ARCHIWUM').classes('text-2xl font-black')

            with ui.tab_panel(t_streams):
                streams_management_interface(username, user_role)

        if user_role == 'admin':
            with ui.tab_panel(t_admin):
                users_management_interface()


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