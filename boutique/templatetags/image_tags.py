import json

from django import template

from boutique.image_sources import image_source_urls

register = template.Library()


@register.simple_tag
def image_sources(image_field, external_url=''):
    """Expose the preferred image URL and browser fallback URLs to templates."""
    urls = image_source_urls(image_field, external_url)
    return {
        'src': urls[0] if urls else '',
        'fallbacks': json.dumps(urls[1:]),
        'available': bool(urls),
    }
