import posixpath
from urllib.parse import quote, urlsplit

from django.conf import settings


def image_source_urls(image_field, external_url=''):
    """Return ordered image URLs: stored key, legacy bucket-root key, external URL."""
    urls = []

    def add(url):
        if url and url not in urls:
            urls.append(url)

    if image_field:
        name = getattr(image_field, 'name', '') or ''
        try:
            add(image_field.url)
        except (AttributeError, ValueError, OSError):
            pass

        public_base = getattr(settings, 'SUPABASE_STORAGE_PUBLIC_BASE_URL', '').rstrip('/')
        basename = posixpath.basename(name.replace('\\', '/'))
        if public_base and basename:
            add(f'{public_base}/{quote(basename, safe="")}')

    if external_url:
        parsed = urlsplit(str(external_url).strip())
        if parsed.scheme in ('http', 'https') and parsed.netloc:
            add(str(external_url).strip())

    return urls
