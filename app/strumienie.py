import requests
import logging
import httpx
from nicegui import ui
from models import SessionLocal, User, StreamPath
from config import MEDIAMTX_API, DOMAIN

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- 1. KOMPONENT UPRAWNIEŃ (NAPRAWIONA WIDOCZNOŚĆ) ---
@ui.refreshable
def user_selection_ui(stream_id: int):
    with SessionLocal() as db:
        stream = db.query(StreamPath).filter(StreamPath.id == stream_id).first()
        if not stream: return
        all_users = db.query(User).all()
        current_viewers = [u.id for u in stream.authorized_viewers]
        current_publishers = [u.id for u in stream.authorized_publishers]

    ui.label('ZARZĄDZANIE DOSTĘPEM').classes('text-[10px] font-black text-orange-500 mb-2 tracking-widest')
    
    with ui.grid(columns=2).classes('w-full gap-4'):
        # KOLUMNA WIDZÓW
        with ui.column():
            ui.label('WIDZOWIE').classes('text-[9px] font-bold text-zinc-500 uppercase')
            for user in all_users:
                # DODANO: .classes('text-zinc-200'), żeby tekst był białawy
                ui.checkbox(user.username, value=(user.id in current_viewers),
                            on_change=lambda e, u_id=user.id: toggle_rel(stream_id, u_id, 'viewer', e.value)) \
                    .classes('text-sm font-medium text-zinc-200')

        # KOLUMNA PILOTÓW
        with ui.column():
            ui.label('PILOCI (NADAWCY)').classes('text-[9px] font-bold text-zinc-500 uppercase')
            for user in all_users:
                ui.checkbox(user.username, value=(user.id in current_publishers),
                            on_change=lambda e, u_id=user.id: toggle_rel(stream_id, u_id, 'publisher', e.value)) \
                    .classes('text-sm font-medium text-zinc-200')

# ... (funkcje toggle_rel i add_new_stream pozostają bez zmian) ...

# --- 2. GŁÓWNY INTERFEJS ---
@ui.refreshable
def streams_management_interface(username, role):
    if role != 'admin':
        ui.label('Brak uprawnień.').classes('text-red-500 p-8 text-xl')
        return

    with SessionLocal() as db:
        streams = db.query(StreamPath).all()

    ui.label('Zarządzanie Flotą Dronów').classes('text-2xl font-black mb-6 text-white uppercase')

    # SEKCJA DODAWANIA
    with ui.card().classes('w-full bg-zinc-950 border-2 border-orange-900/20 p-6 mb-8 rounded-2xl'):
        ui.label('REJESTRACJA NOWEGO STRUMIENIA').classes('text-[10px] font-black text-orange-500 mb-4 tracking-widest')
        with ui.row().classes('w-full items-end gap-4'):
            p_in = ui.input('Ścieżka (np. istebna/matrice)').classes('flex-grow').props('dark outlined color=orange')
            d_in = ui.input('Opis (np. DJI Mavic 3)').classes('flex-grow').props('dark outlined color=orange')
            ui.button(icon='add', on_click=lambda: add_new_stream(p_in.value, d_in.value)).props('round size=lg color=orange')

    # SIATKA STRUMIENI
    with ui.grid(columns='1fr 1fr').classes('w-full gap-6'):
        for stream in streams:
            with ui.card().classes('bg-zinc-900 border-2 border-zinc-800 p-5 rounded-2xl'):
                
                # Nagłówek
                with ui.row().classes('w-full justify-between items-start'):
                    with ui.column().classes('gap-0'):
                        ui.label(stream.description or stream.path_name).classes('text-xl font-black text-white uppercase')
                        ui.label(f"PATH: {stream.path_name}").classes('text-[10px] font-mono text-zinc-500 mt-1')
                    
                    with ui.row().classes('gap-2'):
                        with ui.button(icon='manage_accounts', color='zinc-800').props('flat round'):
                            with ui.menu().classes('p-6 bg-zinc-900 border border-zinc-700 shadow-2xl min-w-[350px]'):
                                user_selection_ui.refresh(stream.id)
                                user_selection_ui(stream.id)
                        
                        ui.button(icon='delete', color='red-9', on_click=lambda s_id=stream.id: delete_stream(s_id)).props('flat round')

                ui.separator().classes('my-4 bg-zinc-800 opacity-40')

                # --- NOWOŚĆ: LINK RTMP DLA PILOTA ---
                # Generujemy link: rtmp://DOMENA:1935/ścieżka
                rtmp_url = f"rtmp://{DOMAIN}:1935/{stream.path_name}"
                
                with ui.column().classes('w-full gap-1 mb-4 bg-zinc-950 p-3 rounded-xl border border-zinc-800'):
                    ui.label('LINK RTMP DLA DRONA (NADAWANIE)').classes('text-[9px] font-black text-orange-500 tracking-widest')
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label(rtmp_url).classes('text-[11px] font-mono text-zinc-400 truncate flex-grow')
                        ui.button(icon='content_copy', on_click=lambda url=rtmp_url: ui.run_javascript(f'navigator.clipboard.writeText("{url}")')) \
                            .props('flat dense size=sm color=zinc-500').tooltip('Kopiuj link')

                # Suwaczek nagrywania
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('AUTONAGRYWANIE HLS').classes('text-[10px] font-black text-zinc-500 tracking-widest')
                    ui.switch(value=stream.is_recording_enabled, 
                              on_change=lambda e, s_id=stream.id: update_recording_status(s_id, e.value)).props('color=orange')