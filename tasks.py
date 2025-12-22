import os
import json
from celery import Celery
from dotenv import load_dotenv
from fpdf import FPDF
import os
import base64
import uuid
import requests
from io import BytesIO
from rembg import remove
from PIL import Image
import cloudinary
import cloudinary.uploader
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from logger import logger
from reportlab.lib.utils import ImageReader
import hashlib
import sys

# Monkey patch for Python 3.8 compatibility
if sys.version_info < (3, 9):
    _md5_new = hashlib.md5
    
    def md5_new(*args, **kwargs):
        kwargs.pop('usedforsecurity', None)
        return _md5_new(*args, **kwargs)
    
    hashlib.md5 = md5_new

load_dotenv()
REDIS_URL = os.getenv("REDIS_CONNECTION_STRING", "redis://localhost:6379/0")


celery_app = Celery('worker', broker=f"{REDIS_URL}/0", backend=f"{REDIS_URL}/0")

CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
WEB_API_URL = os.getenv("WEB_API_URL", "http://localhost:80/api/task/results.php")


# Configure Cloudinary
cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key=os.environ.get("CLOUDINARY_API_KEY"),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET"),
)
# -----------------------------
# Celery Tasks
# -----------------------------
# entry point for task queue

tasks = ['image.remove_bg', 'image.resize', 'image.compress', 'image.convert_format', 'images.to_pdf']
@celery_app.task(bind=True, max_retries=3, default_retry_delay=3, name="task_queue")
def task_queue(self, data):
    try:
       
    #    print("Processing data:", data)
        print("Received data:", data)

        task_type = data.get("task_type")
        task_id = data.get("task_id")
        payload = data.get("payload", {})

        if not task_type or not task_id:
            raise ValueError("Missing task_type or task_id")

        # Check for valid task type
        if task_type not in tasks:
            raise ValueError(f"Invalid task type: {task_type}")

        print(f"Dispatching to worker task: {task_type}")

        # Send the job to the actual worker task
        celery_app.send_task(
            name=task_type,
            kwargs={"payload": payload, "task_id": task_id}
        )
       
    except Exception as exc:
        raise self.retry(exc=exc)



@celery_app.task(bind=True, max_retries=3, default_retry_delay=3, name="image.remove_bg")
def remove_bg(self, task_id, payload):
    try:
        print("Removing background for data:", payload)
        payload = json.loads(payload) if isinstance(payload, str) else payload
        # payload = payload.to_dict() if hasattr(payload, "to_dict") else payload
        # Placeholder for background removal logic
        # Simulate processing
        original_url = payload.get("original_url")
        parameters = payload.get("parameters", {})
        output_format = parameters.get("output_format", "png")
        if not original_url:
            raise ValueError("Missing image_url in payload")
        # Download original image
        if original_url.startswith("http"):
            response = requests.get(original_url)
            img = Image.open(BytesIO(response.content))
        else:
            img = Image.open(original_url)
        # Remove background
        result_img = remove(img).convert("RGBA")

        # Downscale if larger than 2000px (Cloudinary limit support)
        max_size = (2000, 2000)
        result_img.thumbnail(max_size)

        # Compress if output would be too big
        temp_file = f"/tmp/{uuid.uuid4()}.{output_format}"

        # If PNG: convert to JPEG unless user REALLY wants PNG
        if output_format.lower() == "png":
            # PNG is always large. Compress instead:
            result_img.save(temp_file, optimize=True)
        else:
            # For JPG output
            rgb_img = result_img.convert("RGB")
            rgb_img.save(temp_file, quality=80, optimize=True)

        # Save temporarily before upload
        temp_file = f"/tmp/{uuid.uuid4()}.{output_format}"
        result_img.save(temp_file)

        # Upload to Cloudinary
        upload_result = cloudinary.uploader.upload(temp_file, resource_type="image")
        cloud_url = upload_result["secure_url"]
        # Prepare output JSON
        result_json = {
            "task_id": task_id,
            "status": "done",
            "result": {
                "output_url": cloud_url,
                "metadata": {
                    "original_file_name": os.path.basename(original_url),
                    "output_file_name": os.path.basename(temp_file),
                    "output_format": output_format,
                     "processing_time": upload_result.get("created_at")
                }
            },
            "error": None
        }
        print("Background removal result:", result_json)
        print("Sending result to API:", WEB_API_URL)
        send_result.delay(WEB_API_URL, result_json)
        return
        # return result
    except Exception as exc:
        raise self.retry(exc=exc)
    
