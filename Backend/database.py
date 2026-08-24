import os
import urllib.parse
import logging
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import Session
from fastapi import HTTPException, Request

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

# === Your existing credentials (unchanged) ===
db_user1 = os.getenv("MYSQL_USER")
# print("Database User:", db_user1)  # Debugging line to check if the user is loaded correctly
db_pass1 = urllib.parse.quote_plus(os.getenv("MYSQL_PASSWORD") or "")
# print("Database Password:", db_pass1)  # Debugging line to check if the password is loaded correctly
db_host1 = os.getenv("MYSQL_HOST")
# print("Database Host:", db_host1)  # Debugging line to check if the host is loaded correctly
db_name1 = os.getenv("MYSQL_DATABASE")
# print("Database Name:", db_name1)  # Debugging line to check if the database name is loaded correctly

if not all([db_user1, db_pass1, db_host1, db_name1]):
    raise ValueError("One or more required database environment variables are not set. Please check MYSQL_USER, MYSQL_PASSWORD, MYSQL_HOST, and MYSQL_DATABASE in your .env file.")


# db_user2 = "agentsupport_trigma_in"
# db_pass2 = urllib.parse.quote_plus("A#@34bguf3454DE#@")
# db_host2 = "13.204.24.129"
# db_name2 = "agentsupport_trigma_in"

# === Your existing URLs (unchanged) ===
DB1_URL_WITHOUT_NAME = f"mysql+mysqldb://{db_user1}:{db_pass1}@{db_host1}"
DB1_URL = f"{DB1_URL_WITHOUT_NAME}/{db_name1}"

# DB2_URL_WITHOUT_NAME = f"mysql+mysqldb://{db_user2}:{db_pass2}@{db_host2}"
# DB2_URL = f"{DB2_URL_WITHOUT_NAME}/{db_name2}"

# === IMPROVED Engine Creation with Better Connection Pooling ===
engine_admin = create_engine(
    DB1_URL, 
    pool_size=50,           # Increase from default (usually 5)
    max_overflow=30,        # Allow 30 additional connections when needed
    pool_pre_ping=True,     # Test connections before use
    pool_recycle=3600,      # Recycle connections after 1 hour
    pool_timeout=30,        # Timeout when waiting for connections
    echo=False              # Set to True for debugging
)

# === IMPROVED Session Makers ===
SessionLocalAdmin = sessionmaker(autocommit=False, autoflush=False, bind=engine_admin)

# === Your existing database creation (unchanged) ===
def create_db_if_not_exists(engine_url: str, db_name: str):
    temp_engine = create_engine(engine_url, pool_pre_ping=True)
    with temp_engine.connect() as conn:
        result = conn.execute(text(f"SHOW DATABASES LIKE '{db_name}'"))
        if result.fetchone() is None:
            conn.execute(text(f"CREATE DATABASE {db_name}"))
            print(f"✅ Created database: {db_name}")
            
            print(f"Database connection established successfully")
        else:
            print(f"✅ Database already exists: {db_name}")
            print(f"Database connection established successfully")

create_db_if_not_exists(DB1_URL_WITHOUT_NAME, db_name1)
# create_db_if_not_exists(DB2_URL_WITHOUT_NAME, db_name2)

# === Your existing functions (unchanged) ===
def get_dynamic_db(email: str) -> Session:
    if not email:
        raise HTTPException(status_code=400, detail="Missing email for DB selection")

    db = SessionLocalAdmin() 
    return db

async def get_db_from_headers(request: Request):
    email = request.headers.get("user-email","admin@gmail.com")
    if not email:
        raise HTTPException(status_code=400, detail="Missing user-email in headers")
    
    db = get_dynamic_db(email)
    try:
        yield db
    finally:
        db.close()

async def get_db_from_query(request: Request):
    email = request.query_params.get("user-email", "admin@ogmail.com")  
    if not email:
        raise HTTPException(status_code=400, detail="Missing user-email in query params")
    
    db = get_dynamic_db(email)
    try:
        yield db
    finally:
        db.close()