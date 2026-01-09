import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Table, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

# 1. Konfiguracja ścieżki bazy danych (bezpieczna dla Dockera)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

# 2. Inicjalizacja silnika SQLAlchemy
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False}, # Wymagane dla SQLite i NiceGUI
    pool_pre_ping=True
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- TABELE ŁĄCZĄCE (Many-to-Many) ---

# Tabela wiążąca Pilotów ze Strumieniami (kto może nadawać)
publishers_table = Table(
    'publishers', Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id'), primary_key=True),
    Column('stream_id', Integer, ForeignKey('streams.id'), primary_key=True)
)

# Tabela wiążąca Widzów ze Strumieniami (kto może oglądać)
viewers_table = Table(
    'viewers', Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id'), primary_key=True),
    Column('stream_id', Integer, ForeignKey('streams.id'), primary_key=True)
)

# --- MODELE GŁÓWNE ---

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)
    role = Column(String, default='viewer') # admin, pilot, viewer
    
    # Relacje
    allowed_streams = relationship(
        "StreamPath", 
        secondary=viewers_table, 
        back_populates="authorized_viewers"
    )
    publishing_streams = relationship(
        "StreamPath", 
        secondary=publishers_table, 
        back_populates="authorized_publishers"
    )

class StreamPath(Base):
    __tablename__ = 'streams'
    id = Column(Integer, primary_key=True, index=True)
    path_name = Column(String, unique=True, index=True, nullable=False) # np. 'istebna/matrice'
    description = Column(String)
    owner_username = Column(String)
    is_recording_enabled = Column(Boolean, default=False)
    
    # Relacje zwrotne
    authorized_viewers = relationship(
        "User", 
        secondary=viewers_table, 
        back_populates="allowed_streams"
    )
    authorized_publishers = relationship(
        "User", 
        secondary=publishers_table, 
        back_populates="publishing_streams"
    )

class SystemConfig(Base):
    __tablename__ = 'system_config'
    id = Column(Integer, primary_key=True)
    retention_policy = Column(String, default="DELETE") # DELETE lub BACKUP
    retention_days = Column(Integer, default=30)
    gdrive_folder_id = Column(String, nullable=True)

# Funkcja pomocnicza do tworzenia tabel (wywoływana w app.py)
def init_db():
    Base.metadata.create_all(bind=engine)