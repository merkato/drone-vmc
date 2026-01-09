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
    """Zarządza relacjami Many-to-Many między użytkownikami a strumieniami."""
    with SessionLocal() as db:
        stream = db.query(StreamPath).filter(StreamPath.id == stream_id).first()
        user = db.query(User).filter(User.id == user_id).first()
        
        if not stream or not user:
            return
        
        target_list = stream.authorized_viewers if rel_type == 'viewer' else stream.authorized_publishers
        
        if state:
            if user not in target_list: target_list.append(user)
        else:
            if user in target_list: target_list.remove(user)
            
        db.commit()
        ui.notify(f"Uprawnienia {user.username} zaktualizowane", type='positive')

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
        # Tutaj też używamy selectinload, żeby menu widziało relacje
        stream = db.query(StreamPath).options(
            selectinload(StreamPath.authorized_viewers),
            selectinload(StreamPath.authorized_publishers)
        ).filter(StreamPath.id == stream_id).first()
        
        all_users = db.query(User).all()
        current_viewers = [u.id for u in stream.authorized_viewers]
        current_publishers = [u.id for u in stream.authorized_publishers]

    ui.label('UPRAWNIENIA').classes('text-[10px] font-black text-orange-500 mb-2')
    with ui.grid(columns=2).classes('w-full gap-4'):
        with ui.column():
            ui.label('WIDZOWIE').classes('text-[8px] text-zinc-500')
            for user in all_users:
                ui.checkbox(user.username, value=(user.id in current_viewers),
                            on_change=lambda e, u_id=user.id: toggle_rel(stream_id, u_id, 'viewer', e.value)) \
                    .classes('text-xs text-zinc-200')
        with ui.column():
            ui.label('PILOCI').classes('text-[8px] text-zinc-500')
            for user in all_users:
                ui.checkbox(user.username, value=(user.id in current_publishers),
                            on_change=lambda e, u_id=user.id: toggle_rel(stream_id, u_id, 'publisher', e.value)) \
                    .classes('text-xs text-zinc-200')

@ui.refreshable
def streams_management_interface(username, role):
    if role != 'admin':
        ui.label('Brak dostępu').classes('text-red-500 p-8')
        return

    with SessionLocal() as db:
        # KLUCZOWE ROZWIĄZANIE: Ładujemy relacje Many-to-Many od razu (Eager Loading)
        streams = db.query(StreamPath).options(
            selectinload(StreamPath.authorized_viewers),
            selectinload(StreamPath.authorized_publishers)
        ).all()

    ui.label('Zarządzanie Strumieniami').classes('text-2xl font-black mb-6 text-white uppercase')

    # SEKCJA DODAWANIA
    with ui.card().classes('w-full bg-zinc-950 border-2 border-orange-900/20 p-6 mb-8 rounded-2xl'):
        with ui.row().classes('w-full items-end gap-4'):
            p_in = ui.input('path_name').classes('flex-grow').props('dark outlined color=orange')
            d_in = ui.input('description').classes('flex-grow').props('dark outlined color=orange')
            ui.button(icon='add', on_click=lambda: add_new_stream(p_in.value, d_in.value)).props('round size=lg color=orange')

    # LISTA
    with ui.grid(columns='1fr 1fr').classes('w-full gap-6'):
        for stream in streams:
            with ui.card().classes('bg-zinc-900 border-2 border-zinc-800 p-5 rounded-2xl'):
                with ui.row().classes('w-full justify-between items-start'):
                    with ui.column():
                        ui.label(stream.description or stream.path_name).classes('text-xl font-black text-white')
                        ui.label(f"PATH: {stream.path_name}").classes('text-[10px] text-zinc-500')
                    
                    with ui.row():
                        with ui.button(icon='manage_accounts', color='zinc-800').props('flat round'):
                            with ui.menu().classes('p-6 bg-zinc-900 border border-zinc-700 min-w-[350px]'):
                                user_selection_ui.refresh(stream.id)
                                user_selection_ui(stream.id)
                        ui.button(icon='delete', color='red-9', on_click=lambda s_id=stream.id: delete_stream(s_id)).props('flat round')

                ui.separator().classes('my-4 bg-zinc-800 opacity-40')

                # SEKCJA LINKÓW (Teraz authorized_publishers są już załadowane!)
                with ui.column().classes('w-full gap-3 mb-4 bg-zinc-950 p-4 rounded-xl border border-zinc-800'):
                    ui.label('LINKI RTMP (PILOCI)').classes('text-[8px] font-black text-orange-500 tracking-widest')
                    for pilot in stream.authorized_publishers:
                        rtmp_link = f"rtmp://{DOMAIN}:1935/{stream.path_name}?user={pilot.username}&password={pilot.password}"
                        with ui.row().classes('w-full items-center justify-between no-wrap'):
                            ui.label(pilot.username).classes('text-[10px] text-zinc-200 font-bold w-16')
                            ui.label(rtmp_link).classes('text-[9px] font-mono text-zinc-500 truncate flex-grow')
                            ui.button(icon='content_copy', on_click=lambda l=rtmp_link: ui.run_javascript(f'navigator.clipboard.writeText("{l}")')) \
                                .props('flat dense size=sm color=orange')

                    ui.separator().classes('my-2 bg-zinc-800 opacity-20')

                    ui.label('LINKI HLS (WIDZOWIE)').classes('text-[8px] font-black text-blue-400 tracking-widest')
                    for viewer in stream.authorized_viewers:
                        hls_link = f"http://{DOMAIN}:8888/{stream.path_name}/index.m3u8?user={viewer.username}&password={viewer.password}"
                        with ui.row().classes('w-full items-center justify-between no-wrap'):
                            ui.label(viewer.username).classes('text-[10px] text-zinc-200 font-bold w-16')
                            ui.label(hls_link).classes('text-[9px] font-mono text-zinc-500 truncate flex-grow')
                            ui.button(icon='link', on_click=lambda l=hls_link: ui.run_javascript(f'navigator.clipboard.writeText("{l}")')) \
                                .props('flat dense size=sm color=blue-400')

                ui.switch('NAGRYWANIE', value=stream.is_recording_enabled, 
                          on_change=lambda e, s_id=stream.id: update_recording_status(s_id, e.value)).props('color=orange')