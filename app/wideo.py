import os
import requests
from datetime import datetime
from nicegui import ui
from models import SessionLocal, User, StreamPath
from config import MEDIAMTX_WEBRTC, RECORDINGS_DIR

# --- LOGIKA SYSTEMU WIDEO ---

def get_active_streams_from_api():
    """Pobiera listę ścieżek z MediaMTX, które aktualnie nadają sygnał."""
    try:
        response = requests.get('http://mediamtx:9997/v3/paths/list', timeout=1)
        if response.status_code == 200:
            data = response.json()
            # Zwracamy listę nazw ścieżek, które mają status 'ready: True'
            return [item['name'] for item in data.get('items', []) if item.get('ready')]
    except Exception as e:
        print(f"Błąd sprawdzania statusu LIVE: {e}")
    return []

def get_recordings_list():
    """Skanuje folder /recordings w poszukiwaniu plików wideo."""
    base_path = '/recordings'
    recordings = []
    if not os.path.exists(base_path):
        return []

    for root, dirs, files in os.walk(base_path):
        for file in files:
            if file.endswith(('.mp4', '.m4v')):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, base_path)
                
                stats = os.stat(full_path)
                size_mb = round(stats.st_size / (1024 * 1024), 2)
                date_str = datetime.fromtimestamp(stats.st_ctime).strftime('%Y-%m-%d %H:%M')
                
                recordings.append({
                    'id': rel_path,
                    'drone': rel_path.split(os.sep)[0],
                    'filename': file,
                    'date': date_str,
                    'size': f"{size_mb} MB",
                    'url': f"/recordings/{rel_path}"
                })
    return sorted(recordings, key=lambda x: x['date'], reverse=True)

    
# --- INTERFEJS: GRID OPERACYJNY (LIVE) ---
@ui.refreshable
def live_grid_content(username, role):
    """
    Scentralizowana funkcja Gridu: pobiera dane, filtruje uprawnienia 
    i rysuje interfejs w jednym miejscu.
    """
    # 1. Pobieramy statusy LIVE z API MediaMTX
    active_paths = get_active_streams_from_api()

    # 2. Pobieramy i filtrujemy strumienie bezpośrednio z bazy
    with SessionLocal() as db:
        if role == 'admin':
            streams = db.query(StreamPath).all()
        else:
            user = db.query(User).filter(User.username == username).first()
            if not user:
                ui.label('Błąd autoryzacji sesji.').classes('text-red-500')
                return
            
            # Pobieramy to co użytkownik posiada LUB to do czego został dopisany jako widz
            owned = db.query(StreamPath).filter(StreamPath.owner_username == username).all()
            assigned = user.allowed_streams # Relacja z models.py
            
            # Deduplikacja (słownik po nazwie ścieżki)
            unique_map = {s.path_name: s for s in (owned + assigned)}
            streams = list(unique_map.values())

    # 3. Renderowanie UI
    if not streams:
        with ui.column().classes('w-full items-center p-12'):
            ui.icon('videocam_off', size='lg').classes('text-zinc-800')
            ui.label('BRAK DOSTĘPNYCH STRUMIENI').classes('text-zinc-600 font-bold mt-2')
            ui.label('Poproś administratora o przypisanie drona do Twojego konta.').classes('text-zinc-700 text-xs')
        return

    # Siatka kafelków
    with ui.grid(columns='1fr 1fr 1fr').classes('w-full gap-4 p-4'):
        for s in streams:
            is_live = s.path_name in active_paths
            
            with ui.card().classes('bg-zinc-900 p-0 overflow-hidden border border-zinc-800 shadow-xl relative'):
                # Nagłówek kafelka (Status Bar)
                with ui.row().classes('w-full p-2 justify-between items-center bg-zinc-950/80 border-b border-zinc-800'):
                    with ui.column().classes('gap-0'):
                        ui.label(s.path_name.upper()).classes('text-[10px] font-black text-orange-500 tracking-tighter')
                        ui.label(s.description or 'MISJA OPERACYJNA').classes('text-[8px] text-zinc-500 truncate w-32 uppercase')
                    
                    # Sygnalizacja LIVE
                    dot_color = 'red-600' if is_live else 'zinc-700'
                    with ui.row().classes('items-center gap-1 bg-black/40 px-2 py-1 rounded'):
                        ui.icon('fiber_manual_record', color=dot_color).classes('text-[10px]' + (' animate-pulse' if is_live else ''))
                        ui.label('LIVE' if is_live else 'OFF').classes(f'text-[9px] font-black text-{dot_color}')

                # Odtwarzacz Iframe (MediaMTX HLS)
                # Adres streamu z opcjonalnymi parametrami auth
                stream_url = f"https://stream.giswgorach.pl/{s.path_name}/"
                
                ui.html(f'''
    <video style="width:100%; height:auto;" autoplay muted controls>
        <source src="http://{DOMAIN}:8888/{path}/index.m3u8" type="application/x-mpegURL">
    </video>
''', sanitize=False)

