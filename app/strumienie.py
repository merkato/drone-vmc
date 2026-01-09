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
# --- LOGIKA ZEWNĘTRZNA (MediaMTX) ---
async def sync_recording_state(path_name: str, should_record: bool):
    """Informuje MediaMTX czy ma nagrywać konkretną ścieżkę wideo."""
    try:
        # Port 9997 to port API MediaMTX (zgodnie z Twoim docker-compose)
        url = f"http://mediamtx:9997/v3/config/paths/patch/{path_name}"
        payload = {"record": should_record}
        response = requests.patch(url, json=payload, timeout=2)
        return response.status_code == 200
    except Exception as e:
        logging.info(f"Błąd API MediaMTX (REC): {e}")
        return False

# --- 1. KOMPONENT POMOCNICZY (Odświeżalny) ---
@ui.refreshable
def user_selection_ui(current_allowed: str, stream_id: int):
    """Generuje listę strażaków dla konkretnego drona."""
    with SessionLocal() as db:
        all_users = [str(u.username) for u in db.query(User).all()]
    
    allowed_list = current_allowed.split(',') if current_allowed else []
    
    ui.label('Uprawnienia dostępu:').classes('text-[10px] font-black text-zinc-500 mt-2 uppercase')
    
    with ui.column().classes('gap-0 w-full'):
        for user in all_users:
            is_on = user in allowed_list
            # Każdy klik od razu leci do bazy przez toggle_permission
            ui.checkbox(user, value=is_on, 
                        on_change=lambda e, u=user: toggle_permission(stream_id, u, e.value)) \
                .classes('text-sm')

# --- 2. LOGIKA ZAPISU (Zaszyta w tle) ---
def toggle_permission(stream_id: int, username: str, state: bool):
    """Standard OSP: Klikasz i zapisane. Bez zbędnych przycisków 'Zatwierdź'."""
    with SessionLocal() as db:
        stream = db.query(StreamPath).filter(StreamPath.id == stream_id).first()
        if not stream: return
            
        current = set(stream.allowed_users.split(',')) if stream.allowed_users else set()
        if state: current.add(username)
        else: current.discard(username)
            
        stream.allowed_users = ",".join(filter(None, current))
        db.commit()
        ui.notify(f"Zaktualizowano dostęp dla {username}", type='positive', position='bottom-right')

# --- 3. TWOJA GŁÓWNA FUNKCJA (Punkt wejścia) ---
def streams_management_interface(username, role):
    """To jest funkcja, którą już masz w kodzie. Tutaj ją aktualizujemy."""
    
    # Zabezpieczenie: Tylko admin widzi opcje edycji
    if role != 'admin':
        ui.label('Brak uprawnień do zarządzania strumieniami.').classes('text-red-500')
        return

    with SessionLocal() as db:
        streams = db.query(StreamPath).all()

    ui.label('Konfiguracja Strumieni Wideo').classes('text-2xl font-black mb-6')

    # Siatka z dronami
    with ui.grid(columns='1fr 1fr').classes('w-full gap-4'):
        for stream in streams:
            with ui.card().classes('bg-zinc-900 border-2 border-zinc-800 p-4 rounded-xl'):
                with ui.row().classes('w-full justify-between items-start'):
                    with ui.column().classes('gap-0'):
                        ui.label(stream.label or stream.path).classes('text-lg font-black text-orange-500')
                        ui.label(f"ID: {stream.path}").classes('text-[10px] font-mono text-zinc-600')
                    
                    # PRZYCISK ZARZĄDZANIA LUDŹMI
                    with ui.button(icon='person_add', color='zinc-700').props('flat round'):
                        with ui.menu().classes('p-4 bg-zinc-900 border border-zinc-700 shadow-2xl'):
                            ui.label(f'Uprawnienia: {stream.path}').classes('text-xs font-bold mb-2')
                            
                            # TUTAJ WYWOŁUJEMY NASZ KOMPONENT
                            # Wymuszamy odświeżenie przy każdym otwarciu menu!
                            user_selection_ui.refresh(stream.allowed_users, stream.id)
                            user_selection_ui(stream.allowed_users, stream.id)

                ui.separator().classes('my-4 bg-zinc-800')

                # Przełącznik nagrywania (MediaMTX)
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('Nagrywanie na serwerze').classes('text-xs text-zinc-400')
                    # Tu możesz podpiąć funkcję toggle_recording z MediaMTX
                    ui.switch(value=stream.record).props('color=orange')