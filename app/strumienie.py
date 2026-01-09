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

# --- 1. KOMPONENT UPRAWNIEŃ (PILOCI I WIDZOWIE - RELACJE M2M) ---
@ui.refreshable
def user_selection_ui(stream_id: int):
    """Generuje listę strażaków z podziałem na role (Widz/Pilot)."""
    with SessionLocal() as db:
        stream = db.query(StreamPath).filter(StreamPath.id == stream_id).first()
        if not stream:
            return
        all_users = db.query(User).all()
        
        # Pobieramy ID użytkowników przypisanych do relacji
        current_viewers = [u.id for u in stream.authorized_viewers]
        current_publishers = [u.id for u in stream.authorized_publishers]

    ui.label('ZARZĄDZANIE DOSTĘPEM').classes('text-[10px] font-black text-orange-500 mb-2 tracking-widest')
    
    with ui.grid(columns=2).classes('w-full gap-4'):
        # KOLUMNA WIDZÓW
        with ui.column():
            ui.label('WIDZOWIE').classes('text-[9px] font-bold text-zinc-500 uppercase')
            for user in all_users:
                ui.checkbox(user.username, value=(user.id in current_viewers),
                            on_change=lambda e, u_id=user.id: toggle_rel(stream_id, u_id, 'viewer', e.value)) \
                    .classes('text-xs font-bold')

        # KOLUMNA PILOTÓW (PUBLISHERS)
        with ui.column():
            ui.label('PILOCI (NADADAWCY)').classes('text-[9px] font-bold text-zinc-500 uppercase')
            for user in all_users:
                ui.checkbox(user.username, value=(user.id in current_publishers),
                            on_change=lambda e, u_id=user.id: toggle_rel(stream_id, u_id, 'publisher', e.value)) \
                    .classes('text-xs font-bold')

def toggle_rel(stream_id: int, user_id: int, rel_type: str, state: bool):
    """Zapisuje zmiany w relacjach Many-to-Many."""
    with SessionLocal() as db:
        stream = db.query(StreamPath).filter(StreamPath.id == stream_id).first()
        user = db.query(User).filter(User.id == user_id).first()
        
        target_list = stream.authorized_viewers if rel_type == 'viewer' else stream.authorized_publishers
        
        if state:
            if user not in target_list: target_list.append(user)
        else:
            if user in target_list: target_list.remove(user)
            
        db.commit()
        ui.notify(f"Uprawnienia {user.username} zaktualizowane", type='positive', position='bottom-right')

# --- 2. LOGIKA NAGRYWANIA (SUWACZEK) ---
def update_recording_status(stream_id: int, state: bool):
    """Aktualizuje status nagrywania w bazie danych."""
    with SessionLocal() as db:
        stream = db.query(StreamPath).filter(StreamPath.id == stream_id).first()
        if stream:
            stream.is_recording_enabled = state
            db.commit()
            status = "WŁĄCZONE" if state else "WYŁĄCZONE"
            ui.notify(f"Nagrywanie dla {stream.path_name}: {status}", 
                      type='warning' if state else 'info', position='bottom-right')

# --- 3. LOGIKA DODAWANIA I USUWANIA ---
def add_new_stream(path_name_val, description_val):
    """Tworzy nowy rekord drona w bazie."""
    if not path_name_val:
        ui.notify('Ścieżka (path_name) jest wymagana!', type='negative')
        return

    with SessionLocal() as db:
        # Sprawdzamy czy ścieżka już istnieje (unikalność)
        exists = db.query(StreamPath).filter(StreamPath.path_name == path_name_val).first()
        if exists:
            ui.notify('Taki strumień już istnieje w systemie!', type='warning')
            return

        new_stream = StreamPath(
            path_name=path_name_val,
            description=description_val or path_name_val,
            is_recording_enabled=False
        )
        db.add(new_stream)
        db.commit()
        
    ui.notify(f'Dodano drona: {path_name_val}', type='positive')
    streams_management_interface.refresh()

