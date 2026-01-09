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
    """Pobiera listę plików i pakuje je w słowniki z kluczami 'name', 'size', 'date'."""
    if not os.path.exists(RECORDINGS_DIR):
        return []
    
    recordings = []
    try:
        for f in os.listdir(RECORDINGS_DIR):
            # Filtrujemy tylko pliki wideo
            if f.endswith(('.mp4', '.mkv', '.ts')):
                full_path = os.path.join(RECORDINGS_DIR, f)
                stats = os.stat(full_path)
                
                # Tworzymy słownik - KAŻDY musi mieć te same klucze
                recordings.append({
                    'name': str(f),
                    'size': f"{stats.st_size / (1024*1024):.1f} MB",
                    'date': datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'raw_date': stats.st_mtime # do sortowania
                })
    except Exception as e:
        print(f"Błąd skanowania katalogu nagrań: {e}")
        return []

    # Sortujemy od najnowszych
    return sorted(recordings, key=lambda x: x['raw_date'], reverse=True)

async def delete_recording(filename: str, username: str, role: str):
    """
    Usuwa plik nagrania z dysku po potwierdzeniu przez użytkownika.
    """
    def perform_deletion():
        try:
            full_path = os.path.join(RECORDINGS_DIR, filename)
            if os.path.exists(full_path):
                os.remove(full_path)
                ui.notify(f"Usunięto nagranie: {filename}", type='info')
                # ODŚWIEŻAMY INTERFEJS (musimy przekazać argumenty!)
                archive_interface.refresh(username, role)
            else:
                ui.notify("Błąd: Plik nie istnieje na dysku.", type='negative')
        except Exception as e:
            ui.notify(f"Błąd podczas usuwania: {e}", type='negative')

    with ui.dialog() as confirm_dialog, ui.card().classes('p-6 bg-zinc-900 border-2 border-red-900/50'):
        ui.label(f'CZY NA PEWNO USUNĄĆ?').classes('text-lg font-black text-white uppercase')
        ui.label(f'Plik: {filename}').classes('text-sm text-zinc-400 mb-4')
        with ui.row().classes('w-full justify-end gap-3'):
            ui.button('ANULUJ', on_click=confirm_dialog.close).props('flat color=white')
            ui.button('USUŃ DEFINITYWNIE', on_click=lambda: [perform_deletion(), confirm_dialog.close()]) \
                .props('color=red-9 shadow-lg')
    
    confirm_dialog.open()

@ui.refreshable
def archive_interface(username: str, role: str):
    ui.label('Archiwum Nagrań Operacyjnych').classes('text-2xl font-black mb-6 text-white uppercase')
    
    recordings = get_recordings_list()
    
    if not recordings:
        with ui.column().classes('w-full items-center p-12 border-2 border-dashed border-zinc-800 rounded-2xl'):
            ui.icon('inventory_2', size='64px').classes('text-zinc-800')
            ui.label('Brak nagrań na dysku serwera.').classes('text-zinc-600 font-bold')
            return

    with ui.column().classes('w-full gap-4'):
        for rec in recordings:
            # Używamy rec.get('name'), aby uniknąć KeyError w razie błędu
            file_name = rec.get('name', 'Nieznany plik')
            file_date = rec.get('date', '--')
            file_size = rec.get('size', '0 MB')

            with ui.card().classes('bg-zinc-900 border border-zinc-800 w-full p-4 rounded-xl shadow-lg'):
                with ui.row().classes('w-full items-center justify-between'):
                    with ui.row().classes('items-center gap-4'):
                        ui.icon('videocam', color='orange').classes('text-2xl')
                        with ui.column().classes('gap-0'):
                            ui.label(file_name).classes('text-lg font-bold text-zinc-100')
                            ui.label(f"{file_date} | {file_size}").classes('text-xs text-zinc-500')
                    
                    with ui.row().classes('gap-2'):
                        ui.button(icon='play_arrow', on_click=lambda f=file_name: play_recording(f)) \
                            .props('flat round color=green')
                        
                        ui.button(icon='download', on_click=lambda f=file_name: ui.download(f"/recordings/{f}")) \
                            .props('flat round color=blue')

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
