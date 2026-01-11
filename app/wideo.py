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
    ui.label('Archiwum Nagrań').classes('text-3xl font-black mb-8 text-white uppercase')
    
    hierarchy = get_recordings_hierarchy()
    if not hierarchy:
        ui.label('Brak nagrań.').classes('text-zinc-500 p-8')
        return

    for group_name, drones in hierarchy.items():
        with ui.expansion(group_name.upper(), icon='folder').classes('w-full bg-zinc-950 border border-zinc-800 rounded-2xl mb-4'):
            # SIATKA KAFELKÓW: 2 kolumny dla podgrup (dronów)
            with ui.grid(columns=2).classes('w-full gap-4 p-4'):
                for drone_name, files in drones.items():
                    with ui.card().classes('bg-zinc-900 border border-zinc-800 p-4 rounded-xl shadow-lg'):
                        ui.label(drone_name.upper()).classes('text-orange-500 font-black mb-2 border-b border-zinc-800 pb-1')
                        
                        # Lista plików wewnątrz kafelka drona
                        with ui.column().classes('w-full gap-2'):
                            for rec in files:
                                with ui.row().classes('w-full items-center justify-between no-wrap'):
                                    ui.label(rec['name']).classes('text-[10px] text-zinc-100 truncate flex-grow')
                                    with ui.row().classes('gap-1'):
                                        ui.button(icon='play_arrow', on_click=lambda r=rec: play_recording(r['relative_path'])) \
                                            .props('flat dense size=sm color=green')
                                        ui.button(icon='download', on_click=lambda r=rec: ui.download(f"/recordings/{r['relative_path']}")) \
                                            .props('flat dense size=sm color=blue')

# --- POBIERANIE AKTYWNYCH STRUMIENI Z API MEDIAMTX ---
async def get_active_streams_from_api():
    """Odpytuje MediaMTX przez sieć wewnętrzną Dockera."""
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            # Używamy nazwy usługi z docker-compose: 'mediamtx'
            r = await client.get("http://mediamtx:9997/v3/paths/list")
            if r.status_code == 200:
                data = r.json()
                active = [p['name'] for p in data.get('items', []) if p.get('source')]
                logging.info(f"DEBUG: Aktywne ścieżki z API: {active}") # Odkomentuj do testów
                return active
    except Exception as e:
        logging.error(f"Błąd API MediaMTX: {e}")
    return []

# Słowniki do trzymania referencji (poza funkcją)
slots = {} 
last_status = {}

async def live_grid_content(username: str, password: str):
    """Buduje szkielet siatki. To wywołujemy RAZ."""
    active_paths = await get_active_streams_from_api()
    
    with SessionLocal() as db:
        user = db.query(User).filter(User.username == username).first()
        if not user: return
        accessible_streams = db.query(StreamPath).join(StreamPath.authorized_viewers).filter(User.id == user.id).all()

    if not accessible_streams:
        ui.label('Brak uprawnień do strumieni.').classes('text-zinc-500 p-8')
        return

    # SIATKA: Budujemy ją raz
    with ui.grid(columns='1fr 1fr').classes('w-full gap-4 p-2') as grid:
        for stream in accessible_streams:
            is_live = stream.path_name in active_paths
            
            # KARTA (zostaje na stałe)
            card = ui.card().classes('bg-zinc-900 border-2 rounded-xl overflow-hidden shadow-2xl transition-all')
            card.style(f'border-color: {"#f97316" if is_live else "#27272a"}')
            
            with card:
                # Nagłówek (zostaje na stałe)
                with ui.row().classes('w-full justify-between p-2 bg-zinc-950/50'):
                    ui.label(stream.description or stream.path_name).classes('text-sm font-black text-white uppercase')
                    badge = ui.badge('LIVE' if is_live else 'OFFLINE', color='orange' if is_live else 'zinc-700')

                # SLOT NA TREŚĆ (to będziemy czyścić tylko przy zmianie statusu)
                video_slot = ui.column().classes('w-full aspect-video items-center justify-center bg-black/40')
                
                # Przycisk Fullscreen (zawsze pod slotem)
                hls_url = f"https://stream.{DOMAIN}/{stream.path_name}/index.m3u8?user={username}&password={password}"
                fs_btn = ui.button('PEŁNY EKRAN', icon='fullscreen', 
                                  on_click=lambda u=hls_url: ui.run_javascript(f'window.open("{u}", "_blank")')) \
                            .props('flat color=orange').classes('text-xs font-bold mt-2 mx-auto')
                fs_btn.set_visibility(is_live)

                # Zapamiętujemy slot i badge dla tego drona
                slots[stream.id] = {
                    'video_slot': video_slot,
                    'badge': badge,
                    'card': card,
                    'fs_btn': fs_btn,
                    'path': stream.path_name
                }
                
                # Inicjalne wypełnienie slotu
                update_card_content(stream.id, is_live, username, password)
                last_status[stream.id] = is_live

def update_card_content(stream_id, is_live, username, password):
    """Wypełnia slot wideo lub placeholderem."""
    data = slots[stream_id]
    data['video_slot'].clear()
    
    with data['video_slot']:
        if is_live:
            hls_url = f"https://stream.{DOMAIN}/{data['path']}/index.m3u8?user={username}&password={password}"
            ui.video(hls_url).classes('w-full h-full rounded-xl').props('autoplay muted playsinline loop controls')
        else:
            ui.icon('videocam_off', size='48px').classes('text-zinc-800')
            ui.label('Oczekiwanie na sygnał...').classes('text-[10px] text-zinc-700 uppercase')

async def live_grid_interface(username: str, role: str, password: str):
    ui.label('Panel Operacyjny - Podgląd na Żywo').classes('text-2xl font-black mb-4 uppercase tracking-tighter')
    
    # Przycisk "Twardego" odświeżenia
    ui.button('ZRESETUJ WIDOK', icon='refresh', on_click=lambda: ui.navigate.to('/')) \
        .props('outline color=orange').classes('mb-4')

    # Budujemy grid raz
    await live_grid_content(username, password)
    
    # TIMER: Inteligente sprawdzanie zmian
    async def check_for_updates():
        active_paths = await get_active_streams_from_api()
        for s_id, data in slots.items():
            is_live_now = data['path'] in active_paths
            if is_live_now != last_status[s_id]:
                # STATUS SIĘ ZMIENIŁ - tylko wtedy dotykamy DOM
                update_card_content(s_id, is_live_now, username, password)
                data['badge'].set_text('LIVE' if is_live_now else 'OFFLINE')
                data['badge'].props(f'color={"orange" if is_live_now else "zinc-700"}')
                data['card'].style(f'border-color: {"#f97316" if is_live_now else "#27272a"}')
                data['fs_btn'].set_visibility(is_live_now)
                last_status[s_id] = is_live_now

    ui.timer(5.0, check_for_updates)