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

# --- 1. POBIERANIE AKTYWNYCH STRUMIENI Z API MEDIAMTX ---
async def get_active_streams_from_api():
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            # Pamiętaj o stream.DOMAIN i porcie API MediaMTX (standardowo 9997)
            r = await client.get(f"https://api.{DOMAIN}:9997/v3/paths/list")
            if r.status_code == 200:
                data = r.json()
                return [p['name'] for p in data.get('items', []) if p.get('source')]
    except:
        return []
    return []

async def live_grid_interface(username, role, password):
    """Główny interfejs budowany RAZ przy wejściu w zakładkę."""
    ui.label('PANEL OPERACYJNY - PODGLĄD LIVE').classes('text-3xl font-black mb-6 text-white uppercase')

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == username).first()
        if not user: return
        # Pobieramy wszystkie strumienie, do których user ma uprawnienia (nawet te offline)
        accessible_streams = db.query(StreamPath).join(StreamPath.authorized_viewers).filter(User.id == user.id).all()

    # Tworzymy stałą siatkę (2 kolumny)
    grid = ui.grid(columns=2).classes('w-full gap-8')

    with grid:
        for stream in accessible_streams:
            with ui.card().classes('bg-zinc-900 border-2 border-zinc-800 rounded-3xl overflow-hidden shadow-2xl'):
                # Nagłówek drona
                with ui.row().classes('w-full justify-between p-4 bg-zinc-950/50'):
                    ui.label(stream.description or stream.path_name).classes('text-lg font-black text-white uppercase')
                    # Ten badge będziemy aktualizować dynamicznie
                    status_badge = ui.badge('OFFLINE', color='zinc-800').props('text-color=zinc-500')

                # KONTENER NA WIDEO / PLACEHOLDER
                # To jest klucz: ten kontener będzie czyszczony TYLKO przy zmianie statusu
                content_slot = ui.column().classes('w-full aspect-video bg-black items-center justify-center')
                
                # Przechowujemy referencje do aktualizacji
                video_containers[stream.id] = {
                    'slot': content_slot,
                    'badge': status_badge,
                    'path': stream.path_name
                }
                
                # Przycisk Fullscreen (zawsze widoczny, działa dynamicznie)
                with ui.row().classes('w-full justify-end p-2'):
                    hls_url = f"https://stream.{DOMAIN}/{stream.path_name}/index.m3u8?user={username}&password={password}"
                    ui.button(icon='fullscreen', on_click=lambda u=hls_url: ui.run_javascript(f'window.open("{u}", "_blank")')) \
                        .props('flat color=orange size=lg')

    # Timer do inteligentnej aktualizacji statusów (bez przeładowywania całego gridu)
    async def update_status_loop():
        active_now = await get_active_streams_from_api()
        
        for s_id, data in video_containers.items():
            is_live = data['path'] in active_now
            was_live = last_statuses.get(s_id, False)

            if is_live != was_live:
                # ZMIANA STANU - TYLKO WTEDY REAGUJEMY
                data['slot'].clear()
                if is_live:
                    data['badge'].set_text('LIVE')
                    data['badge'].props('color=orange text-color=white')
                    with data['slot']:
                        hls_url = f"https://stream.{DOMAIN}/{data['path']}/index.m3u8?user={username}&password={password}"
                        ui.video(hls_url).classes('w-full h-full').props('autoplay muted playsinline loop controls')
                else:
                    data['badge'].set_text('OFFLINE')
                    data['badge'].props('color=zinc-800 text-color=zinc-500')
                    with data['slot']:
                        ui.icon('videocam_off', size='64px').classes('text-zinc-800')
                        ui.label('OCZEKIWANIE NA SYGNAŁ...').classes('text-[10px] text-zinc-700 font-bold mt-2')
                
                last_statuses[s_id] = is_live

    # Uruchamiamy pętlę sprawdzającą co 5 sekund
    ui.timer(5.0, update_status_loop)
    # Wywołujemy raz na starcie
    ui.timer(0.1, update_status_loop, once=True)
