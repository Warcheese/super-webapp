import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import Patient, ServiceTemplate, InvoiceItem


def read_rows(data, required):
    try:
        reader = csv.DictReader(StringIO(data.decode('utf-8-sig')), delimiter=';')
        if not reader.fieldnames or not set(required).issubset(reader.fieldnames):
            raise ValidationError('CSV-Spalten fehlen: ' + ', '.join(required))
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise ValidationError('Bitte eine gültige UTF-8-CSV mit Semikolon als Trennzeichen wählen.') from exc
    if len(rows) > 5000:
        raise ValidationError('Maximal 5000 Zeilen pro Import.')
    for index, row in enumerate(rows, start=2):
        if None in row or any(row.get(k) is None for k in required):
            raise ValidationError(f'Zeile {index}: Anzahl der Spalten stimmt nicht.')
    return rows


def date_value(value, optional=False):
    if optional and not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), '%d.%m.%Y').date()
    except ValueError as exc:
        raise ValidationError(f'Ungültiges Datum: {value}. Erwartet: TT.MM.JJJJ.') from exc


def price_value(value):
    try:
        price = Decimal(value.strip().replace(',', '.'))
        if not price.is_finite() or price <= 0 or price.as_tuple().exponent < -2:
            raise InvalidOperation
        return price
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f'Ungültiger Preis: {value}. Bitte positive Eurobeträge mit höchstens zwei Nachkommastellen verwenden.') from exc


@transaction.atomic
def import_patients(data):
    rows = read_rows(data, ['ID','Anrede','Vorname','Nachname','Straße','Ort','Geburtsdatum','Telefon'])
    seen = set()
    count = 0
    for row in rows:
        legacy_id = row['ID'].strip()
        if not legacy_id or legacy_id in seen:
            raise ValidationError('Die Patienten-ID muss vorhanden und pro Datei eindeutig sein.')
        seen.add(legacy_id)
        defaults = dict(title=row['Anrede'], first_name=row['Vorname'], last_name=row['Nachname'],
                        street=row['Straße'], city=row['Ort'], phone=row['Telefon'],
                        birth_date=date_value(row['Geburtsdatum'], optional=True))
        existing = Patient.objects.filter(legacy_id=legacy_id).first()
        candidate = existing or Patient(legacy_id=legacy_id)
        for key, value in defaults.items():
            setattr(candidate, key, value)
        candidate.full_clean()
        candidate.save()
        count += 1
    return count


@transaction.atomic
def import_templates(data):
    rows = read_rows(data, ['Ziffer','Leistungsbeschreibung','Summe'])
    for row in rows:
        obj = ServiceTemplate.objects.filter(code=row['Ziffer'], description=row['Leistungsbeschreibung']).first()
        obj = obj or ServiceTemplate(code=row['Ziffer'], description=row['Leistungsbeschreibung'])
        obj.price = price_value(row['Summe'])
        obj.full_clean()
        obj.save()
    return len(rows)


@transaction.atomic
def import_items(invoice, data):
    rows = read_rows(data, ['Datum','Ziffer','Leistungsbeschreibung','Summe'])
    if not rows:
        raise ValidationError('Die Rechnungs-CSV enthält keine Leistungen.')
    if invoice.items.count() + len(rows) > 100:
        raise ValidationError('Maximal 100 Leistungen pro Rechnung.')
    diagnoses = {row.get('Diagnose', '').strip() for row in rows if row.get('Diagnose', '').strip()}
    if len(diagnoses) > 1:
        raise ValidationError('Die CSV enthält unterschiedliche Diagnosen. Bitte vor dem Import prüfen.')
    for row in rows:
        obj = InvoiceItem(invoice=invoice, date=date_value(row['Datum']), code=row['Ziffer'],
                          description=row['Leistungsbeschreibung'], price=price_value(row['Summe']))
        obj.full_clean()
        obj.save()
    if diagnoses:
        diagnosis = diagnoses.pop()
        if invoice.diagnosis and invoice.diagnosis != diagnosis:
            raise ValidationError('Die CSV-Diagnose weicht vom Entwurf ab. Bitte zuerst die Diagnose abgleichen.')
        invoice.diagnosis = diagnosis
    invoice.revision += 1
    invoice.full_clean()
    invoice.save()
    return len(rows)
