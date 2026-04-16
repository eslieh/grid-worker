# Grid Worker

This project is an asynchronous file-processing worker built with Celery and Redis. It receives jobs from a PHP client, processes images or image collections, uploads the output to Cloudinary, and then sends the result back to a web API.

## What It Does

The worker currently supports these task types:

- `image.remove_bg`
- `image.resize`
- `images.to_pdf`

The code also lists `image.compress` and `image.convert_format` as allowed task names, but there are no task implementations for them yet in [tasks.py](/Users/app/Development/grid-worker/tasks.py:55).

## Main Components

- [tasks.py](/Users/app/Development/grid-worker/tasks.py:1): Celery app setup, task dispatcher, processing tasks, and result callback task.
- [worker.py](/Users/app/Development/grid-worker/worker.py:1): starts the Celery worker process.
- [logger.py](/Users/app/Development/grid-worker/logger.py:1): writes API callback logs to `api.log`.
- [docker-compose.yml](/Users/app/Development/grid-worker/docker-compose.yml:1): runs the worker container.
- [Dockerfile](/Users/app/Development/grid-worker/Dockerfile:1): builds the Python runtime image.

There is also a PHP producer in a sibling project:

- `../htdocs/ryfty-grid/push_to_queue.php`

That file is responsible for creating Celery-compatible messages and pushing them into Redis.

## How It Works

### 1. A client queues a job

The PHP client builds a Celery protocol v2 message and pushes it onto the Redis queue named `celery`.

Expected logical payload shape:

```json
{
  "task_type": "image.resize",
  "task_id": "uuid-123",
  "payload": {
    "original_url": "https://example.com/image.png",
    "parameters": {
      "width": 800,
      "height": 600,
      "keep_aspect_ratio": true,
      "output_format": "jpg"
    }
  }
}
```

### 2. Celery receives the message

The Python worker listens on Redis through the Celery app defined in [tasks.py](/Users/app/Development/grid-worker/tasks.py:36).

The first task that receives the job is `task_queue` in [tasks.py](/Users/app/Development/grid-worker/tasks.py:56).

Its job is to:

- read `task_type`, `task_id`, and `payload`
- validate that the task type is allowed
- forward the job to the real worker task using `celery_app.send_task(...)`

### 3. The processing task runs

Depending on `task_type`, one of these handlers runs:

- `image.remove_bg` in [tasks.py](/Users/app/Development/grid-worker/tasks.py:87)
- `image.resize` in [tasks.py](/Users/app/Development/grid-worker/tasks.py:155)
- `images.to_pdf` in [tasks.py](/Users/app/Development/grid-worker/tasks.py:245)

Each task:

- parses the payload
- fetches the source file from a remote URL or local path
- processes the file in memory
- writes a temporary output file to `/tmp`
- uploads the output to Cloudinary

### 4. The result is sent back

After upload, the worker builds a result payload and queues `task.send_result` in [tasks.py](/Users/app/Development/grid-worker/tasks.py:391).

That task sends an HTTP `POST` request to `WEB_API_URL` with the final status and file metadata.

## Processing Flows

### `image.remove_bg`

Implemented in [tasks.py](/Users/app/Development/grid-worker/tasks.py:87).

Flow:

- loads the input image
- removes the background using `rembg`
- converts output to RGBA
- saves the processed image to a temp file
- uploads the processed image to Cloudinary
- posts the output URL and metadata back to the web API

Main libraries used:

- `rembg`
- `Pillow`
- `cloudinary`

### `image.resize`

Implemented in [tasks.py](/Users/app/Development/grid-worker/tasks.py:155).

Flow:

- loads the input image
- reads `width`, `height`, `keep_aspect_ratio`, and `output_format`
- resizes the image with Pillow
- converts to RGB when JPEG output is required
- saves to a temp file
- uploads to Cloudinary
- posts the output URL and metadata back to the web API

Main libraries used:

- `Pillow`
- `cloudinary`

### `images.to_pdf`

Implemented in [tasks.py](/Users/app/Development/grid-worker/tasks.py:245).

Flow:

- expects `payload.original_url` to be a list of image URLs
- downloads each image
- scales each image to fit an A4 page
- creates a PDF with one image per page using ReportLab
- uploads the PDF to Cloudinary
- verifies the returned URL
- posts the output URL and metadata back to the web API

Main libraries used:

- `Pillow`
- `reportlab`
- `cloudinary`

## Architecture

```text
PHP Client / Web App
    |
    | push Celery-formatted message to Redis
    v
Redis queue (`celery`)
    |
    v
Celery worker
    |
    v
`task_queue` dispatcher
    |
    +--> `image.remove_bg`
    +--> `image.resize`
    +--> `images.to_pdf`
    |
    v
Cloudinary
    |
    v
`task.send_result`
    |
    v
Web API callback endpoint
```

## Environment Variables

The worker expects these environment variables:

- `REDIS_CONNECTION_STRING`: Redis broker connection string.
- `CLOUDINARY_CLOUD_NAME`: Cloudinary cloud name.
- `CLOUDINARY_API_KEY`: Cloudinary API key.
- `CLOUDINARY_API_SECRET`: Cloudinary API secret.
- `WEB_API_URL`: callback endpoint that receives task results.

Create a `.env` file locally with those values before running the worker.

## Running Locally

### Install dependencies

```bash
pip install -r requirements.txt
```

### Start the worker directly

```bash
python worker.py
```

You can also start Celery with:

```bash
celery -A worker.celery_app worker --loglevel=info -P solo
```

### Start with Docker Compose

```bash
docker-compose up --build
```

## Result Payload Shape

Successful tasks send a payload similar to this:

```json
{
  "task_id": "uuid-123",
  "status": "done",
  "result": {
    "output_url": "https://res.cloudinary.com/...",
    "metadata": {
      "output_format": "png"
    }
  },
  "error": null
}
```

## Notes About The Current Implementation

- The worker is structured as a single-module service today. Most logic lives in `tasks.py`.
- Temporary files are written to `/tmp`.
- Retry behavior is handled through Celery task retries with `max_retries=3`.
- `logger.py` logs callback activity to `api.log`.
- The Docker image currently starts `gunicorn` for `app:app`, but this repository does not contain an `app.py` entrypoint. The active runtime path appears to be the Celery worker started from Docker Compose or `worker.py`.
- `task_queue` allows `image.compress` and `image.convert_format`, but those task handlers are not implemented yet.

## Suggested Next Cleanup

If this service keeps growing, a good next step would be splitting `tasks.py` into:

- `celery_app.py`
- `tasks/dispatcher.py`
- `tasks/image.py`
- `tasks/pdf.py`
- `tasks/callbacks.py`

That would make it easier to add new task types and test them independently.
