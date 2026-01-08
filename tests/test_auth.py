import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.app import app, Base, User, StreamPath, SessionLocal

# --- KONFIGURACJA TESTOWEJ BAZY ---
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL)
TestingSessionLocal = sessionmaker(bind=engine)

@pytest.fixture
def client():
    # Tworzymy tabele w pamięci przed każdym testem
    Base.metadata.create_all(bind=engine)
    
    # Podmieniamy sesję bazy danych w aplikacji na testową
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    # Tutaj symulujemy klienta FastAPI
    with TestClient(app) as c:
        yield c
    
    # Czyścimy bazę po teście
    Base.metadata.drop_all(bind=engine)

# --- KONKRETNE TESTY ---

def test_user_creation(client):
    """Testuje, czy możemy dodać użytkownika do bazy"""
    db = TestingSessionLocal()
    new_user = User(username="pilot1", password="secret_password", role="publisher")
    db.add(new_user)
    db.commit()
    
    user_in_db = db.query(User).filter(User.username == "pilot1").first()
    assert user_in_db is not None
    assert user_in_db.role == "publisher"
    db.close()

def test_auth_endpoint_success(client):
    """Testuje, czy endpoint /auth poprawnie wpuszcza drona z dobrymi danymi w URL"""
    # 1. Przygotowanie danych
    db = TestingSessionLocal()
    user = User(username="dron01", password="key123", role="publisher")
    path = StreamPath(path_name="akcja-las")
    user.allowed_streams.append(path)
    db.add(user)
    db.add(path)
    db.commit()

    # 2. Symulacja zapytania od MediaMTX
    auth_data = {
        "user": "dron01",
        "password": "key123",
        "path": "akcja-las",
        "action": "publish"
    }
    
    response = client.post("/auth", json=auth_data)
    
    # 3. Weryfikacja
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    db.close()

def test_auth_endpoint_wrong_password(client):
    """Testuje, czy system odrzuci drona z błędnym hasłem"""
    db = TestingSessionLocal()
    db.add(User(username="dron01", password="key123", role="publisher"))
    db.commit()

    auth_data = {
        "user": "dron01",
        "password": "ZLE_HASLO",
        "path": "test",
        "action": "publish"
    }
    
    response = client.post("/auth", json=auth_data)
    assert response.status_code == 401
    db.close()

def test_unauthorized_path_access(client):
    """Testuje relację wiele-do-wielu: czy publisher może nadawać na nieprzypisaną ścieżkę"""
    db = TestingSessionLocal()
    user = User(username="pilotA", password="123", role="publisher")
    path_other = StreamPath(path_name="strefa-zamknieta")
    db.add(user)
    db.add(path_other)
    db.commit()

    # PilotA nie ma przypisanej ścieżki 'strefa-zamknieta'
    auth_data = {
        "user": "pilotA",
        "password": "123",
        "path": "strefa-zamknieta",
        "action": "publish"
    }
    
    response = client.post("/auth", json=auth_data)
    assert response.status_code == 403 # Forbidden
    db.close()