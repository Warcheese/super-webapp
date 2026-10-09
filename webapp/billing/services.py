import hashlib
import logging
import uuid
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Invoice, InvoiceItem, NumberSequence, Practice
from .pdf import render_pdf

logger = logging.getLogger(__name__)


def practice_snapshot(practice):
    # UI preferences are unrelated to invoice content.
    fields = ['name', 'profession', 'street', 'city', 'phone', 'bank', 'iban', 'bic', 'tax_text', 'payment_days']
    return {name: getattr(practice, name) for name in fields} | {'logo': practice.logo.name or ''}


def frozen_preview(invoice):
    invoice.patient_snapshot = invoice.patient.snapshot()
    practice, _ = Practice.objects.get_or_create(pk=1)
    invoice.practice_snapshot = practice_snapshot(practice)
    return invoice


def finalize_invoice(pk, revision):
    """One SQLite IMMEDIATE transaction allocates a number and freezes content.

    PDFs are created exclusively under unpredictable names. Failures roll back
    the number and remove newly created files. A crash may leave an orphan PDF,
    but never overwrites an existing invoice. Repeated finalization is idempotent.
    """
    created = []
    try:
        with transaction.atomic():
            invoice = Invoice.objects.select_related('patient').get(pk=pk)
            if invoice.number:
                return invoice
            if invoice.revision != revision:
                raise ValidationError('Der Entwurf wurde inzwischen geändert. Bitte erneut prüfen.')
            if not invoice.items.exists():
                raise ValidationError('Bitte mindestens eine Leistung hinzufügen.')
            invoice.full_clean()
            for item in invoice.items.all():
                item.full_clean()
            frozen_preview(invoice)
            practice = invoice.practice_snapshot
            if not all(practice[k].strip() for k in ['name', 'street', 'city']):
                raise ValidationError('Bitte zuerst Name und Anschrift der Praxis in den Einstellungen ergänzen.')
            if invoice.payment_method != 'cash' and not practice['iban'].strip():
                raise ValidationError('Für Überweisungen bitte zuerst die IBAN in den Einstellungen ergänzen.')
            if invoice.payment_method == 'cash' and not invoice.cash_date:
                raise ValidationError('Das Barzahlungsdatum fehlt.')
            # Use actual issue date; prevent accidentally creating backdated invoices.
            invoice.date = timezone.localdate()
            sequence, _ = NumberSequence.objects.get_or_create(year=invoice.date.year)
            invoice.number = f'{sequence.year}-{sequence.next_number:03d}'
            root = Path(settings.DATA_DIR) / 'pdfs'
            root.mkdir(parents=True, exist_ok=True)
            for duplicate in ([False, True] if invoice.beihilfe else [False]):
                data = render_pdf(invoice, duplicate=duplicate)
                filename = f'{invoice.number}-{uuid.uuid4().hex}{"-zweitschrift" if duplicate else ""}.pdf'
                path = root / filename
                with path.open('xb') as f:
                    created.append(path)
                    f.write(data)
                digest = hashlib.sha256(data).hexdigest()
                if duplicate:
                    invoice.copy_path = f'pdfs/{filename}'
                    invoice.copy_sha256 = digest
                else:
                    invoice.pdf_path = f'pdfs/{filename}'
                    invoice.pdf_sha256 = digest
            invoice.finalized_at = timezone.now()
            if invoice.payment_method == 'cash':
                invoice.paid_at = invoice.cash_date
            invoice.save()
            sequence.next_number += 1
            sequence.save()
            return invoice
    except Exception:
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception('Unreferenzierte PDF konnte nicht entfernt werden')
        raise


def cancel_invoice(pk, *, reason, correct=False, settlement='', settlement_note=''):
    """Atomically save the cancellation PDF, reserve its number and optionally copy a draft."""
    created = []
    try:
        with transaction.atomic():
            original = Invoice.objects.select_related('patient').get(pk=pk)
            if not original.number or original.cancellation_of_id:
                raise ValidationError('Nur fertige Originalrechnungen können storniert werden.')
            if hasattr(original, 'cancellation'):
                raise ValidationError('Diese Rechnung wurde bereits storniert.')
            reason = reason.strip()
            if not reason or len(reason) > 1000:
                raise ValidationError('Bitte einen Stornogrund mit maximal 1000 Zeichen angeben.')
            if settlement not in ['', 'pending', 'refund', 'offset'] or (original.paid_at and not settlement):
                raise ValidationError('Bitte Rückzahlung oder Verrechnung festhalten.')
            cancellation = Invoice.objects.create(patient=original.patient, invoice_type=original.invoice_type,
                payment_method=original.payment_method, cash_date=original.cash_date,
                diagnosis=original.diagnosis, cancellation_of=original, cancellation_reason=reason,
                patient_snapshot=original.patient_snapshot, practice_snapshot=original.practice_snapshot,
                settlement=settlement if original.paid_at else '', settlement_note=settlement_note)
            for item in original.items.all():
                InvoiceItem.objects.create(invoice=cancellation, date=item.date, code=item.code,
                    description=item.description, quantity=item.quantity, price=item.price)
            cancellation.date = timezone.localdate()
            sequence, _ = NumberSequence.objects.get_or_create(year=cancellation.date.year)
            cancellation.number = f'{sequence.year}-{sequence.next_number:03d}'
            cancellation.finalized_at = timezone.now()
            data = render_pdf(cancellation)
            root = Path(settings.DATA_DIR) / 'pdfs'
            root.mkdir(parents=True, exist_ok=True)
            path = root / f'{cancellation.number}-{uuid.uuid4().hex}-storno.pdf'
            with path.open('xb') as stream:
                created.append(path)
                stream.write(data)
            cancellation.pdf_path = f'pdfs/{path.name}'
            cancellation.pdf_sha256 = hashlib.sha256(data).hexdigest()
            cancellation.save()
            sequence.next_number += 1
            sequence.save()
            draft = None
            if correct:
                # A paid original never marks the replacement as paid automatically.
                draft = Invoice.objects.create(patient=original.patient, invoice_type=original.invoice_type,
                    payment_method='transfer', diagnosis=original.diagnosis, beihilfe=original.beihilfe,
                    correction_of=original)
                for item in original.items.all():
                    InvoiceItem.objects.create(invoice=draft, date=item.date, code=item.code,
                        description=item.description, quantity=item.quantity, price=item.price)
            return cancellation, draft
    except Exception:
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception('Unreferenzierte Storno-PDF konnte nicht entfernt werden')
        raise
