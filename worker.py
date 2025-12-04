from tasks import celery_app

if __name__ == "__main__":
    # Start the Celery worker to listen to Redis
    celery_app.worker_main([
        "worker",
        "--loglevel=info",
        "--queues=default"  # default queue
    ])
    # celery -A worker.celery_app worker --loglevel=info -P solo # for single-threaded execution