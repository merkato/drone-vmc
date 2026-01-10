import os
#import requests
import logging
import httpx
from datetime import datetime
from nicegui import ui
from models import SessionLocal, User, StreamPath
from config import RECORDINGS_DIR, DOMAIN

def get_recordings_hierarchy():
    """
    Skanuje rekurencyjnie katalog nagrań i buduje strukturę:
    {
       'Grupa (np. Istebna)': {
           'Dron (np. Mini)': [lista plików],
           'Dron (np. Matrice)': [lista plików]
       }
    }
    """
    hierarchy = {}

    if not os.path.exists(RECORDINGS_DIR):
        return hierarchy

    # os.walk przejdzie przez wszystkie podfoldery automatycznie
    for root, dirs, files in os.walk(RECORDINGS_DIR):
        # Filtrujemy tylko pliki wideo
        video_files = [f for f in files if f.endswith(('.mp4', '.mkv', '.ts'))]
        
        if not video_files:
            continue

        # Obliczamy ścieżkę względną od folderu głównego (np. "istebna/mini")
        rel_path = os.path.relpath(root, RECORDINGS_DIR)
        parts = rel_path.split(os.sep)

        # Określamy Grupę i Drona na podstawie struktury folderów
        group_name = parts[0] if len(parts) > 0 and parts[0] != '.' else "Nieprzypisane"
        drone_name = parts[1] if len(parts) > 1 else "Ogólne"

        # Inicjalizacja kluczy w słowniku
        if group_name not in hierarchy:
            hierarchy[group_name] = {}
        if drone_name not in hierarchy[group_name]:
            hierarchy[group_name][drone_name] = []

        for f in video_files:
            full_path = os.path.join(root, f)
            try:
                stats = os.stat(full_path)
                file_info = {
                    'name': f,
                    'relative_path': os.path.join(rel_path, f), # potrzebne do ui.download i odtwarzacza
                    'size': f"{stats.st_size / (1024*1024):.1f} MB",
                    'date': datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'raw_date': stats.st_mtime
                }
                hierarchy[group_name][drone_name].append(file_info)
            except Exception as e:
                print(f"Błąd statystyk pliku {f}: {e}")

        # Sortowanie nagrań wewnątrz drona (od najnowszych)
        hierarchy[group_name][drone_name].sort(key=lambda x: x['raw_date'], reverse=True)

    return hierarchy

async def delete_recording(relative_path: str, username: str, role: str):
    """
    Usuwa plik nagrania z uwzględnieniem podfolderów (np. istebna/mini/plik.mp4).
    """
    def perform_deletion():
        try:
            # relative_path to np. "istebna/mini/nagranie.mp4"
            full_path = os.path.join(RECORDINGS_DIR, relative_path)
            
            if os.path.exists(full_path):
                os.remove(full_path)
                ui.notify(f"Zniszczono nagranie: {relative_path}", type='info')
                
                # ODŚWIEŻAMY INTERFEJS - lista zniknie z widoku
                archive_interface.refresh(username, role)
            else:
                ui.notify(f"Błąd: Nie znaleziono pliku w {relative_path}", type='negative')
        except Exception as e:
            ui.notify(f"Awaria systemu plików: {e}", type='negative')

    # DIALOG POTWIERDZENIA - Duży i czytelny dla OSP
    with ui.dialog() as confirm_dialog, ui.card().classes('p-8 bg-zinc-950 border-2 border-red-900/50 rounded-3xl shadow-2xl'):
        ui.label('POTWIERDŹ USUNIĘCIE').classes('text-xl font-black text-white uppercase tracking-tighter')
        ui.label(f'Lokalizacja: {relative_path}').classes('text-xs font-mono text-zinc-500 mb-6')
        
        with ui.row().classes('w-full justify-end gap-4'):
            ui.button('ANULUJ', on_click=confirm_dialog.close).props('flat color=white').classes('font-bold')
            ui.button('USUŃ DEFINITYWNIE', on_click=lambda: [perform_deletion(), confirm_dialog.close()]) \
                .props('color=red-9 shadow-lg').classes('px-6 font-black')
    
    confirm_dialog.open()