@celery_app.task(bind=True, max_retries=3, default_retry_delay=3, name="image.resize")
def resize_image(self, task_id, payload):
    try:
        print("Resizing image for data:", payload)
        payload = json.loads(payload) if isinstance(payload, str) else payload
        # payload = payload.to_dict() if hasattr(payload, "to_dict") else payload
        # Placeholder for background removal logic
        # Simulate processing
        original_url = payload.get("original_url")
        params = payload.get("parameters", {})
        if not original_url:
            raise ValueError("Missing image_url in payload")
        # Download original image
        if original_url.startswith("http"):
            response = requests.get(original_url)
            img = Image.open(BytesIO(response.content))
        else:
            img = Image.open(original_url)
        result = {"status": "image resized", "data": payload}

        width = params.get("width")
        height = params.get("height")
        keep_aspect = params.get("keep_aspect_ratio", True)
        output_format = params.get("output_format", "png").upper()

        if not original_url:
            raise ValueError("Missing 'original_url' in payload")

        # Download or open local image
        if original_url.startswith("http"):
            response = requests.get(original_url)
            img = Image.open(BytesIO(response.content))
        else:
            img = Image.open(original_url)

        # Keep aspect ratio
        if keep_aspect:
            img.thumbnail((width, height), Image.LANCZOS)
        else:
            img = img.resize((width, height), Image.LANCZOS)

        # Convert format (JPEG requires RGB)
        if output_format == "JPG" or output_format == "JPEG":
            img = img.convert("RGB")

        # Compress if output would be too big
        temp_file = f"/tmp/{uuid.uuid4()}.{output_format}"

        result_img = img
        # If PNG: convert to JPEG unless user REALLY wants PNG
        if output_format.lower() == "png":
            # PNG is always large. Compress instead:
            result_img.save(temp_file, optimize=True)
        else:
            # For JPG output
            rgb_img = result_img.convert("RGB")
            rgb_img.save(temp_file, quality=80, optimize=True)


        # Save temporarily before upload
        temp_file = f"/tmp/{uuid.uuid4()}.{output_format}"
        result_img.save(temp_file)

        # Upload to Cloudinary
        upload_result = cloudinary.uploader.upload(temp_file, resource_type="image")
        cloud_url = upload_result["secure_url"]
        # Prepare output JSON
        result_json = {
            "task_id": task_id,
            "status": "done",
            "result": {
                "output_url": cloud_url,
                "metadata": {
                    "original_file_name": os.path.basename(original_url),
                    "output_file_name": os.path.basename(temp_file),
                    "output_format": output_format,
                     "processing_time": upload_result.get("created_at")
                }
            },
            "error": None
        }
        send_result.delay(WEB_API_URL, result_json)
        print("Resizing result:", result_json)
        return

    except Exception as exc:
        raise self.retry(exc=exc)
    


