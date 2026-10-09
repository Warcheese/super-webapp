from django.contrib.auth import views as auth
from django.urls import include, path
from billing.views import SuperLoginView

urlpatterns = [path('anmelden/', SuperLoginView.as_view(), name='login'),
               path('abmelden/', auth.LogoutView.as_view(), name='logout'),
               path('', include('billing.urls'))]
