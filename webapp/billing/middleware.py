class PrivatePagesMiddleware:
    """Keep patient pages out of intermediary and browser caches."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not request.path.startswith('/static/'):
            response['Cache-Control'] = 'no-store, private'
            response['Referrer-Policy'] = 'same-origin'
        return response
