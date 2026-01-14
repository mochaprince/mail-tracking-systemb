from fastapi_utils.tasks import repeat_every
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException,  WebSocket
from fastapi.middleware.cors import CORSMiddleware
from app.database import SessionLocal, engine, Base
from app import models, utils
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from pydantic import BaseModel
from typing import Optional
import io
import asyncio
from pydantic import BaseModel, Extra
from fastapi.responses import JSONResponse
from enum import Enum
from typing import Optional
import logging

# Pydantic models
class MailCreate(BaseModel):
    name: str
    sender: str
    document: str
    recipient: str
    date_sent: str
    status: str = "pending"
    mail_type: str

class MailCreateWithReply(MailCreate):
    reply_to_eksu_ref: Optional[str] = None
    class Config:
        extra = Extra.allow

class MailStatusUpdate(BaseModel):
    status: str

# Create tables
Base.metadata.create_all(bind=engine)

# FastAPI app
app = FastAPI(title="EKSU Mail Tracking System")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DB Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -----------------------------------------------
#  UPLOAD EXCEL / CSV
# -----------------------------------------------
@app.post("/upload")
async def upload_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        if not file.filename.endswith((".xlsx", ".csv")):
            raise HTTPException(status_code=400, detail="Upload .xlsx or .csv only")

        content = await file.read()
        rows = utils.parse_excel_to_rows(io.BytesIO(content))
        utils.simple_match_and_upsert(rows, db)

        return {"count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

# -----------------------------------------------
#  LIST MAILS
# -----------------------------------------------
def safe_mail_status(status_value):
    if status_value is None:
        return models.MailStatus.pending
    try:
        if isinstance(status_value, Enum):
            return models.MailStatus(status_value.value.lower())
        return models.MailStatus(str(status_value).lower())
    except:
        return models.MailStatus.pending

@app.get("/mails")
def list_mails(
    skip: int = 0,
    limit: int = 200,
    mail_type: Optional[models.MailTypeEnum] = None,
    status: Optional[models.MailStatus] = None,
    db: Session = Depends(get_db)
):
    try:
        query = db.query(models.Mail)

        if mail_type:
            query = query.filter(models.Mail.mail_type == mail_type)

        if status:
            query = query.filter(models.Mail.status == status)

        mails = (
            query
            .order_by(models.Mail.date_sent.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

        return [{
            "id": m.id,
            "name": m.name,
            "sender": m.sender,
            "document": m.document,
            "recipient": m.recipient,
            "date_sent": m.date_sent.isoformat() if m.date_sent else None,
            "status": m.status.value if m.status else "pending",
            "eksu_ref": m.eksu_ref,
            "custom_threshold_hours": m.custom_threshold_hours,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None,
            "notified": m.notified,
            "notified_at": m.notified_at.isoformat() if m.notified_at else None,
            "reminder_sent_at": m.reminder_sent_at.isoformat() if m.reminder_sent_at else None,
            "matched_to_id": m.matched_to_id,
            "mail_type": m.mail_type.value if m.mail_type else None,
        } for m in mails]

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Error: {str(e)}"}
        )

# -----------------------------------------------
#  NOTIFICATIONS
# -----------------------------------------------
@app.get("/notifications")
def get_notifications(db: Session = Depends(get_db)):
    return db.query(models.Mail).filter(
        models.Mail.notified == True,
        models.Mail.status == models.MailStatus.pending
    ).order_by(models.Mail.date_sent.desc()).all()

# -----------------------------------------------
# UPDATE THRESHOLD
# -----------------------------------------------
@app.put("/mails/{mail_id}/duration")
def update_duration(mail_id: int, hours: int, db: Session = Depends(get_db)):
    mail = db.query(models.Mail).filter(models.Mail.id == mail_id).first()
    if not mail:
        raise HTTPException(status_code=404, detail="Mail not found")

    mail.custom_threshold_hours = hours
    mail.updated_at = datetime.utcnow()
    db.commit()

    return {"message": f"Custom threshold updated to {hours} hours"}

# -----------------------------------------------
# OVERDUE SUMMARY
# -----------------------------------------------
@app.get("/overdue-summary")
def get_overdue_summary(db: Session = Depends(get_db)):
    from sqlalchemy import text
    incoming = db.execute(text("""
        SELECT COUNT(*) FROM mails
        WHERE sender IS NOT NULL AND status='pending'
        AND TIMESTAMPDIFF(HOUR, date_sent, NOW()) > 24
    """)).fetchone()[0]

    outgoing = db.execute(text("""
        SELECT COUNT(*) FROM mails
        WHERE recipient IS NOT NULL AND status='pending'
        AND TIMESTAMPDIFF(HOUR, date_sent, NOW()) > 48
    """)).fetchone()[0]

    return {"incoming": incoming, "outgoing": outgoing}

# -----------------------------------------------
# CREATE MAIL
# -----------------------------------------------
@app.post("/mails")
def create_mail(mail_data: MailCreateWithReply, db: Session = Depends(get_db)):
    try:
        date_sent = datetime.fromisoformat(mail_data.date_sent.replace("Z", "+00:00"))
        eksu_ref = utils.generate_eksu_ref(db)

        mail_type_enum = models.MailTypeEnum[mail_data.mail_type]

        new_mail = models.Mail(
            name=mail_data.name,
            sender=mail_data.sender,
            document=mail_data.document,
            recipient=mail_data.recipient,
            date_sent=date_sent,
            status=models.MailStatus(mail_data.status.lower()),
            eksu_ref=eksu_ref,
            mail_type=mail_type_enum
        )

        if mail_data.reply_to_eksu_ref:
            ref = db.query(models.Mail).filter(models.Mail.eksu_ref == mail_data.reply_to_eksu_ref).first()
            if ref:
                ref.status = models.MailStatus.completed
                new_mail.matched_to_id = ref.id

        db.add(new_mail)
        db.commit()
        db.refresh(new_mail)

        return new_mail

    except Exception as e:
        logging.error(f"Mail creation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# -----------------------------------------------
# UPDATE STATUS
# -----------------------------------------------
@app.put("/mails/{mail_id}/status")
def update_mail_status(mail_id: int, body: MailStatusUpdate, db: Session = Depends(get_db)):
    mail = db.query(models.Mail).filter(models.Mail.id == mail_id).first()
    if not mail:
        raise HTTPException(status_code=404, detail="Mail not found")

    mail.status = models.MailStatus(body.status.lower())
    mail.updated_at = datetime.utcnow()
    db.commit()

    return {"message": "Status updated"}

# -----------------------------------------------
# BACKGROUND STARTUP CHECKER (EVERY 1 HOUR)
# -----------------------------------------------
@app.on_event("startup")
@repeat_every(seconds=600)  # every 10 minutes
def mark_overdue_mails():
    db = SessionLocal()
    try:
        now = datetime.utcnow()

        mails = db.query(models.Mail).filter(
            models.Mail.status == models.MailStatus.pending
        ).all()

        for mail in mails:
            threshold = mail.custom_threshold_hours or 48

            if (now - mail.date_sent) >= timedelta(hours=threshold):
                mail.status = models.MailStatus.overdue
                mail.notified = True
                mail.notified_at = now
                db.add(mail)

        db.commit()
    finally:
        db.close()



# ======================================================
# 🔥 ADDED: FUNCTION REQUIRED BY WEBSOCKET
# ======================================================
def get_new_alerts_from_db():
    db = SessionLocal()
    try:
        now = datetime.utcnow()

        first_interval = timedelta(minutes=90)
        repeat_interval = timedelta(hours=24)

        mails = db.query(models.Mail).filter(
            models.Mail.status == models.MailStatus.overdue
        ).all()

        incoming_refs = []
        outgoing_refs = []

        for mail in mails:
            # ⛔ completed mails NEVER notify
            if mail.status == models.MailStatus.completed:
                continue

            # ⏱ first alert: immediately when overdue
            if mail.reminder_sent_at is None:
                # already overdue → notify every 1h30m
                if (now - mail.notified_at) < first_interval:
                    continue
            else:
                # reminder marked → notify every 24h
                if (now - mail.reminder_sent_at) < repeat_interval:
                    continue

            # categorize
            if mail.mail_type == models.MailTypeEnum.Incoming:
                incoming_refs.append(mail.eksu_ref)
            else:
                outgoing_refs.append(mail.eksu_ref)

            # update reminder timestamp
            mail.reminder_sent_at = now
            db.add(mail)

        db.commit()

        alerts = []

        if incoming_refs:
            alerts.append({
                "type": "incoming",
                "message": f"Incoming mails ({', '.join(incoming_refs)}) not replied within required time.",
                "count": len(incoming_refs),
                "time": now.isoformat()
            })

        if outgoing_refs:
            alerts.append({
                "type": "outgoing",
                "message": f"Outgoing mails ({', '.join(outgoing_refs)}) has not received a response.",
                "count": len(outgoing_refs),
                "time": now.isoformat()
            })

        return alerts

    finally:
        db.close()



# ======================================================
#  WEBSOCKET — REALTIME ALERTS
# ======================================================
@app.websocket("/ws")
async def alerts_ws(websocket: WebSocket):
    await websocket.accept()
    while True:
        alerts = get_new_alerts_from_db()
        if alerts:
            for alert in alerts:
                await websocket.send_json(alert)
        await asyncio.sleep(10)

@app.delete("/mails/clear-all")
def clear_all_mails(db: Session = Depends(get_db)):
    db.query(models.Mail).delete()
    db.commit()
    return {"message": "All mails deleted successfully"}
