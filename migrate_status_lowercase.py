import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import models
from sqlalchemy.exc import SQLAlchemyError
from urllib.parse import urlparse, parse_qs, urlunparse

def get_database_url():
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set.")

    # Parse URL to adjust for mysql+mysqlconnector scheme and SSL settings
    parsed = urlparse(database_url)
    query_params = parse_qs(parsed.query)

    # Remove ssl-mode if present (the backend removes it too)
    if 'ssl-mode' in query_params:
        del query_params['ssl-mode']

    new_query = '&'.join([f"{k}={v[0]}" for k, v in query_params.items()])

    scheme = "mysql+mysqlconnector"

    new_url = urlunparse((
        scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))

    logging.info(f"Using database URL: {new_url}")
    return new_url

def migrate_status_lowercase():
    database_url = get_database_url()

    engine = create_engine(database_url, echo=True)

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    db = SessionLocal()
    try:
        mails = db.query(models.Mail).all()
        updated_count = 0
        for mail in mails:
            if mail.status:
                new_status_str = mail.status.value.lower() if hasattr(mail.status, 'value') else str(mail.status).lower()
                if str(mail.status) != new_status_str:
                    mail.status = models.MailStatus(new_status_str)
                    updated_count += 1
        db.commit()
        print(f"Updated {updated_count} mail status records to lowercase.")
    except SQLAlchemyError as e:
        db.rollback()
        print(f"Error during status migration: {e}")
        logging.error(f"Migration error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    migrate_status_lowercase()