@celery_app.task(bind=True, max_retries=3, default_retry_delay=3, name="images.to_pdf")
def to_pdf(self, task_id, payload):
    temp_files = []
    
    try:
        print("Creating PDF for:", payload)

        payload = json.loads(payload) if isinstance(payload, str) else payload
        original_urls = payload.get("original_url")
        params = payload.get("parameters", {})

        if not original_urls or not isinstance(original_urls, list):
            raise ValueError("`original_url` must be a list of one or more image URLs")

        output_file_name = params.get("output_file_name") or f"merged_{uuid.uuid4()}.pdf"
        temp_pdf_path = f"/tmp/{output_file_name}"
        temp_files.append(temp_pdf_path)

        # Create PDF with reportlab
        c = canvas.Canvas(temp_pdf_path, pagesize=A4)
        page_width, page_height = A4  # 595.27 x 841.89 points

        for idx, url in enumerate(original_urls):
            print(f"Processing image {idx + 1}/{len(original_urls)}: {url}")
            
            # Download image
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            # Open image
            img = Image.open(BytesIO(response.content))
            
            # Convert to RGB
            if img.mode in ("RGBA", "P", "LA", "L"):
                img = img.convert("RGB")
            
            # Get dimensions
            img_width, img_height = img.size
            aspect_ratio = img_width / img_height
            
            # Calculate scaling
            if aspect_ratio > (page_width / page_height):
                w = page_width
                h = page_width / aspect_ratio
            else:
                h = page_height
                w = page_height * aspect_ratio
            
            # Center on page
            x = (page_width - w) / 2
            y = (page_height - h) / 2
            
            # Save to temp file for reportlab
            temp_img = f"/tmp/temp_{uuid.uuid4()}.jpg"
            temp_files.append(temp_img)
            img.save(temp_img, "JPEG", quality=95)
            
            # Draw image
            c.drawImage(temp_img, x, y, width=w, height=h)
            c.showPage()
            
            # Cleanup
            os.remove(temp_img)
            temp_files.remove(temp_img)

        c.save()
        print(f"PDF created: {temp_pdf_path} ({os.path.getsize(temp_pdf_path)} bytes)")

        # Try uploading to Cloudinary with different settings
        try:
            upload_result = cloudinary.uploader.upload(
                temp_pdf_path,
                resource_type="auto",  # Changed from "raw"
                public_id=output_file_name.replace(".pdf", ""),
                format="pdf",
                access_mode="public",
                invalidate=True,  # Clear CDN cache
                overwrite=True
            )
            
            cloud_url = upload_result.get("secure_url")
            print("Cloudinary upload result:", upload_result)
            
        except Exception as cloudinary_error:
            print(f"Cloudinary upload error: {cloudinary_error}")
            
            # Fallback: Try uploading as image type
            upload_result = cloudinary.uploader.upload(
                temp_pdf_path,
                resource_type="image",
                public_id=output_file_name.replace(".pdf", ""),
                format="pdf"
            )
            cloud_url = upload_result.get("secure_url")
            print("Cloudinary upload (image type) result:", upload_result)

        # Verify the URL is accessible
        verify_response = requests.head(cloud_url, timeout=10)
        print(f"URL verification status: {verify_response.status_code}")
        
        if verify_response.status_code == 401:
            print("WARNING: Uploaded file returns 401. Cloudinary settings may need adjustment.")
            # Check if there's an unsigned URL available
            if "secure_url" in upload_result:
                # Try the non-secure URL
                insecure_url = upload_result.get("url")
                print(f"Trying insecure URL: {insecure_url}")
                cloud_url = insecure_url

        # Clean up temp PDF
        if os.path.exists(temp_pdf_path):
            os.remove(temp_pdf_path)

        result_json = {
            "task_id": task_id,
            "status": "done",
            "result": {
                "output_url": cloud_url,
                "metadata": {
                    "file_name": output_file_name,
                    "pages": len(original_urls),
                    "processing_time": upload_result.get("created_at"),
                    "file_size": upload_result.get("bytes"),
                    "cloudinary_public_id": upload_result.get("public_id"),
                }
            },
            "error": None
        }

        send_result.delay(WEB_API_URL, result_json)
        return 

    except Exception as exc:
        print(f"Error in PDF task: {type(exc).__name__}: {exc}")
        
        # Clean up temp files
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception as cleanup_error:
                print(f"Failed to clean up {temp_file}: {cleanup_error}")
        
        raise self.retry(exc=exc)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=3, name="task.send_result")
def send_result(self, api_url, payload):
    try:
        print("Sending result to API:", api_url)
        response = requests.post(api_url, json=payload)
        logger.info(
            f"POST {api_url} | IP=%s | Payload=%s",
            response.remote_addr,
            response
        )
        print("API response status:", response.status_code)
        return response.json()
    except Exception as exc:
        raise self.retry(exc=exc)