import os
import json
from celery import Celery
import requests
from dotenv import load_dotenv

load_dotenv()
REDIS_URL = os.getenv("REDIS_CONNECTION_STRING", "redis://localhost:6379/0")

celery_app = Celery('worker', broker=f"{REDIS_URL}/0", backend=f"{REDIS_URL}/0")

# -----------------------------
# Celery Tasks
# -----------------------------
@celery_app.task(bind=True, max_retries=3, default_retry_delay=3, name="task_queue")
def task_queue(self, data):
    try:
       
       print("Processing data:", data)
       
    except requests.RequestException as exc:
        raise self.retry(exc=exc)