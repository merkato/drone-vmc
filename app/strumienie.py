import requests
import logging
import httpx
from nicegui import ui
from sqlalchemy.orm import selectinload
from models import SessionLocal, User, StreamPath
from config import MEDIAMTX_API, DOMAIN

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- 1. LOGIKA RELACJI (WIDZOWIE / PILOCI) ---
def toggle_rel(stream_id: int, user_id: int, rel_type: str, state: bool):
    with SessionLocal() as db:
        stream = db.query(StreamPath).filter(StreamPath.id == stream_id).first()
        user = db.query(User).filter(User.id == user_id).first()
        if not stream or not user: return
        target_list = stream.authorized_viewers if rel_type == 'viewer' else stream.authorized_publishers
        if state:
            if user not in target_list: target_list.append(user)
        else:
            if user in target_list: target_list.remove(user)
        db.commit()
        ui.notify(f"Zaktualizowano: {user.username}")

# --- 2. LOGIKA ZARZĄDZANIA STRUMIENIAMI (DODAJ / USUŃ / NAGRYWAJ) ---
def add_new_stream(path_name, description):
    """Dodaje nowy strumień do bazy danych."""
    if not path_name:
        ui.notify('Ścieżka (path_name) jest wymagana!', type='negative')
        return
    with SessionLocal() as db:
        new_s = StreamPath(
            path_name=path_name, 
            description=description, 
            is_recording_enabled=False
        )
        db.add(new_s)
        db.commit()
    ui.notify(f'Dodano drona: {path_name}', type='positive')
    streams_management_interface.refresh()

def delete_stream(stream_id: int):
    with SessionLocal() as db:
        db.query(StreamPath).filter(StreamPath.id == stream_id).delete()
        db.commit()
    ui.notify('Usunięto strumień')
    streams_management_interface.refresh()

def update_recording_status(stream_id, state):
    with SessionLocal() as db:
        stream = db.query(StreamPath).filter(StreamPath.id == stream_id).first()
        if stream:
            stream.is_recording_enabled = state
            db.commit()
            ui.notify("Status nagrywania zmieniony")

@ui.refreshable
def user_selection_ui(stream_id: int):
    with SessionLocal() as db:
        stream = db.query(StreamPath).options(
            selectinload(StreamPath.authorized_viewers),
            selectinload(StreamPath.authorized_publishers)
        ).filter(StreamPath.id == stream_id).first()
        all_users = db.query(User).all()
        v_ids = [u.id for u in stream.authorized_viewers]
        p_ids = [u.id for u in stream.authorized_publishers]

    ui.label('ZARZĄDZANIE DOSTĘPEM').classes('text-xs font-black text-orange-500 mb-4 tracking-widest')
    
    with ui.grid(columns=2).classes('w-full gap-8'):
        # WIDZOWIE
        with ui.column():
            ui.label('WIDZOWIE (HLS)').classes('text-xs font-bold text-zinc-500 uppercase')
            for user in all_users:
                # Zwiększona czcionka: text-base (16px) i font-bold
                ui.checkbox(user.username, value=(user.id in v_ids),
                            on_change=lambda e, u_id=user.id: toggle_rel(stream_id, u_id, 'viewer', e.value)) \
                    .classes('text-base font-bold text-zinc-100 py-1')

        # PILOCI
        with ui.column():
            ui.label('PILOCI (RTMP)').classes('text-xs font-bold text-zinc-500 uppercase')
            for user in all_users:
                ui.checkbox(user.username, value=(user.id in p_ids),
                            on_change=lambda e, u_id=user.id: toggle_rel(stream_id, u_id, 'publisher', e.value)) \
                    .classes('text-base font-bold text-zinc-100 py-1')

