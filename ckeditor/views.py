import mimetypes
import os
import re
import StringIO
from urlparse import urlparse, urlunparse

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.http import HttpResponse
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.utils.importlib import import_module

try:
    from PIL import Image, ImageOps
except ImportError:
    import Image
    import ImageOps

try:
    from django.views.decorators.csrf import csrf_exempt
except ImportError:
    # monkey patch this with a dummy decorator which just returns the
    # same function (for compatability with pre-1.1 Djangos)
    def csrf_exempt(fn):
        return fn

THUMBNAIL_SIZE = (75, 75)

import logging
logger = logging.getLogger(__name__)


def get_user_folder_function():
    """
    Imports and returns user folder defining function from settings.py or a default function.
    """
    userdefined_function = getattr(settings, 'CKEDITOR_GET_USERDIR', None)
    if not userdefined_function:
        return lambda u: u.id
    module, func_name = userdefined_function.rsplit('.', 1)
    try:
        module = import_module(module)
        return getattr(module, func_name)
    except (AttributeError, ImportError) as e:
        raise ValueError("Could not find CKEDITOR_GET_USER_DIR function: {0}\nError: {1}".format(userdefined_function, e))


get_user_folder = get_user_folder_function()

def get_available_name(name):
    """
    Returns a filename that's free on the target storage system, and
    available for new content to be written to.
    """
    dir_name, file_name = os.path.split(name)
    file_root, file_ext = os.path.splitext(file_name)
    # If the filename already exists, keep adding an underscore (before the
    # file extension, if one exists) to the filename until the generated
    # filename doesn't exist.
    while default_storage.exists(name):
        file_root += '_'
        # file_ext includes the dot.
        name = os.path.join(dir_name, file_root + file_ext)
    return name


def get_thumb_filename(file_name):
    """
    Generate thumb filename by adding _thumb to end of
    filename before . (if present)
    """
    return '%s_thumb%s' % os.path.splitext(file_name)

def get_image_format(extension):
    mimetypes.init()
    return mimetypes.types_map[extension]

def create_thumbnail(filename):
    thumbnail_filename = get_thumb_filename(filename)
    thumbnail_format = get_image_format(os.path.splitext(filename)[1])
    pil_format = thumbnail_format.split('/')[1]

    image = default_storage.open(filename)
    image = Image.open(image)

    # Convert to RGB if necessary
    # Thanks to Limodou on DjangoSnippets.org
    # http://www.djangosnippets.org/snippets/20/
    if image.mode not in ('L', 'RGB'):
        image = image.convert('RGB')

    # scale and crop to thumbnail
    imagefit = ImageOps.fit(image, THUMBNAIL_SIZE, Image.ANTIALIAS)
    thumbnail_io = StringIO.StringIO()
    imagefit.save(thumbnail_io, format=pil_format)

    thumbnail = InMemoryUploadedFile(thumbnail_io, None, thumbnail_filename, thumbnail_format,
                                  thumbnail_io.len, None)
    thumbnail.seek(0)

    return default_storage.save(thumbnail_filename, thumbnail)


def get_media_url(path):
    """
    Determine system file's media URL. Don't trust filesystem storage.
    """
    if not settings.DEFAULT_FILE_STORAGE == 'django.core.files.storage.FileSystemStorage':
        return default_storage.url(path)
    upload_prefix = getattr(settings, "CKEDITOR_UPLOAD_PREFIX", None)
    if upload_prefix:
        url = upload_prefix + path.replace(settings.CKEDITOR_UPLOAD_PATH, '')
    else:
        url = settings.MEDIA_URL + path.replace(settings.MEDIA_ROOT, '')
    # Remove multiple forward-slashes from the path portion of the url.
    # Break url into a list.
    url_parts = list(urlparse(url))
    # Replace two or more slashes with a single slash.
    url_parts[2] = re.sub('\/+', '/', url_parts[2])
    # Reconstruct the url.
    url = urlunparse(url_parts)
    return url


def get_upload_filename(upload_name, user):
    user_path = get_user_folder(user)
    upload_path = os.path.join(settings.CKEDITOR_UPLOAD_PATH, user_path)
    return get_available_name(os.path.join(upload_path, upload_name))


@csrf_exempt
def upload(request):
    """
    Uploads a file and send back its URL to CKEditor.

    TODO:
        Validate uploads
    """
    # Get the uploaded file from request.
    upload = request.FILES['upload']

    # Open output file in which to store upload.
    upload_filename = get_upload_filename(upload.name, request.user)

    image = default_storage.save(upload_filename, upload)

    create_thumbnail(image)

    # Respond with Javascript sending ckeditor upload url.
    url = get_media_url(image)
    return HttpResponse("""
    <script type='text/javascript'>
        window.parent.CKEDITOR.tools.callFunction(%s, '%s');
    </script>""" % (request.GET['CKEditorFuncNum'], url))


def get_image_files(user=None, path=''):
    """
    Recursively walks all dirs under upload dir and generates a list of
    full paths for each file found.
    """
    # If a user is provided and CKEDITOR_RESTRICT_BY_USER is True,
    # limit images to user specific path, but not for superusers.
    STORAGE_DIRECTORIES = 0
    STORAGE_FILES = 1

    user_path = get_user_folder(user) if user else ''

    browse_path = os.path.join(settings.CKEDITOR_UPLOAD_PATH, user_path, path)

    try:
        storage_list = default_storage.listdir(browse_path)
    except NotImplementedError:
        return
    except OSError:
        return

    for filename in storage_list[STORAGE_FILES]:
        if os.path.splitext(filename)[0].endswith('_thumb'):
            continue
        filename = os.path.join(browse_path, filename)
        yield filename

    for directory in storage_list[STORAGE_DIRECTORIES]:
        directory_path = os.path.join(path, directory)
        for element in get_image_files(user, directory_path):
            yield element


def get_image_browse_urls(user=None, order_dates=0, order_titles=0):
    """user
    Recursively walks all dirs under upload dir and generates a list of
    thumbnail and full image URL's for each file found.
    """
    def _get_key(image):
        if order_titles:
            return image['title']
        return default_storage.modified_time(image['src'])

    images = []
    for filename in get_image_files(user=user):
        images.append({
            'src': filename,
            'title': os.path.basename(filename),
        })
    if order_dates or order_titles:
        images.sort(key=_get_key, reverse=(order_dates < 0 or order_titles < 0))
    for image in images:
        image['thumb'] = get_media_url(get_thumb_filename(image['src']))
        image['src'] = get_media_url(image['src'])
    return images


def browse(request):
    order_dates = order_titles = 0
    date_order = request.GET.get('date_order')
    title_order = request.GET.get('title_order')
    if date_order == 'ascending':
        order_dates = 1
    elif date_order == 'descending':
        order_dates = -1
    if title_order == 'ascending':
        order_titles = 1
    elif title_order == 'descending':
        order_titles = -1
    logger.info((date_order, title_order))
    context = RequestContext(request, {
        'CKEDITOR_MEDIA_PREFIX': settings.CKEDITOR_MEDIA_PREFIX,
        'images': get_image_browse_urls(request.user, order_dates, order_titles),
        'date_order': date_order or 'descending',
        'title_order': title_order or 'descending',
    })
    return render_to_response('browse.html', context)
