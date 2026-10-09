from datetime import date
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from billing.models import Patient, Practice, ServiceTemplate, Invoice, InvoiceItem


class Command(BaseCommand):
    help = 'Synthetische Beispieldaten für eine leere Installation anlegen (keine Anmeldung).'

    @transaction.atomic
    def handle(self, *args, **options):
        if Patient.objects.exists() or Invoice.objects.exists():
            raise CommandError('Demo nur in einer leeren Installation möglich.')
        Practice.objects.update_or_create(pk=1, defaults={'name':'Praxis Muster', 'street':'Musterweg 1',
            'city':'89155 Erbach', 'iban':'DE00 0000 0000 0000 0000 00', 'phone':'01234 567890', 'bank':'Musterbank'})
        anna = Patient.objects.create(title='Frau',first_name='Anna',last_name='Musterfrau',street='Musterstraße 12',city='89155 Erbach',birth_date=date(1985,4,12),phone='01234 12345')
        Patient.objects.create(title='Herr',first_name='Max',last_name='Mustermann',street='Testweg 3',city='89073 Ulm',birth_date=date(1974,8,3))
        ServiceTemplate.objects.create(description='Manuelle Therapie (30 min)',code='A.1',price='45.00')
        ServiceTemplate.objects.create(description='Hausbesuch',code='A.2',price='24.99')
        ServiceTemplate.objects.create(description='Krankengymnastik (30 min)',code='A.3',price='38.00')
        invoice = Invoice.objects.create(patient=anna,diagnosis='Synthetische Beispieldiagnose')
        InvoiceItem.objects.create(invoice=invoice,description='Manuelle Therapie (30 min)',code='A.1',price='45.00')
        self.stdout.write(self.style.SUCCESS('Synthetische Beispieldaten angelegt.'))