# --- 3. GŁÓWNY INTERFEJS (POWIĘKSZONE LINKI) ---
@ui.refreshable
def streams_management_interface(username, role):
    if role != 'admin':
        ui.label('Brak uprawnień.').classes('text-red-500 p-8 text-xl')
        return

    with SessionLocal() as db:
        streams = db.query(StreamPath).options(
            selectinload(StreamPath.authorized_viewers),
            selectinload(StreamPath.authorized_publishers)
        ).all()

    ui.label('Zarządzanie Strumieniami').classes('text-3xl font-black mb-8 text-white uppercase tracking-tighter')

    with ui.grid(columns='1fr 1fr').classes('w-full gap-8'):
        for stream in streams:
            with ui.card().classes('bg-zinc-900 border-2 border-zinc-800 p-6 rounded-3xl shadow-2xl'):
                
                # Nagłówek Karty
                with ui.row().classes('w-full justify-between items-center mb-4'):
                    with ui.column().classes('gap-0'):
                        ui.label(stream.description or stream.path_name).classes('text-2xl font-black text-white uppercase')
                        ui.label(f"ID: {stream.path_name}").classes('text-xs font-mono text-zinc-500')
                    
                    with ui.row().classes('gap-3'):
                        with ui.button(icon='manage_accounts', color='orange-9').props('elevated round size=lg'):
                            with ui.menu().classes('p-8 bg-zinc-900 border-2 border-zinc-700 min-w-[450px] rounded-2xl shadow-2xl'):
                                user_selection_ui.refresh(stream.id)
                                user_selection_ui(stream.id)
                        
                        # Przycisk usuwania (wspólny dla modułu)
                        ui.button(icon='delete', color='red-9', on_click=lambda s_id=stream.id: delete_stream(s_id)).props('flat round size=md')

                ui.separator().classes('mb-6 bg-zinc-800 opacity-50')

                # --- SEKCJA LINKÓW (CZYTELNA, DUŻA CZCIONKA) ---
                with ui.column().classes('w-full gap-4 mb-6 bg-zinc-950 p-6 rounded-2xl border border-zinc-800'):
                    
                    # LINKI RTMP DLA PILOTÓW
                    ui.label('LINKI DLA PILOTÓW (RTMP)').classes('text-xs font-black text-orange-500 tracking-widest uppercase')
                    for pilot in stream.authorized_publishers:
                        # DODANO: stream.DOMAIN
                        rtmp_link = f"rtmp://stream.{DOMAIN}:1935/{stream.path_name}?user={pilot.username}&password={pilot.password}"
                        with ui.row().classes('w-full items-center justify-between no-wrap bg-zinc-900/50 p-3 rounded-lg'):
                            ui.label(pilot.username).classes('text-sm font-black text-zinc-200 w-24 truncate')
                            # Powiększony link: text-sm font-mono
                            ui.label(rtmp_link).classes('text-[12px] font-mono text-orange-300/80 truncate flex-grow px-2')
                            ui.button(icon='content_copy', on_click=lambda l=rtmp_link: ui.run_javascript(f'navigator.clipboard.writeText("{l}")')) \
                                .props('flat color=orange').classes('ml-2')

                    ui.separator().classes('my-2 bg-zinc-800 opacity-30')

                    # LINKI HLS DLA WIDZÓW
                    ui.label('LINKI PODGLĄDU (HLS)').classes('text-xs font-black text-blue-400 tracking-widest uppercase')
                    for viewer in stream.authorized_viewers:
                        # DODANO: stream.DOMAIN
                        hls_link = f"http://stream.{DOMAIN}:8888/{stream.path_name}/index.m3u8?user={viewer.username}&password={viewer.password}"
                        with ui.row().classes('w-full items-center justify-between no-wrap bg-zinc-900/50 p-3 rounded-lg'):
                            ui.label(viewer.username).classes('text-sm font-black text-zinc-200 w-24 truncate')
                            ui.label(hls_link).classes('text-[12px] font-mono text-blue-300/80 truncate flex-grow px-2')
                            ui.button(icon='link', on_click=lambda l=hls_link: ui.run_javascript(f'navigator.clipboard.writeText("{l}")')) \
                                .props('flat color=blue-400').classes('ml-2')

                # Suwaczek nagrywania
                with ui.row().classes('w-full justify-between items-center px-4 py-2 bg-zinc-800/30 rounded-xl'):
                    ui.label('NAGRYWANIE ARCHIWALNE').classes('text-xs font-black text-zinc-400 tracking-widest')
                    ui.switch(value=stream.is_recording_enabled, 
                              on_change=lambda e, s_id=stream.id: update_recording_status(s_id, e.value)).props('color=orange size=lg')