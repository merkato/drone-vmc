from nicegui import ui, app
from models import SessionLocal, User
import sqlalchemy

def user_management_interface():
    """
    Kompletny moduł zarządzania personelem:
    - Dodawanie nowych użytkowników
    - Lista pracowników (Tabela)
    - Edycja uprawnień i haseł
    - Usuwanie kont
    """

    # --- FUNKCJE LOGIKI (DATABASE) ---

    def get_users_from_db():
        """Pobiera aktualną listę użytkowników do tabeli."""
        with SessionLocal() as db:
            users = db.query(User).all()
            return [
                {'id': u.id, 'username': u.username, 'role': u.role, 'password': u.password} 
                for u in users
            ]

    async def add_new_user(username_input, password_input, role_input):
        """Dodaje użytkownika do bazy danych."""
        u_val = username_input.value.strip()
        p_val = password_input.value.strip()
        r_val = role_input.value

        if not u_val or not p_val:
            ui.notify('BŁĄD: Nazwa użytkownika i hasło są wymagane!', color='negative', icon='warning')
            return

        try:
            with SessionLocal() as db:
                # Sprawdzenie czy użytkownik już istnieje
                existing = db.query(User).filter(User.username == u_val).first()
                if existing:
                    ui.notify(f'Użytkownik {u_val} już istnieje!', color='negative')
                    return

                new_user = User(username=u_val, password=p_val, role=r_val)
                db.add(new_user)
                db.commit()
                
            ui.notify(f'Dodano użytkownika: {u_val}', color='positive', icon='person_add')
            # Czyszczenie pól
            username_input.value = ''
            password_input.value = ''
            # Odświeżenie tabeli
            user_table.rows = get_users_from_db()
        except Exception as e:
            ui.notify(f'Błąd bazy danych: {e}', color='negative')

    async def edit_user_dialog(user_data):
        """Otwiera okno edycji użytkownika."""
        with ui.dialog() as dialog, ui.card().classes('w-96 bg-zinc-900 border border-zinc-800'):
            ui.label(f'EDYCJA: {user_data["username"]}').classes('text-orange-500 font-bold mb-4')
            
            edit_pass = ui.input('Nowe Hasło', value=user_data['password']).props('dark filled password-toggle').classes('w-full mb-2')
            edit_role = ui.select(['admin', 'pilot', 'viewer'], label='Rola systemowa', value=user_data['role']).props('dark filled').classes('w-full')
            
            with ui.row().classes('w-full justify-end mt-4'):
                ui.button('ANULUJ', on_click=dialog.close).props('flat color=white')
                ui.button('ZAPISZ', on_click=lambda: dialog.submit({
                    'password': edit_pass.value,
                    'role': edit_role.value
                })).props('color=orange')

        result = await dialog
        if result:
            with SessionLocal() as db:
                db_user = db.query(User).filter(User.id == user_data['id']).first()
                if db_user:
                    db_user.password = result['password']
                    db_user.role = result['role']
                    db.commit()
                    ui.notify(f'Zaktualizowano: {db_user.username}', color='positive')
                    user_table.rows = get_users_from_db()

    async def delete_user_confirm(user_data):
        """Potwierdzenie i usunięcie użytkownika."""
        with ui.dialog() as dialog, ui.card().classes('bg-zinc-900 border border-red-900 p-6'):
            ui.label(f'Czy na pewno chcesz usunąć użytkownika {user_data["username"]}?').classes('text-white mb-4')
            ui.label('Tej operacji nie da się cofnąć.').classes('text-zinc-500 text-xs mb-4')
            with ui.row().classes('w-full justify-center gap-4'):
                ui.button('USUŃ', on_click=lambda: dialog.submit(True)).props('color=red')
                ui.button('ANULUJ', on_click=lambda: dialog.submit(False)).props('flat color=white')
        
        if await dialog:
            with SessionLocal() as db:
                db_user = db.query(User).filter(User.id == user_data['id']).first()
                if db_user:
                    db.delete(db_user)
                    db.commit()
                    ui.notify(f'Usunięto użytkownika {user_data["username"]}', color='warning')
                    user_table.rows = get_users_from_db()

    # --- INTERFEJS (UI) ---

    with ui.column().classes('w-full max-w-5xl mx-auto p-4 gap-6 bg-black'):
        
        # PANEL DODAWANIA UŻYTKOWNIKA
        with ui.card().classes('bg-zinc-900 border border-zinc-800 p-6 w-full shadow-lg'):
            ui.label('DODAJ NOWEGO UŻYTKOWNIKA').classes('text-orange-500 font-bold mb-4 tracking-widest')
            with ui.row().classes('w-full items-end gap-4'):
                new_username = ui.input('Login / Nazwisko').classes('flex-grow').props('dark filled')
                new_password = ui.input('Hasło dostępu').classes('flex-grow').props('dark filled password-toggle')
                new_role = ui.select(
                    ['admin', 'pilot', 'viewer'], 
                    label='Rola systemowa', 
                    value='viewer'
                ).classes('w-40').props('dark filled')
                
                ui.button(icon='person_add', on_click=lambda: add_new_user(new_username, new_password, new_role)) \
                    .classes('bg-orange-700 hover:bg-orange-600 h-14 w-14 shadow-lg') \
                    .tooltip('Dodaj użytkownika do bazy')

        # TABELA UŻYTKOWNIKÓW
        ui.label('AKTUALNY PERSONEL').classes('text-sm font-bold text-zinc-500 mt-4 tracking-widest uppercase')
        
        columns = [
            {'name': 'username', 'label': 'LOGIN', 'field': 'username', 'align': 'left', 'sortable': True},
            {'name': 'role', 'label': 'ROLA', 'field': 'role', 'align': 'left', 'sortable': True},
            {'name': 'actions', 'label': 'OPERACJE', 'field': 'actions', 'align': 'right'},
        ]

        user_table = ui.table(columns=columns, rows=get_users_from_db(), row_key='id') \
            .classes('w-full bg-zinc-950 border border-zinc-900').props('dark flat border')

        # Slot na przyciski Edytuj/Usuń
        user_table.add_slot('body-cell-actions', '''
            <q-td :props="props">
                <q-btn flat round icon="edit" color="orange" size="sm" @click="$parent.$emit('edit', props.row)">
                    <q-tooltip>Edytuj dane i uprawnienia</q-tooltip>
                </q-btn>
                <q-btn flat round icon="person_remove" color="red-8" size="sm" @click="$parent.$emit('delete', props.row)">
                    <q-tooltip>Usuń użytkownika z systemu</q-tooltip>
                </q-btn>
            </q-td>
        ''')

        # MAPOWANIE ZDARZEŃ TABELI
        user_table.on('edit', lambda e: edit_user_dialog(e.args))
        user_table.on('delete', lambda e: delete_user_confirm(e.args))

        # Stopka informacyjna
        with ui.row().classes('w-full justify-between items-center mt-4 p-4 border-t border-zinc-900'):
            ui.label('System VMS Istebna | 2026').classes('text-[10px] text-zinc-700')
            ui.button('ODŚWIEŻ LISTĘ', icon='refresh', on_click=lambda: setattr(user_table, 'rows', get_users_from_db())) \
                .props('flat dense color=zinc-600 text-color=zinc-600') \
                .classes('text-[10px]')

