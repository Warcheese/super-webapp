from .models import Practice


def appearance(request):
    if not request.user.is_authenticated:
        return {}
    practice = Practice.objects.filter(pk=1).first()
    return {'ui_practice': practice}