# --- POPRAWIONY ODTWARZACZ ---
def play_recording(relative_path: str):
    """Otwiera okno z odtwarzaczem, obsługując podfoldery."""
    with ui.dialog() as dialog, ui.card().classes('w-[900px] bg-black p-0 border-2 border-zinc-800 rounded-3xl overflow-hidden'):
        with ui.row().classes('w-full justify-between p-4 bg-zinc-900 items-center'):
            ui.label(f'PODGLĄD: {relative_path}').classes('text-zinc-400 font-bold text-xs truncate max-w-[700px]')
            ui.button(icon='close', on_click=dialog.close).props('flat color=white round')
        
        # dynamiczny URL do pliku w podfolderze
        ui.video(f'/recordings/{relative_path}').classes('w-full aspect-video')
    dialog.open()

@ui.refreshable
def archive_interface(username: str, role: str):
    ui.label('Archiwum Nagrań Operacyjnych').classes('text-3xl font-black mb-8 text-white uppercase tracking-tighter')
    
    # Pobieramy nową strukturę słownikową
    hierarchy = get_recordings_hierarchy()
    
    if not hierarchy:
        with ui.column().classes('w-full items-center p-12 border-2 border-dashed border-zinc-800 rounded-3xl'):
            ui.icon('inventory_2', size='64px').classes('text-zinc-800')
            ui.label('Brak nagrań na dysku serwera.').classes('text-zinc-600 font-bold uppercase')
            return

    # GŁÓWNA KONTENER GRUP (np. ISTEBNA, KONIAKÓW)
    with ui.column().classes('w-full gap-4'):
        for group_name, drones in hierarchy.items():
            with ui.expansion(group_name.upper(), icon='folder_shared').classes('w-full bg-zinc-950 border border-zinc-800 rounded-2xl text-orange-500 font-black'):
                
                # PODGRUPY (np. MINI, MATRICE)
                for drone_name, files in drones.items():
                    with ui.expansion(f"DRON: {drone_name.upper()}", icon='visibility').classes('ml-4 my-2 bg-zinc-900 border border-zinc-800 rounded-xl text-zinc-300 font-bold'):
                        
                        # LISTA PLIKÓW
                        with ui.column().classes('w-full gap-2 p-4'):
                            for rec in files:
                                file_name = rec.get('name')
                                rel_path = rec.get('relative_path') # np. "istebna/mini/nagranie.mp4"
                                
                                with ui.card().classes('bg-zinc-800 border border-zinc-700 w-full p-4 rounded-xl shadow-md hover:border-zinc-500 transition-all'):
                                    with ui.row().classes('w-full items-center justify-between'):
                                        # Info o pliku
                                        with ui.row().classes('items-center gap-4'):
                                            ui.icon('movie', color='orange').classes('text-xl')
                                            with ui.column().classes('gap-0'):
                                                ui.label(file_name).classes('text-base font-bold text-zinc-100')
                                                ui.label(f"{rec['date']} | {rec['size']}").classes('text-[10px] text-zinc-500 uppercase font-mono')
                                        
                                        # Przyciski Akcji
                                        with ui.row().classes('gap-3'):
                                            # Odtwarzanie - przekazujemy rel_path
                                            ui.button(icon='play_circle', on_click=lambda p=rel_path: play_recording(p)) \
                                                .props('flat round color=green size=md').tooltip('Odtwórz')
                                            
                                            # Pobieranie - URL uwzględnia strukturę folderów
                                            ui.button(icon='download', on_click=lambda p=rel_path: ui.download(f"/recordings/{p}")) \
                                                .props('flat round color=blue size=md').tooltip('Pobierz')
                                            
                                            # Usuwanie (tylko dla Admina)
                                            if role == 'admin':
                                                ui.button(icon='delete_forever', on_click=lambda p=rel_path: delete_recording(p, username, role)) \
                                                    .props('flat round color=red-9 size=md').tooltip('Usuń')

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
                    hls_url = f"http://stream.{DOMAIN}:8888/{stream.path_name}/index.m3u8?user={username}&password={password}"
                    video_id = f"video_{stream.id}"
    
                    # DODAJEMY sanitize=False na końcu
                    ui.html(f'''
                    <video id="{video_id}" controls autoplay muted playsinline class="w-full aspect-video bg-black rounded-lg shadow-inner">
                    <source src="{hls_url}" type="application/x-mpegURL">
                    Twoja przeglądarka nie obsługuje HLS.
                    </video>
                    ''', sanitize=False)
                    
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
