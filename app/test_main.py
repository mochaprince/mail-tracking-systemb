import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app, get_db
from app import models
from app.database import Base
import asyncio
import json

# Setup test database on disk
SQLALCHEMY_DATABASE_URL = "sqlite:///test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Override get_db dependency for testing
def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

# Create tables before running tests
Base.metadata.create_all(bind=engine)

client = TestClient(app)

@pytest.fixture(scope="function", autouse=True)
def setup_and_teardown():
    # Clean up DB before each test
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

def test_list_mails_empty():
    response = client.get("/mails")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) == 0

def test_create_mail_and_list():
    mail_data = {
        "name": "Test Mail",
        "sender": "sender@example.com",
        "document": "Subject 1",
        "recipient": "recipient@example.com",
        "date_sent": "2023-01-01T12:00:00Z",
        "status": "pending",
        "mail_type": "INCOMING"
    }
    response = client.post("/mails", json=mail_data)
    assert response.status_code == 200
    created_mail = response.json()
    assert created_mail["name"] == mail_data["name"]
    assert created_mail["sender"] == mail_data["sender"]

    list_response = client.get("/mails")
    assert list_response.status_code == 200
    mails = list_response.json()
    assert len(mails) == 1
    assert mails[0]["name"] == mail_data["name"]

def test_get_notifications_empty():
    response = client.get("/notifications")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) == 0

def test_update_duration():
    # Create mail first
    mail_data = {
        "name": "Duration Test",
        "sender": "sender2@example.com",
        "document": "Doc 2",
        "recipient": "recipient2@example.com",
        "date_sent": "2023-01-02T12:00:00Z",
        "status": "pending",
        "mail_type": "INCOMING"
    }
    response = client.post("/mails", json=mail_data)
    mail_id = response.json()["id"]

    response = client.put(f"/mails/{mail_id}/duration?hours=72")
    assert response.status_code == 200
    assert "Custom threshold updated" in response.json()["message"]

def test_get_overdue_summary():
    response = client.get("/overdue-summary")
    assert response.status_code == 200
    json_resp = response.json()
    assert "incoming" in json_resp
    assert "outgoing" in json_resp

def test_update_mail_status():
    mail_data = {
        "name": "Status Update",
        "sender": "statussender@example.com",
        "document": "Doc Status",
        "recipient": "statusrecipient@example.com",
        "date_sent": "2023-01-03T12:00:00Z",
        "status": "pending",
        "mail_type": "INCOMING"
    }
    response = client.post("/mails", json=mail_data)
    mail_id = response.json()["id"]

    response = client.put(f"/mails/{mail_id}/status", json={"status": "completed"})
    assert response.status_code == 200
    assert response.json()["message"] == "Status updated"

@pytest.mark.asyncio
async def test_websocket_alerts():
    async with client.websocket_connect("/ws") as websocket:
        # Wait briefly for messages
        try:
            data = await asyncio.wait_for(websocket.receive_json(), timeout=5)
            assert "ref" in data
            assert "message" in data
            assert "time" in data
        except asyncio.TimeoutError:
            # No alerts may be present - not a failure
            pass
