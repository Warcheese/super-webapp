from django import template

register = template.Library()


@register.inclusion_tag('billing/sort_header.html', takes_context=True)
def sort_header(context, key, label, numeric=False):
    active = context.get('sort_key') == key
    direction = context.get('sort_direction', 'asc')
    params = context['request'].GET.copy()
    params['sort'] = key
    params['direction'] = 'desc' if active and direction == 'asc' else 'asc'
    return {'label': label, 'numeric': numeric, 'enabled': context.get('sort_headers', False),
            'url': '?' + params.urlencode(), 'active': active, 'direction': direction,
            'aria_sort': ('descending' if direction == 'desc' else 'ascending') if active else 'none'}
