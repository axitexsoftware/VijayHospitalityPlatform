"""Validated image storage for homepage, gallery, and category media."""
import io
import os
import secrets
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP'}


class MediaError(ValueError):
    pass


def save_upload(upload, upload_dir):
    if not upload or not getattr(upload, 'filename', ''):
        raise MediaError('Choose an image file.')
    raw = upload.stream.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise MediaError('Each image must be 8 MB or smaller.')
    if not raw:
        raise MediaError('The selected image is empty.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            check = Image.open(io.BytesIO(raw))
            if check.format not in ALLOWED_FORMATS:
                raise MediaError('Use a JPEG, PNG, or WebP image.')
            check.verify()
            image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB')
    except MediaError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise MediaError('That file is not a valid image.') from exc
    os.makedirs(upload_dir, exist_ok=True)
    token = secrets.token_hex(16)
    main_name = token + '.jpg'
    thumb_name = token + '-480.jpg'
    main_path = os.path.join(upload_dir, main_name)
    thumb_path = os.path.join(upload_dir, thumb_name)
    try:
        image.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
        image.save(main_path, 'JPEG', quality=84, optimize=True)
        thumb = image.copy()
        thumb.thumbnail((480, 480), Image.Resampling.LANCZOS)
        thumb.save(thumb_path, 'JPEG', quality=80, optimize=True)
    except OSError:
        for path in (main_path, thumb_path):
            if os.path.exists(path):
                os.remove(path)
        raise
    relative = 'uploads/' + main_name
    return {'path': relative, 'thumb_path': 'uploads/' + thumb_name}


def save_panorama_upload(upload, upload_dir):
    if not upload or not getattr(upload, 'filename', ''):
        raise MediaError('Choose an equirectangular panorama image.')
    raw = upload.stream.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise MediaError('Panorama image must be 8 MB or smaller.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            check = Image.open(io.BytesIO(raw))
            if check.format not in ALLOWED_FORMATS:
                raise MediaError('Use a JPEG, PNG or WebP panorama.')
            check.verify()
            image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
            width, height = image.size
    except MediaError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise MediaError('That file is not a valid image.') from exc
    if height < 256 or width < 512 or abs(width / height - 2) > 0.1:
        raise MediaError('Use a 2:1 equirectangular panorama (at least 512 × 256 pixels).')
    return save_upload(FileStorage(stream=io.BytesIO(raw), filename=upload.filename), upload_dir)


def delete_upload(path, upload_dir):
    name = os.path.basename(path or '')
    if not name or name != path.replace('\\', '/').split('/')[-1]:
        return
    stem, ext = os.path.splitext(name)
    if ext.lower() != '.jpg':
        return
    for filename in (name, stem + '-480.jpg'):
        full = os.path.abspath(os.path.join(upload_dir, filename))
        root = os.path.abspath(upload_dir)
        if os.path.commonpath((root, full)) == root and os.path.isfile(full):
            os.remove(full)
