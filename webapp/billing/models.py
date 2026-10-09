from decimal import Decimal, ROUND_HALF_UP
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from uuid import uuid4


def logo_upload_path(instance, filename):
    # Keep old logo versions: finalized invoices reference the version they used.
    return f'logos/{uuid4().hex}.png'


class Patient(models.Model):
    legacy_id = models.CharField(max_length=50, unique=True, null=True, blank=True)
    title = models.CharField('Anrede', max_length=40, blank=True)
    first_name = models.CharField('Vorname', max_length=100)
    last_name = models.CharField('Nachname', max_length=100)
    street = models.CharField('Straße und Hausnummer', max_length=200)
    city = models.CharField('Postleitzahl und Ort', max_length=200)
    birth_date = models.DateField('Geburtsdatum', null=True, blank=True)
    phone = models.CharField('Telefon', max_length=100, blank=True)
    active = models.BooleanField('Aktiv', default=True)

    class Meta:
        ordering = ['last_name', 'first_name', 'pk']

    def __str__(self):
        return f'{self.first_name} {self.last_name}'

    def snapshot(self):
        return {k: getattr(self, k) for k in ['title', 'first_name', 'last_name', 'street', 'city']} | {
            'birth_date': self.birth_date.isoformat() if self.birth_date else ''}


class ServiceTemplate(models.Model):
    code = models.CharField('Leistungsziffer', max_length=40, blank=True)
    description = models.CharField('Leistung', max_length=500)
    price = models.DecimalField('Preis (€)', max_digits=9, decimal_places=2,
                                validators=[MinValueValidator(Decimal('0.01'))])
    active = models.BooleanField('Aktiv', default=True)

    class Meta:
        ordering = ['description', 'pk']

    def __str__(self):
        return f'{self.description} · {self.price:.2f} €'


class Practice(models.Model):
    name = models.CharField('Name', max_length=150, default='Stefanie Kalkbrenner')
    profession = models.CharField('Berufsbezeichnung', max_length=150, default='Heilpraktikerin für Physiotherapie')
    street = models.CharField('Straße', max_length=200, blank=True)
    city = models.CharField('Postleitzahl und Ort', max_length=200, blank=True)
    phone = models.CharField('Telefon', max_length=100, blank=True)
    bank = models.CharField('Bank', max_length=100, blank=True)
    iban = models.CharField('IBAN', max_length=34, blank=True)
    bic = models.CharField('BIC', max_length=11, blank=True)
    tax_text = models.CharField('Steuerhinweis', max_length=300, default='Steuerfreie Leistung nach § 4 Nr. 14 UStG')
    payment_days = models.PositiveIntegerField('Zahlungsfrist (Tage)', default=14, validators=[MinValueValidator(1)])
    logo = models.FileField('Firmenlogo', upload_to=logo_upload_path, blank=True)
    developer_greetings = models.BooleanField('Grüße vom Entwickler', default=False)
    color_scheme = models.CharField('Farbschema', max_length=12, default='green', choices=[('green', 'Grün (Standard)'), ('blue', 'Blau'), ('purple', 'Violett'), ('rose', 'Rosé'), ('dark', 'Dunkel')])


class NumberSequence(models.Model):
    year = models.PositiveIntegerField(unique=True)
    next_number = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])


class Invoice(models.Model):
    TYPES = [('self', 'Selbstzahler'), ('private', 'Privat / GebüH')]
    PAYMENTS = [('transfer', 'Überweisung'), ('cash', 'Barzahlung')]
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name='invoices')
    date = models.DateField('Rechnungsdatum', default=timezone.localdate)
    diagnosis = models.TextField('Diagnose', blank=True, max_length=3000)
    invoice_type = models.CharField('Rechnungsform', choices=TYPES, max_length=10, default='self')
    payment_method = models.CharField('Zahlungsart', choices=PAYMENTS, max_length=10, default='transfer')
    cash_date = models.DateField('Barzahlungsdatum', null=True, blank=True)
    beihilfe = models.BooleanField('Beihilfe-Zweitschrift erstellen', default=False)
    number = models.CharField(max_length=30, unique=True, null=True, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateField(null=True, blank=True)
    patient_snapshot = models.JSONField(default=dict, blank=True)
    practice_snapshot = models.JSONField(default=dict, blank=True)
    pdf_path = models.CharField(max_length=200, blank=True)
    copy_path = models.CharField(max_length=200, blank=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)
    copy_sha256 = models.CharField(max_length=64, blank=True)
    revision = models.PositiveIntegerField(default=0)
    cancellation_of = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='cancellation')
    correction_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='corrections')
    cancellation_reason = models.TextField('Stornogrund', blank=True, max_length=1000)
    settlement = models.CharField('Rückzahlung / Verrechnung', max_length=12, blank=True, choices=[('pending', 'Noch offen'), ('refund', 'Rückzahlung erledigt'), ('offset', 'Verrechnung vereinbart')])
    settlement_note = models.CharField('Notiz zur Rückzahlung / Verrechnung', max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def total(self):
        value = sum((item.total for item in self.items.all()), Decimal('0.00'))
        return -value if self.cancellation_of_id else value

    @property
    def status(self):
        if self.cancellation_of_id:
            return 'Stornorechnung'
        if hasattr(self, 'cancellation'):
            return 'Storniert'
        return 'Bezahlt' if self.paid_at else ('Offen' if self.number else 'Entwurf')

    @property
    def recipient(self):
        if self.number:
            return f"{self.patient_snapshot['first_name']} {self.patient_snapshot['last_name']}"
        return str(self.patient)


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='items')
    date = models.DateField('Leistungsdatum', default=timezone.localdate)
    code = models.CharField('Ziffer', max_length=40, blank=True)
    description = models.CharField('Leistungsbeschreibung', max_length=500)
    quantity = models.DecimalField('Anzahl', max_digits=6, decimal_places=2, default=1,
                                  validators=[MinValueValidator(Decimal('0.01'))])
    price = models.DecimalField('Einzelpreis (€)', max_digits=9, decimal_places=2,
                                validators=[MinValueValidator(Decimal('0.01'))])

    class Meta:
        ordering = ['pk']

    @property
    def total(self):
        return (self.quantity * self.price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(default=timezone.now)
