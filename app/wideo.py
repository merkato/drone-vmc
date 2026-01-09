import os
#import requests
import logging
import httpx
from datetime import datetime
from nicegui import ui
from models import SessionLocal, User, StreamPath
from config import RECORDINGS_DIR, DOMAIN

# --- LOGIKA SYSTEMU WIDEO ---
def get_recordings_list():
    """
    Skanuje folder nagrań, wyciąga metadane i sortuje od najnowszych.
    Zastępuje sztywne ścieżki (hardcoding) profesjonalną stałą RECORDINGS_DIR.
    """
    recordings = []
    
    # Sprawdzamy, czy folder w ogóle istnieje (bezpiecznik przed crashem)
    if not os.path.exists(RECORDINGS_DIR):
        logging.warning(f"Brak dostępu do folderu nagrań: {RECORDINGS_DIR}")
        return []

    try:
        # os.walk przejdzie przez wszystkie podfoldery (np. /recordings/drone1/...)
        for root, dirs, files in os.walk(RECORDINGS_DIR):
            for file in files:
                if file.endswith(('.mp4', '.m4v')):
                    full_path = os.path.join(root, file)
                    
                    # Ścieżka relatywna do RECORDINGS_DIR (potrzebna do URL i ID)
                    rel_path = os.path.relpath(full_path, RECORDINGS_DIR)
                    
                    try:
                        stats = os.stat(full_path)
                        # Rozmiar w MB - bez kilometrowych ułamków "a la Pesa"
                        size_mb = round(stats.st_size / (1024 * 1024), 2)
                        # Data utworzenia/modyfikacji
                        date_str = datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M')
                        
                        recordings.append({
                            'id': rel_path,
                            'drone': rel_path.split(os.sep)[0] if os.sep in rel_path else "System",
                            'filename': file,
                            'date': date_str,
                            'size': f"{size_mb} MB",
                            'url': f"/recordings/{rel_path}" # Serwowane przez app.add_static_files
                        })
                    except Exception as e:
                        logging.error(f"Nie można odczytać statystyk pliku {file}: {e}")

    except Exception as e:
        logging.error(f"Krytyczny błąd skanowania dysku: {e}")
        return []

    # Sortowanie: najnowsze nagrania (z największą datą) na samej górze
    return sorted(recordings, key=lambda x: x['date'], reverse=True)

@ui.refreshable
def archive_interface(username: str, role: str): # <--- DODAJEMY ARGUMENTY
    """Interfejs przeglądania i odtwarzania nagrań."""
    ui.label('Archiwum Nagrań Operacyjnych').classes('text-2xl font-black mb-6 text-white uppercase')
    
    # Możemy teraz dodać logikę uprawnień!
    # Np. tylko admin może usuwać nagrania
    is_admin = (role == 'admin')

    recordings = get_recordings_list()
    
    if not recordings:
        with ui.column().classes('w-full items-center p-12 border-2 border-dashed border-zinc-800 rounded-2xl'):
            ui.icon('inventory_2', size='64px').classes('text-zinc-800')
            ui.label('Brak nagrań na dysku serwera.').classes('text-zinc-600 font-bold')
            return

    with ui.column().classes('w-full gap-4'):
        for rec in recordings:
            with ui.card().classes('bg-zinc-900 border border-zinc-800 w-full p-4 rounded-xl shadow-lg'):
                with ui.row().classes('w-full items-center justify-between'):
                    with ui.row().classes('items-center gap-4'):
                        ui.icon('videocam', color='orange').classes('text-2xl')
                        with ui.column().classes('gap-0'):
                            ui.label(rec['name']).classes('text-lg font-bold text-zinc-100')
                            ui.label(f"{rec['date']} | {rec['size']}").classes('text-xs text-zinc-500')
                    
                    with ui.row().classes('gap-2'):
                        ui.button(icon='play_arrow', on_click=lambda r=rec: play_recording(r['name'])) \
                            .props('flat round color=green')
                        
                        ui.button(icon='download', on_click=lambda r=rec: ui.download(f"/recordings/{rec['name']}")) \
                            .props('flat round color=blue')

                        # DODATKOWO: Usuwanie tylko dla Admina (wykorzystujemy role)
                        if is_admin:
                            ui.button(icon='delete', on_click=lambda r=rec: delete_recording(r['name'])) \
                                .props('flat round color=red')

def play_recording(filename):
    """Otwiera okno dialogowe z odtwarzaczem wideo."""
    with ui.dialog() as dialog, ui.card().classes('w-[800px] bg-black p-0'):
        with ui.row().classes('w-full justify-between p-4 bg-zinc-900'):
            ui.label(f'Odtwarzanie: {filename}').classes('text-white font-bold')
            ui.button(icon='close', on_click=dialog.close).props('flat color=white')
        
        # Odtwarzacz NiceGUI
        ui.video(f'/recordings/{filename}').classes('w-full aspect-video')
    dialog.open()

