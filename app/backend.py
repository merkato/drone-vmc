#backend.py
import time
import os
import logging
from pathlib import Path
import psutil
import shutil

from datetime import datetime
from collections import defaultdict
from nicegui import ui
from models import SessionLocal, SystemConfig

# --- KONFIGURACJA ---
RECORDINGS_DIR = '/recordings'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
# --- DANE IN-MEMORY (Znikają po restarcie kontenera) ---
stats_history = defaultdict(list)
last_bytes = {}
alert_logs = []

def add_alert(drone, msg, level='warning'):
    """Rejestruje alert w systemie i wyświetla powiadomienie."""
    alert = {
        'time': datetime.now().strftime('%H:%M:%S'), 
        'drone': drone, 
        'msg': msg, 
        'level': level
    }
    alert_logs.insert(0, alert)
    
    # Utrzymujemy tylko 20 ostatnich alertów
    if len(alert_logs) > 20: 
        alert_logs.pop()
        
    # Wyświetlenie powiadomienia w interfejsie NiceGUI
    ui.notify(
        f"[{drone}] {msg}", 
        type='negative' if level == 'critical' else 'warning',
        position='top-right'
    )

def get_sys_resources():
    """Pobiera dane o zużyciu procesora, RAMu i wolnym miejscu na dysku."""
    try:
        total, used, free = shutil.disk_usage(RECORDINGS_DIR)
        return {
            'cpu': psutil.cpu_percent(),
            'ram': psutil.virtual_memory().percent,
            'disk_pct': (used / total) * 100,
            'disk_free': free // (2**30), # Wynik w GB
            'disk_total': total // (2**30)
        }
    except FileNotFoundError:
        # Na wypadek gdyby folder nie istniał poza dockerem
        return {'cpu': 0, 'ram': 0, 'disk_pct': 0, 'disk_free': 0, 'disk_total': 0}

def system_info_ui():
    """Komponent UI wyświetlający stan serwera (możesz go użyć w KONFIGURACJI)."""
    res = get_sys_resources()
    
    with ui.row().classes('w-full gap-4 items-center justify-between p-4 bg-zinc-900 rounded-lg border border-zinc-800'):
        with ui.column().classes('items-center'):
            ui.label('CPU').classes('text-[10px] text-zinc-500')
            ui.knob(res['cpu'] / 100, show_value=True, color='orange').props('size=60px center-color=zinc-950')
            
        with ui.column().classes('items-center'):
            ui.label('RAM').classes('text-[10px] text-zinc-500')
            ui.knob(res['ram'] / 100, show_value=True, color='blue').props('size=60px center-color=zinc-950')
            
        with ui.column().classes('items-center flex-grow'):
            ui.label('DYSK (RECORDINGS)').classes('text-[10px] text-zinc-500')
            ui.linear_progress(value=res['disk_pct'] / 100, color='red').classes('w-full')
            ui.label(f"{res['disk_free']} GB wolne z {res['disk_total']} GB").classes('text-[10px] text-zinc-400')

# --- LOGIKA BACKENDU: GOOGLE DRIVE & RETENCJA ---
def upload_to_gdrive(file_path, folder_id):
    from googleapiclient.discovery import build
    from google.oauth2 import service_account
    from googleapiclient.http import MediaFileUpload
    try:
        if not os.path.exists('credentials.json'): 
            logging.warning("Brak credentials.json - pomijam wysyłanie do chmury.")
            return False
        creds = service_account.Credentials.from_service_account_file('credentials.json', scopes=['https://www.googleapis.com/auth/drive.file'])
        service = build('drive', 'v3', credentials=creds)
        file_metadata = {'name': file_path.name, 'parents': [folder_id] if folder_id else []}
        media = MediaFileUpload(str(file_path), mimetype='video/mp4', resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return True
    except Exception as e:
        logging.error(f"Gdrive Error: {e}")
        return False

async def run_retention_task():
    """Zadanie uruchamiane okresowo do czyszczenia/backupu plików."""
    logging.info("Rozpoczynam procedurę retencji danych...")
    with SessionLocal() as db:
        config = db.query(SystemConfig).first()
        if not config:
            return # Brak konfiguracji, nic nie robimy

        now = time.time()
        # Obliczamy próg w sekundach: $T = n \times 24 \times 3600$
        retention_secs = config.retention_days * 24 * 3600

        for vid in RECORDINGS_DIR.rglob("*.mp4"):
            file_age = now - vid.stat().st_mtime
            
            if file_age > retention_secs:
                if config.retention_policy == "BACKUP" and config.gdrive_folder_id:
                    logging.info(f"Archiwizacja na GDrive: {vid.name}")
                    if upload_to_gdrive(vid, config.gdrive_folder_id):
                        vid.unlink() # Usuń po sukcesie backupu
                        logging.info(f"Zarchiwizowano i usunięto: {vid.name}")
                else:
                    vid.unlink() # Po prostu usuń
                    logging.info(f"Usunięto stary plik (retencja): {vid.name}")

def retention_settings_ui():
    with SessionLocal() as db:
        config = db.query(SystemConfig).first()
        if not config:
            config = SystemConfig()
            db.add(config)
            db.commit()

    with ui.card().classes('bg-zinc-900 border border-zinc-800 p-6 w-full'):
        ui.label('POLITYKA RETENCJI I BACKUPU G-DRIVE').classes('text-orange-500 font-bold')
        
        with ui.row().classes('w-full items-center gap-4'):
            # Wybór trybu
            mode = ui.select(
                {'DELETE': 'Tylko usuwaj', 'BACKUP': 'Backupuj na GDrive, potem usuń'}, 
                value=config.retention_policy,
                on_change=lambda e: update_config('retention_policy', e.value)
            ).classes('flex-grow').props('dark filled')
            
            # Dni retencji
            days = ui.number('Dni przechowywania', value=config.retention_days, 
                             on_change=lambda e: update_config('retention_days', e.value)) \
                .classes('w-32').props('dark filled')

        ui.input('Google Drive Folder ID', value=config.gdrive_folder_id,
                 on_change=lambda e: update_config('gdrive_folder_id', e.value)) \
            .classes('w-full mt-2').props('dark filled')

    def update_config(field, val):
        with SessionLocal() as db:
            cfg = db.query(SystemConfig).first()
            setattr(cfg, field, val)
            db.commit()
            ui.notify('Ustawienia retencji zapisane')