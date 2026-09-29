"""Cloudinary image storage — unsigned upload, no API secret needed."""
import io

import cloudinary
import cloudinary.uploader

from . import config

cloudinary.config(cloud_name=config.CLOUDINARY_CLOUD_NAME, secure=True)


def upload_image_bytes(image_bytes: bytes, folder: str) -> tuple[str, str]:
    """Upload raw image bytes, return (secure_url, public_id)."""
    result = cloudinary.uploader.unsigned_upload(
        io.BytesIO(image_bytes),
        upload_preset=config.CLOUDINARY_UPLOAD_PRESET,
        folder=folder,
    )
    return result["secure_url"], result["public_id"]
