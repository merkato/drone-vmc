import requests
import logging
import httpx
from nicegui import ui
from models import SessionLocal, User, StreamPath
from config import MEDIAMTX_API

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- 1. KOMPONENT UPRAWNIEŃ (POZOSTAJE BEZ ZMIAN) ---
@ui.refreshable
def user_selection_ui(current_allowed: str, stream_id: int):
    with SessionLocal() as db:
        all_users = [str(u.username) for u in db.query(User).all()]
    
    allowed_list = current_allowed.split(',') if current_allowed else []
    ui.label('UPRAWNIENIA DOSTĘPU').classes('text-[10px] font-black text-zinc-500 mb-2 uppercase tracking-widest')
    
    with ui.column().classes('gap-0 w-full'):
        for user in all_users:
            ui.checkbox(user, value=(user in allowed_list), 
                        on_change=lambda e, u=user: toggle_permission(stream_id, u, e.value)).classes('text-sm')

def toggle_permission(stream_id: int, username: str, state: bool):
    with SessionLocal() as db:
        stream = db.query(StreamPath).filter(StreamPath.id == stream_id).first()
        if not stream: return
        current = set(stream.allowed_users.split(',')) if stream.allowed_users else set()
        if state: current.add(username)
        else: current.discard(username)
        stream.allowed_users = ",".join(filter(None, current))
        db.commit()
        ui.notify(f"Zaktualizowano dostęp dla {username}", type='info')

# --- 2. LOGIKA DODAWANIA NOWEGO STRUMIENIA ---
def add_new_stream(path_input, label_input):
    path = path_input.value.strip()
    label = label_input.value.strip()
    
    if not path:
        ui.notify('Ścieżka strumienia nie może być pusta!', type='negative')
        return

    with SessionLocal() as db:
        # Sprawdzamy czy już istnieje
        existing = db.query(StreamPath).filter(StreamPath.path == path).first()
        if existing:
            ui.notify('Ten strumień jest już dodany!', type='warning')
            return

        new_stream = StreamPath(path=path, label=label, record=False, allowed_users="admin")
        db.add(new_stream)
        db.commit()
        
    ui.notify(f'Dodano strumień: {path}', type='positive')
    path_input.value = ''
    label_input.value = ''
    streams_management_interface.refresh() # Odświeżamy całą listę

# --- 3. GŁÓWNY INTERFEJS (ODŚWIEŻALNY) ---
@ui.refreshable
def streams_management_interface(username, role):
    if role != 'admin':
        ui.label('Brak uprawnień.').classes('text-red-500')
        return

    with SessionLocal() as db:
        streams = db.query(StreamPath).all()

    ui.label('Konfiguracja Strumieni Wideo').classes('text-2xl font-black mb-6 text-white')

    # --- SEKCJA DODAWANIA (NOWA) ---
    with ui.card().classes('w-full bg-zinc-900 border-2 border-orange-900/30 p-6 mb-8 rounded-xl'):
        ui.label('DODAJ NOWY STRUMIEŃ (DRONA)').classes('text-xs font-black text-orange-500 mb-4 tracking-widest')
        with ui.row().classes('w-full items-center gap-4'):
            path_in = ui.input('Ścieżka (np. Istebna/Dron1)').classes('flex-grow').props('dark outlined color=orange')
            label_in = ui.input('Nazwa wyświetlana (np. DJI Mini 3)').classes('flex-grow').props('dark outlined color=orange')
            ui.button(icon='add', on_click=lambda: add_new_stream(path_in, label_in)).props('round size=lg color=orange')

    # --- LISTA STRUMIENI ---
    if not streams:
        with ui.column().classes('w-full items-center p-12 border-2 border-dashed border-zinc-800 rounded-xl'):
            ui.icon('videocam_off', size='48px').classes('text-zinc-700')
            ui.label('Brak skonfigurowanych strumieni. Dodaj pierwszego drona powyżej.').classes('text-zinc-500')
    else:
        with ui.grid(columns='1fr 1fr').classes('w-full gap-4'):
            for stream in streams:
                with ui.card().classes('bg-zinc-900 border-2 border-zinc-800 p-4 rounded-xl'):
                    with ui.row().classes('w-full justify-between items-start'):
                        with ui.column().classes('gap-0'):
                            ui.label(stream.label or stream.path).classes('text-lg font-black text-orange-500')
                            ui.label(f"ID: {stream.path}").classes('text-[10px] font-mono text-zinc-600')
                        
                        # Przycisk uprawnień
                        with ui.button(icon='person_add', color='zinc-700').props('flat round'):
                            with ui.menu().classes('p-4 bg-zinc-900 border border-zinc-700'):
                                user_selection_ui.refresh(stream.allowed_users, stream.id)
                                user_selection_ui(stream.allowed_users, stream.id)

                    ui.separator().classes('my-4 bg-zinc-800')
                    
                    with ui.row().classes('w-full justify-between items-center'):
                        ui.label('AUTONAGRYWANIE').classes('text-[10px] font-bold text-zinc-500')
                        ui.switch(value=stream.record).props('color=orange')