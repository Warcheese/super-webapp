from django.urls import path
from . import views as v

urlpatterns = [
    path('', v.dashboard, name='dashboard'),
    path('patienten/', v.patients, name='patients'),
    path('patienten/neu/', v.patient_edit, name='patient_new'),
    path('patienten/<int:pk>/', v.patient_detail, name='patient_detail'),
    path('patienten/<int:pk>/bearbeiten/', v.patient_edit, name='patient_edit'),
    path('rechnungen/', v.invoices, name='invoices'),
    path('rechnungen/neu/', v.invoice_edit, name='invoice_new'),
    path('rechnungen/<int:pk>/', v.invoice_detail, name='invoice_detail'),
    path('rechnungen/<int:pk>/bearbeiten/', v.invoice_edit, name='invoice_edit'),
    path('rechnungen/<int:pk>/erstellen/', v.invoice_finalize, name='invoice_finalize'),
    path('rechnungen/<int:pk>/bezahlt/', v.invoice_paid, name='invoice_paid'),
    path('rechnungen/<int:pk>/pdf/', v.invoice_pdf, name='invoice_pdf'),
    path('vorlagen/', v.templates, name='templates'),
    path('vorlagen/neu/', v.template_edit, name='template_new'),
    path('vorlagen/<int:pk>/', v.template_edit, name='template_edit'),
    path('einstellungen/', v.practice_settings, name='settings'),
    path('einstellungen/nummer/', v.sequence_update, name='sequence_update'),
    path('import/<str:kind>/', v.csv_import, name='csv_import'),
    path('rechnungen/<int:pk>/import/', v.csv_import, {'kind': 'items'}, name='items_import'),
]