def create_default_user():
    """Tworzy domyślnego administratora, jeśli baza jest pusta."""
    with SessionLocal() as db:
        # Sprawdzamy, czy w systemie jest jakikolwiek admin
        admin = db.query(User).filter(User.role == "admin").first()
        if not admin:
            new_admin = User(
                username="admin", 
                password="123", # Zmień po pierwszym zalogowaniu!
                role="admin"
            )
            db.add(new_admin)
            db.commit()
            print(">>> [BOOTSTRAP] STWORZONO DOMYŚLNEGO ADMINA: admin / 123")

def is_authenticated() -> bool:
    """
    Kompleksowy strażnik sesji. 
    Sprawdza czy użytkownik jest zalogowany i czy sesja zawiera wymagane dane.
    """
    return all([
        app.storage.user.get('authenticated', False),
        app.storage.user.get('username') is not None,
        app.storage.user.get('role') is not None,
        app.storage.user.get('user_id') is not None
    ])

def change_my_password_ui(username):
    """Mały panel do zmiany hasła przez zalogowanego użytkownika."""
    with ui.card().classes('bg-zinc-900 border border-zinc-800 p-6 w-full shadow-lg'):
        ui.label('ZMIANA MOJEGO HASŁA').classes('text-blue-400 font-bold mb-4')
        with ui.row().classes('w-full items-end gap-4'):
            new_pass = ui.input('Nowe hasło').props('dark filled password-toggle').classes('flex-grow')
            
            async def update_pass():
                if not new_pass.value:
                    return ui.notify('Wpisz nowe hasło!', color='warning')
                with SessionLocal() as db:
                    user = db.query(User).filter(User.username == username).first()
                    if user:
                        user.password = new_pass.value
                        db.commit()
                        ui.notify('Hasło zostało zmienione', color='positive')
                        new_pass.value = ''

            ui.button('AKTUALIZUJ', on_click=update_pass).props('color=blue')