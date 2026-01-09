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

# --- 1. KOMPONENT ODŚWIEŻALNY (LISTA STRAŻAKÓW) ---
@ui.refreshable
def user_selection_ui(current_allowed: str, stream_id: int):
    """Generuje listę checkboxów. Odświeża się przy każdym otwarciu menu."""
    with SessionLocal() as db:
        # Pobieramy nazwy wszystkich użytkowników
        all_users = [str(u.username) for u in db.query(User).all()]
    
    # Przekształcamy "user1,user2" na listę do porównania
    allowed_list = current_allowed.split(',') if current_allowed else []
    
    ui.label('UPRAWNIENIA DOSTĘPU').classes('text-[10px] font-black text-zinc-500 mb-2 uppercase tracking-widest')
    
    with ui.column().classes('gap-0 w-full'):
        if not all_users:
            ui.label('Brak użytkowników w bazie').classes('text-xs text-zinc-600 italic')
        
        for user in all_users:
            is_on = user in allowed_list
            # Kliknięcie od razu zapisuje zmianę w bazie
            ui.checkbox(user, value=is_on, 
                        on_change=lambda e, u=user: toggle_permission(stream_id, u, e.value)) \
                .classes('text-sm font-medium')

# --- 2. LOGIKA ZAPISU (DYNAMICZNA) ---
def toggle_permission(stream_id: int, username: str, state: bool):
    """Zapisuje uprawnienia w locie. Nie wymaga przycisku 'Zapisz'."""
    with SessionLocal() as db:
        stream = db.query(StreamPath).filter(StreamPath.id == stream_id).first()
        if not stream:
            return
            
        # Zarządzamy zbiorem (set), żeby uniknąć duplikatów
        current = set(stream.allowed_users.split(',')) if stream.allowed_users else set()
        
        if state:
            current.add(username)
        else:
            current.discard(username)
            
        # Zapisujemy z powrotem jako string rozdzielony przecinkami
        stream.allowed_users = ",".join(filter(None, current))
        db.commit()
        ui.notify(f"Dostęp dla {username}: {'NADAWANY' if state else 'ODEBRANY'}", 
                  type='info', position='bottom-right')

# --- 3. TWOJA GŁÓWNA FUNKCJA INTERFEJSU ---
def streams_management_interface(username, role):
    """Główny punkt wejścia wywoływany z app.py."""
    
    # Tylko admin może zarządzać strumieniami
    if role != 'admin':
        with ui.column().classes('w-full items-center p-12'):
            ui.icon('lock', size='64px').classes('text-zinc-700')
            ui.label('Strefa zastrzeżona dla Administratora').classes('text-xl text-zinc-500')
        return

    with SessionLocal() as db:
        streams = db.query(StreamPath).all()

    ui.label('Konfiguracja Strumieni Wideo').classes('text-2xl font-black mb-6 text-white')

    # Siatka kart strumieni (Grid)
    with ui.grid(columns='1fr 1fr').classes('w-full gap-4'):
        for stream in streams:
            with ui.card().classes('bg-zinc-900 border-2 border-zinc-800 p-4 rounded-xl shadow-xl'):
                
                # Nagłówek: Nazwa i przycisk uprawnień
                with ui.row().classes('w-full justify-between items-start'):
                    with ui.column().classes('gap-0'):
                        ui.label(stream.label or stream.path).classes('text-lg font-black text-orange-500 uppercase')
                        ui.label(f"Ścieżka: {stream.path}").classes('text-[10px] font-mono text-zinc-600')
                    
                    # Ikona ludzika otwierająca menu uprawnień
                    with ui.button(icon='person_add', color='zinc-700').props('flat round'):
                        with ui.menu().classes('p-4 bg-zinc-900 border border-zinc-700 shadow-2xl min-w-[200px]'):
                            ui.label(f'Dostęp: {stream.path}').classes('text-xs font-bold text-orange-400 mb-2')
                            
                            # WYWOŁANIE KOMPONENTU DYNAMICZNEGO
                            # Odświeżamy listę osób przy każdym kliknięciu w ikonę!
                            user_selection_ui.refresh(stream.allowed_users, stream.id)
                            user_selection_ui(stream.allowed_users, stream.id)

                ui.separator().classes('my-4 bg-zinc-800')

                # Kontrola nagrywania
                with ui.row().classes('w-full justify-between items-center'):
                    with ui.column().classes('gap-0'):
                        ui.label('NAGRYWANIE ARCHIWALNE').classes('text-[10px] font-bold text-zinc-500 tracking-widest')
                        ui.label('Automatyczny zapis na dysku').classes('text-[9px] text-zinc-700')
                    
                    # Switch do sterowania MediaMTX
                    ui.switch(value=stream.record).props('color=orange')