
def text(value):
    return (value or '').casefold()


def sort_records(records, request, columns):
    key = request.GET.get('sort', '')
    direction = 'desc' if request.GET.get('direction') == 'desc' else 'asc'
    if key in columns:
        records = sorted(records, key=lambda record: (columns[key](record), record.pk), reverse=direction == 'desc')
    else:
        key = ''
    return records, {'sort_key': key, 'sort_direction': direction, 'sort_headers': True,
                     'list_query': request.GET.urlencode()}