def delete_stream(stream_id: int):
    """Usuwa drona z systemu."""
    with SessionLocal() as db:
        db.query(StreamPath).filter(StreamPath.id == stream_id).delete()
        db.commit()
    ui.notify('Strumień został usunięty', type='info')
    streams_management_interface.refresh()

# --- 4. GŁÓWNY INTERFEJS (STRONA) ---
@ui.refreshable
def streams_management_interface(username, role):
    if role != 'admin':
        ui.label('Brak uprawnień administratora.').classes('text-red-500 p-8 text-xl')
        return

    with SessionLocal() as db:
        streams = db.query(StreamPath).all()

    ui.label('Zarządzanie Flotą Dronów i Kamer').classes('text-2xl font-black mb-6 text-white uppercase tracking-tighter')

    # SEKCJA DODAWANIA
    with ui.card().classes('w-full bg-zinc-950 border-2 border-orange-900/20 p-6 mb-8 rounded-2xl shadow-2xl'):
        ui.label('REJESTRACJA NOWEGO STRUMIENIA').classes('text-[10px] font-black text-orange-500 mb-4 tracking-widest')
        with ui.row().classes('w-full items-end gap-4'):
            p_in = ui.input('Ścieżka (np. istebna/matrice)').classes('flex-grow').props('dark outlined color=orange')
            d_in = ui.input('Opis/Model (np. DJI Mavic 3 Enterprise)').classes('flex-grow').props('dark outlined color=orange')
            ui.button(icon='add', on_click=lambda: add_new_stream(p_in.value, d_in.value)) \
                .props('round size=lg color=orange').classes('shadow-lg shadow-orange-900/20')

    # SIATKA STRUMIENI
    ui.label('ZAREJESTROWANE URZĄDZENIA').classes('text-[10px] font-black text-zinc-500 mb-4 tracking-widest px-2')
    
    if not streams:
        with ui.column().classes('w-full items-center p-12 border-2 border-dashed border-zinc-800 rounded-2xl'):
            ui.icon('videocam_off', size='64px').classes('text-zinc-800')
            ui.label('Baza strumieni jest pusta. Dodaj drona powyżej.').classes('text-zinc-600 font-bold')

    with ui.grid(columns='1fr 1fr').classes('w-full gap-6'):
        for stream in streams:
            with ui.card().classes('bg-zinc-900 border-2 border-zinc-800 p-5 rounded-2xl hover:border-zinc-700 transition-all'):
                # Nagłówek Karty
                with ui.row().classes('w-full justify-between items-start'):
                    with ui.column().classes('gap-0'):
                        ui.label(stream.description or stream.path_name).classes('text-xl font-black text-white uppercase')
                        ui.label(f"PATH_NAME: {stream.path_name}").classes('text-[10px] font-mono text-zinc-500 mt-1')
                    
                    with ui.row().classes('gap-2'):
                        # PRZYCISK ZARZĄDZANIA DOSTĘPEM
                        with ui.button(icon='manage_accounts', color='zinc-800').props('flat round'):
                            with ui.menu().classes('p-6 bg-zinc-900 border border-zinc-700 shadow-2xl min-w-[350px] rounded-xl'):
                                user_selection_ui.refresh(stream.id)
                                user_selection_ui(stream.id)
                        
                        # PRZYCISK USUWANIA
                        ui.button(icon='delete_forever', color='red-9').props('flat round') \
                            .on('click', lambda s_id=stream.id: delete_stream(s_id))

                ui.separator().classes('my-4 bg-zinc-800 opacity-40')

                # SEKCJA NAGRYWANIA (SUWACZEK)
                with ui.row().classes('w-full justify-between items-center bg-zinc-950/50 p-3 rounded-xl border border-zinc-800/50'):
                    with ui.column().classes('gap-0'):
                        ui.label('AUTONAGRYWANIE HLS').classes('text-[10px] font-black text-zinc-500 tracking-widest')
                        ui.label('Stały zapis na serwerze').classes('text-[9px] text-zinc-700')
                    
                    # TO JEST TWÓJ SUWACZEK
                    ui.switch(value=stream.is_recording_enabled, 
                              on_change=lambda e, s_id=stream.id: update_recording_status(s_id, e.value)) \
                        .props('color=orange')