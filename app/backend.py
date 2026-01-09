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
from config import RECORDINGS_DIR
# --- KONFIGURACJA ---

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
# --- DANE IN-MEMORY (Znikają po restarcie kontenera) ---
stats_history = defaultdict(list)
last_bytes = {}
alert_logs = []

def init_system_config():
    """Tworzy domyślne ustawienia systemu, jeśli tabela jest pusta."""
    with SessionLocal() as db:
        config = db.query(SystemConfig).first()
        if not config:
            logging.info(">>> [BOOTSTRAP] Inicjalizacja domyślnej konfiguracji systemu...")
            new_config = SystemConfig(
                retention_days=30,
                retention_policy="DELETE",  # Domyślnie tylko usuwamy
                gdrive_folder_id=""
            )
            db.add(new_config)
            db.commit()
            logging.info(">>> [BOOTSTRAP] Domyślna konfiguracja stworzona: 30 dni, tryb DELETE.")

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
    """Pobiera dane i od razu je formatuje do strażackich standardów."""
    try:
        total, used, free = shutil.disk_usage(RECORDINGS_DIR)
        return {
            # Zaokrąglamy do 1 miejsca po przecinku, żeby nie straszyć liczbami
            'cpu': round(psutil.cpu_percent(), 1),
            'ram': round(psutil.virtual_memory().percent, 1),
            'disk_pct': round((used / total) * 100, 1),
            'disk_free': free // (2**30),
            'disk_total': total // (2**30)
        }
    except Exception:
        return {'cpu': 0, 'ram': 0, 'disk_pct': 0, 'disk_free': 0, 'disk_total': 0}

@ui.refreshable
def system_info_ui():
    """Wielki, czytelny panel stanu serwera - widoczny z daleka."""
    res = get_sys_resources()
    
    # Dynamiczne kolory (wyraźne kontrasty)
    cpu_color = 'red' if res['cpu'] > 80 else ('orange' if res['cpu'] > 50 else 'green-5')
    ram_color = 'red' if res['ram'] > 90 else ('blue-6' if res['ram'] > 60 else 'cyan-5')

    # Główny kontener - teraz wyższy i z wyraźniejszym obramowaniem
    with ui.row().classes('w-full gap-8 items-center justify-between p-6 bg-zinc-900 rounded-2xl border-2 border-zinc-800 shadow-2xl'):
        
        # CPU - Wielkie pokrętło
        with ui.column().classes('items-center'):
            ui.label('CPU').classes('text-sm font-black text-zinc-400 tracking-tighter mb-1')
            # Zwiększamy rozmiar do 120px i grubość linii
            ui.knob(res['cpu'], min=0, max=100, show_value=True, color=cpu_color).props(
                'size=120px font-size=24px track-color=zinc-800 center-color=zinc-950 thickness=0.2'
            ).classes('text-bold')
            
        # RAM - Wielkie pokrętło
        with ui.column().classes('items-center'):
            ui.label('RAM').classes('text-sm font-black text-zinc-400 tracking-tighter mb-1')
            ui.knob(res['ram'], min=0, max=100, show_value=True, color=ram_color).props(
                'size=120px font-size=24px track-color=zinc-800 center-color=zinc-950 thickness=0.2'
            ).classes('text-bold')
            
        # DYSK - Solidny pasek postępu
        with ui.column().classes('items-start flex-grow px-6'):
            ui.label('MIEJSCE NA NAGRANIA (DYSK)').classes('text-sm font-black text-zinc-400 mb-2')
            
            # Większy procent zajętości
            with ui.row().classes('w-full justify-between items-center mb-2'):
                ui.label(f"{res['disk_pct']}%").classes('text-3xl font-black text-white')
                ui.label('ZAJĘTE').classes('text-xs text-zinc-600')
            
            # Grubszy pasek (h-5)
            progress_color = 'red' if res['disk_pct'] > 90 else 'blue-7'
            ui.linear_progress(value=res['disk_pct'] / 100, color=progress_color).classes('w-full h-5 rounded-lg shadow-inner')
            
            # Czytelne info o GB
            with ui.row().classes('w-full justify-between mt-3 bg-zinc-950 p-2 rounded-md'):
                with ui.column().classes('gap-0'):
                    ui.label('WOLNE').classes('text-[10px] text-zinc-500')
                    ui.label(f"{res['disk_free']} GB").classes('text-xl font-bold text-green-400')
                with ui.column().classes('gap-0 items-end'):
                    ui.label('POJEMNOŚĆ').classes('text-[10px] text-zinc-500')
                    ui.label(f"{res['disk_total']} GB").classes('text-lg font-bold text-zinc-300')

    # Odświeżanie co 2 sekundy, żeby "tętniło życiem"
    ui.timer(2.0, system_info_ui.refresh)

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

# backend.py
async def run_retention_task():
    logging.info("--- DEBUG START: run_retention_task ---")
    try:
        with SessionLocal() as db:
            logging.info("Krok 1: Sesja otwarta, pobieram rekord...")
            config_db = db.query(SystemConfig).first()
            
            if not config_db:
                logging.warning("Krok 2: Brak rekordu konfiguracji!")
                return

            logging.info(f"Krok 3: Rekord pobrany (ID: {id(config_db)})")
            
            # WYCIĄGAMY DANE (zrzucamy je do zwykłych zmiennych)
            p_days = int(config_db.retention_days)
            p_policy = str(config_db.retention_policy)
            p_folder = str(config_db.gdrive_folder_id) if config_db.gdrive_folder_id else None
            
            logging.info(f"Krok 4: Dane skopiowane do RAM: dni={p_days}, policy={p_policy}")

        logging.info("Krok 5: Sesja zamknięta poprawnie.")
        
        # Pętla już poza sesją
        now = time.time()
        retention_secs = p_days * 24 * 3600
        
        for vid in RECORDINGS_DIR.rglob("*.mp4"):
            # Tu pracujemy TYLKO na p_policy i p_days
            pass 

    except Exception as e:
        logging.error(f"!!! CRASH W RETENCJI !!! Typ: {type(e)}, Błąd: {e}", exc_info=True)
    
    logging.info("--- DEBUG KONIEC: run_retention_task ---")

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