import os

from ckeditor.views import create_thumbnail, get_image_files, \
        get_thumb_filename
from django.core.management.base import NoArgsCommand
from django.core.files.storage import default_storage


class Command(NoArgsCommand):
    """
    Creates thumbnail files for the CKEditor file image browser.
    Useful if starting to use django-ckeditor with existing images.
    """
    def handle_noargs(self, **options):
        for image in get_image_files():
            if not default_storage.exists(get_thumb_filename(image)):
                print "Creating thumbnail for %s" % image
                try:
                    create_thumbnail(image)
                except Exception, e:
                    print "Couldn't create thumbnail for %s: %s" % (image, e)
        print "Finished"
