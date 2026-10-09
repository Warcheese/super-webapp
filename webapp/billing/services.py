import hashlib
import logging
import uuid
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import Invoice, NumberSequence, Practice
from .pdf import render_pdf

logger = logging.getLogger(__name__)


def practice_snapshot(practice):
    return {f.name: getattr(practice, f.name) for f in practice._meta.fields if f.name != 'id'}


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