# --- 1. POBIERANIE AKTYWNYCH STRUMIENI Z API MEDIAMTX ---
async def get_active_streams_from_api():
    """Sprawdza w MediaMTX, które ścieżki faktycznie nadają obraz."""
    try:
        async with httpx.AsyncClient() as client:
            # Zakładamy, że API MediaMTX jest na porcie 9997
            response = await client.get(f"http://api.{DOMAIN}:9997/v3/paths/list")
            if response.status_code == 200:
                data = response.json()
                # Zwracamy listę nazw aktywnych ścieżek, które mają podpięte źródło (source)
                return [p['name'] for p in data['items'] if p.get('source')]
    except Exception as e:
        print(f"Błąd API MediaMTX: {e}")
    return []

# --- 2. ODŚWIEŻALNA TREŚĆ GRIDU ---
@ui.refreshable
async def live_grid_content(username: str, password: str):
    """Generuje siatkę podglądu na żywo dla konkretnego użytkownika."""
    active_paths = await get_active_streams_from_api()
    
    with SessionLocal() as db:
        # Pobieramy tylko te strumienie z bazy, do których zalogowany użytkownik ma uprawnienia Widza
        user = db.query(User).filter(User.username == username).first()
        if not user:
            ui.label('Błąd autoryzacji użytkownika.').classes('text-red-500')
            return

        # Pobieramy strumienie, gdzie user jest w authorized_viewers
        accessible_streams = db.query(StreamPath).join(
            StreamPath.authorized_viewers
        ).filter(User.id == user.id).all()

    if not accessible_streams:
        ui.label('Nie masz uprawnień do żadnego aktywnego strumienia.').classes('text-zinc-500 p-8')
        return

    # SIATKA: 2 KOLUMNY, SKALOWALNA (W-FULL)
    with ui.grid(columns='1fr 1fr').classes('w-full gap-4 p-2'):
        for stream in accessible_streams:
            # Wyświetlamy tylko, jeśli dron faktycznie nadaje (jest w API)
            is_live = stream.path_name in active_paths
            
            with ui.card().classes('bg-zinc-900 border-2 rounded-xl overflow-hidden').style(
                f'border-color: {"#f97316" if is_live else "#27272a"}'
            ):
                # Nagłówek karty z nazwą i statusem
                with ui.row().classes('w-full justify-between p-2 bg-zinc-950/50'):
                    ui.label(stream.description or stream.path_name).classes('text-sm font-black text-white uppercase')
                    ui.badge('LIVE' if is_live else 'OFFLINE', color='orange' if is_live else 'zinc-700')

                # ODTWARZACZ HLS Z AUTORYZACJĄ
                if is_live:
                    # Link HLS z Twoimi parametrami ?user=xxx&password=yyy
                    hls_url = f"http://stream.{DOMAIN}:8888/{stream.path_name}/index.m3u8?user={username}&password={password}"
                    
                    # Kontener na wideo z unikalnym ID dla Fullscreena
                    video_id = f"video_{stream.id}"
                    ui.html(f'''
                        <video id="{video_id}" controls autoplay muted playsinline class="w-full aspect-video">
                            <source src="{hls_url}" type="application/x-mpegURL">
                            Twoja przeglądarka nie obsługuje HLS.
                        </video>
                    ''')
                    
                    # STOPKA Z LINKIEM FULLSCREEN
                    with ui.row().classes('w-full justify-end p-1'):
                        ui.button('PEŁNY EKRAN', icon='fullscreen', 
                                  on_click=lambda v=video_id: ui.run_javascript(f'document.getElementById("{v}").requestFullscreen()')) \
                            .props('flat dense color=orange').classes('text-[10px] font-bold')
                else:
                    # Placeholder gdy dron nie nadaje
                    with ui.column().classes('w-full aspect-video items-center justify-center bg-black/40'):
                        ui.icon('videocam_off', size='48px').classes('text-zinc-800')
                        ui.label('Oczekiwanie na sygnał...').classes('text-[10px] text-zinc-700 uppercase')

async def live_grid_interface(username: str, role: str, password: str):
    """Główny punkt wejścia dla podglądu operacyjnego."""
    ui.label('Panel Operacyjny - Podgląd na Żywo').classes('text-2xl font-black mb-4 uppercase tracking-tighter')
    
    # Przycisk ręcznego odświeżania (jak w porządnym systemie OSP)
    with ui.row().classes('mb-4 items-center gap-4'):
        ui.button('ODŚWIEŻ LISTĘ', icon='refresh', on_click=lambda: live_grid_content.refresh(username, password)) \
            .props('outline color=orange')
        ui.label('System sprawdza aktywność dronów co 10 sekund').classes('text-[10px] text-zinc-600 italic')

    # Wywołanie odświeżalnej treści
    await live_grid_content(username, password)
    
    # Timer do automatycznego odświeżania (sprawdzanie kto dołączył/odszedł)
    ui.timer(10.0, lambda: live_grid_content.refresh(username, password))