# --- INTERFEJS: LIVE GRID - wywołanie ---
def live_grid_interface(username, role):
    """Tę funkcję wywołuje main_page w zakładce GRID OPERACYJNY."""
    with ui.column().classes('w-full p-4 bg-black'):
        # Inicjalne wywołanie zawartości
        live_grid_content(username, role)
        
        # Automatyczne odświeżanie statusów LIVE i listy strumieni co 5 sekund
        ui.timer(30.0, live_grid_content.refresh)

def archive_interface(username, role):
    """Panel przeglądania i odtwarzania nagrań Mp4."""
    ui.label(f'Archiwum dla użytkownika: {username}').classes('text-white')
    # Kontener na odtwarzacz (pojawia się po kliknięciu Play)
    player_box = ui.column().classes('w-full mb-6 bg-black border border-orange-900 rounded-lg overflow-hidden shadow-2xl')
    player_box.set_visibility(False)

    def play(row):
        player_box.clear()
        player_box.set_visibility(True)
        with player_box:
            with ui.row().classes('w-full p-2 bg-zinc-900 justify-between items-center'):
                ui.label(f"NAGRANIE: {row['filename']}").classes('text-[10px] font-bold text-orange-500')
                ui.button(icon='close', on_click=lambda: player_box.set_visibility(False)).props('flat round size=sm color=white')
            ui.video(row['url']).classes('w-full h-96').props('controls autoplay')
        ui.run_javascript('window.scrollTo({top: 0, behavior: "smooth"})')

    async def delete_rec(row):
        # Proste potwierdzenie usunięcia
        with ui.dialog() as diag, ui.card():
            ui.label(f"Usunąć {row['filename']}?")
            with ui.row():
                ui.button('TAK', on_click=lambda: diag.submit(True)).props('color=red')
                ui.button('NIE', on_click=diag.close)
        
        if await diag:
            try:
                os.remove(os.path.join('/recordings', row['id']))
                ui.notify('Usunięto plik')
                table.rows = get_recordings_list()
                player_box.set_visibility(False)
            except Exception as e:
                ui.notify(f'Błąd: {e}', color='negative')

    # Tabela Archiwum
    ui.label('ARCHIWUM PLIKÓW WIDEO').classes('text-orange-500 font-bold mb-4')
    
    columns = [
        {'name': 'date', 'label': 'DATA', 'field': 'date', 'align': 'left', 'sortable': True},
        {'name': 'drone', 'label': 'DRON', 'field': 'drone', 'align': 'left'},
        {'name': 'size', 'label': 'WIELKOŚĆ', 'field': 'size', 'align': 'right'},
        {'name': 'actions', 'label': 'AKCJE', 'field': 'actions', 'align': 'right'},
    ]

    table = ui.table(columns=columns, rows=get_recordings_list(), row_key='id') \
        .classes('w-full bg-zinc-950 border border-zinc-900 shadow-xl').props('dark flat border')

    # Slot dla akcji (Play, Download, Delete)
    # Rozwiązanie problemu backslasha w f-string:
    del_btn = '<q-btn flat round icon="delete" color="red" size="sm" @click="$parent.$emit(\'del\', props.row)"></q-btn>'
    
    table.add_slot('body-cell-actions', f'''
        <q-td :props="props">
            <q-btn flat round icon="play_circle" color="orange" size="sm" @click="$parent.$emit('play', props.row)"></q-btn>
            <q-btn flat round icon="download" color="blue" size="sm" @click="$parent.$emit('down', props.row.url)"></q-btn>
            {del_btn if role == 'admin' else ''}
        </q-td>
    ''')

    table.on('play', lambda e: play(e.args))
    table.on('down', lambda e: ui.download(e.args))
    table.on('del', lambda e: delete_rec(e.args))