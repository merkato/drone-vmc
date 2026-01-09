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

# --- GŁÓWNY INTERFEJS ---

def streams_management_interface(username, role):
    """
    Kompletny moduł zarządzania dronami:
    - Dodawanie/Edycja strumieni
    - Zarządzanie dostępem (Piloci i Widzowie)
    - Sterowanie nagrywaniem (REC) przez API
    - Generowanie linków RTMP
    """

    # --- FUNKCJE POMOCNICZE (DATABASE) ---

    def get_current_users_map():
        """Pobiera mapę ID: Username do menu wyboru."""
        with SessionLocal() as db:
            return {u.id: u.username for u in db.query(User).all()}

    def get_streams_list():
        """Pobiera listę strumieni widocznych dla danego użytkownika."""
        with SessionLocal() as db:
            if role == 'admin':
                streams = db.query(StreamPath).all()
            else:
                # Widzimy tylko swoje lub te, do których mamy uprawnienia
                user = db.query(User).filter(User.username == username).first()
                owned = db.query(StreamPath).filter(StreamPath.owner_username == username).all()
                assigned = user.allowed_streams if user else []
                streams = list({s.path_name: s for s in (owned + assigned)}.values())
            
            return [{
                'path': s.path_name, 
                'desc': s.description, 
                'owner': s.owner_username,
                'rec': s.is_recording_enabled
            } for s in streams]

    # --- AKCJE ---

    async def handle_save_stream(p_val, d_val, pilots_ids, viewers_ids):
        """Zapisuje nowy strumień i relacje w bazie."""
        path = p_val.value.strip().lower()
        if not path:
            ui.notify('Podaj ID strumienia (np. istebna/dron1)', color='negative')
            return

        with SessionLocal() as db:
            # 1. Sprawdź czy już istnieje lub stwórz nowy
            stream = db.query(StreamPath).filter(StreamPath.path_name == path).first()
            if not stream:
                stream = StreamPath(path_name=path, owner_username=username)
                db.add(stream)
            
            stream.description = d_val.value
            
            # 2. Aktualizacja relacji (Many-to-Many)
            if pilots_ids.value:
                stream.authorized_publishers = db.query(User).filter(User.id.in_(pilots_ids.value)).all()
            if viewers_ids.value:
                stream.authorized_viewers = db.query(User).filter(User.id.in_(viewers_ids.value)).all()
            
            db.commit()
            ui.notify(f'Zapisano strumień: {path}', color='positive')
            
            # 3. Odśwież tabelę i pokaż linki
            stream_table.rows = get_streams_list()
            await show_rtmp_dialog(path)

    async def toggle_recording(stream_name: str, status: bool):
        """Zmienia status nagrywania w MediaMTX."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.patch(f"{MEDIAMTX_API}/config/paths/patch/{stream_name}", 
                                       json={"record": status})
            
                if response.status_code == 200:
                    # LOGUJEMY sukces w systemie
                    logging.info(f"Zmieniono status nagrywania dla {stream_name} na: {status}")
                    # POWIADAMIAMY operatora na ekranie
                    ui.notify(f"Strumień {stream_name}: Nagrywanie {'aktywne' if status else 'zatrzymane'}", type='positive')
                else:
                    logging.error(f"Błąd API MediaMTX ({response.status_code}) dla strumienia {stream_name}")
                    ui.notify("Błąd serwera wideo!", type='negative')

        except Exception as e:
            # To jest krytyczne - jeśli MediaMTX padnie, musimy mieć to w logach!
            logging.exception(f"Krytyczny błąd komunikacji z MediaMTX przy obsłudze {stream_name}")
            ui.notify("Brak połączenia z silnikiem wideo!", type='negative')

    async def show_rtmp_dialog(p_name):
        """Wyświetla okno z linkami RTMP dla pilotów."""
        with SessionLocal() as db:
            s = db.query(StreamPath).filter(StreamPath.path_name == p_name).first()
            if not s or not s.authorized_publishers:
                ui.notify('Brak przypisanych pilotów dla tego strumienia!', color='warning')
                return
            
            with ui.dialog() as dialog, ui.card().classes('w-full max-w-2xl bg-zinc-900 border border-orange-900'):
                ui.label(f'KONFIGURACJA RTMP: {p_name}').classes('text-orange-500 font-bold mb-4')
                for pub in s.authorized_publishers:
                    link = f"rtmp://stream.giswgorach.pl/{p_name}?user={pub.username}&password={pub.password}"
                    with ui.row().classes('w-full items-center bg-black p-2 rounded mb-1'):
                        ui.label(pub.username).classes('text-xs w-20 text-zinc-400')
                        ui.label(link).classes('text-[10px] font-mono flex-grow truncate')
                        ui.button(icon='content_copy', on_click=lambda l=link: ui.run_javascript(f'navigator.clipboard.writeText("{l}")')) \
                            .props('flat dense color=orange')
                ui.button('ZAMKNIJ', on_click=dialog.close).classes('w-full mt-4')
        dialog.open()

    async def delete_stream(p_name):
        """Usuwa strumień (tylko admin lub właściciel)."""
        with SessionLocal() as db:
            s = db.query(StreamPath).filter(StreamPath.path_name == p_name).first()
            if s and (role == 'admin' or s.owner_username == username):
                db.delete(s)
                db.commit()
                ui.notify(f'Usunięto {p_name}', color='positive')
                stream_table.rows = get_streams_list()

    # --- UI LAYOUT ---

    with ui.column().classes('w-full max-w-6xl mx-auto p-4 gap-6 bg-black'):
        
        # FORMULARZ DODAWANIA
        with ui.card().classes('bg-zinc-900 border border-zinc-800 p-6 w-full shadow-2xl'):
            ui.label('KONFIGURACJA NOWEGO DRONA').classes('text-orange-500 font-black mb-4 tracking-tighter')
            
            with ui.row().classes('w-full gap-4 mb-2'):
                s_path = ui.input('Path (ID)', placeholder='np. istebna/mini').classes('flex-1').props('dark filled')
                s_desc = ui.input('Opis misji').classes('flex-1').props('dark filled')

            u_map = get_current_users_map()
            with ui.row().classes('w-full gap-4'):
                p_ids = ui.select(u_map, multiple=True, label='Uprawnieni Piloci (NADAWANIE)').classes('flex-1').props('dark filled')
                v_ids = ui.select(u_map, multiple=True, label='Uprawnieni Widzowie (PODGLĄD)').classes('flex-1').props('dark filled')

            ui.button('ZAPISZ I GENERUJ POŚWIADCZENIA', on_click=lambda: handle_save_stream(s_path, s_desc, p_ids, v_ids)) \
                .classes('w-full mt-4 bg-orange-800 hover:bg-orange-700 font-bold py-4')

        # TABELA
        ui.label('ZARZĄDZANIE STRUMIENIAMI').classes('text-sm font-bold text-zinc-600 mt-6 tracking-widest')
        
        columns = [
            {'name': 'path', 'label': 'PATH', 'field': 'path', 'align': 'left', 'sortable': True},
            {'name': 'desc', 'label': 'OPIS', 'field': 'desc', 'align': 'left'},
            {'name': 'rec', 'label': 'AUTO-REC', 'field': 'rec', 'align': 'center'},
            {'name': 'owner', 'label': 'WŁAŚCICIEL', 'field': 'owner', 'align': 'left'},
            {'name': 'actions', 'label': 'AKCJE', 'field': 'actions', 'align': 'right'},
        ]

        stream_table = ui.table(columns=columns, rows=get_streams_list(), row_key='path') \
            .classes('w-full bg-zinc-950 border border-zinc-900 shadow-xl').props('dark flat border')

        # SLOT: Suwak REC
        stream_table.add_slot('body-cell-rec', '''
            <q-td :props="props">
                <q-toggle 
                    v-model="props.row.rec" 
                    color="red" 
                    keep-color
                    @update:model-value="val => $parent.$emit('toggle_rec', {path: props.row.path, state: val})"
                />
            </q-td>
        ''')

        # SLOT: Akcje
        stream_table.add_slot('body-cell-actions', '''
            <q-td :props="props">
                <q-btn flat round icon="key" color="orange" size="sm" @click="$parent.$emit('show_key', props.row.path)">
                    <q-tooltip>Pokaż linki RTMP</q-tooltip>
                </q-btn>
                <q-btn flat round icon="delete" color="red-9" size="sm" @click="$parent.$emit('delete', props.row.path)">
                    <q-tooltip>Usuń strumień</q-tooltip>
                </q-btn>
            </q-td>
        ''')

        stream_table.on('toggle_rec', lambda e: toggle_recording(e.args))
        stream_table.on('show_key', lambda e: show_rtmp_dialog(e.args))
        stream_table.on('delete', lambda e: delete_stream(e.args))