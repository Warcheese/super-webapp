import hashlib
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from .models import Patient, Invoice, InvoiceItem, Practice, NumberSequence, ServiceTemplate
from .services import finalize_invoice
from .importers import import_patients, import_templates
from django.core.files.uploadedfile import SimpleUploadedFile


class BillingTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        setting = override_settings(DATA_DIR=Path(self.tmp.name))
        setting.enable()
        self.addCleanup(setting.disable)
        self.user = get_user_model().objects.create_user('steffi', password='test-password-super')
        self.client.force_login(self.user)
        self.patient = Patient.objects.create(first_name='Max', last_name='Müller', street='Musterstraße 1', city='89155 Erbach')
        self.practice = Practice.objects.create(pk=1, name='Praxis Muster', street='Testweg 1', city='89155 Erbach', iban='DE001234')
        self.invoice = Invoice.objects.create(patient=self.patient, diagnosis='Schulter & Nacken <test>', beihilfe=True)
        self.item = InvoiceItem.objects.create(invoice=self.invoice, date=date(2026, 1, 1), description='Massage & Wärme 50%', price='24.99', quantity='2')

    def post_data(self, *, revision=0):
        return {'patient': self.patient.pk, 'date': '2026-01-01', 'invoice_type': 'self', 'payment_method': 'transfer',
                'diagnosis': 'Schulter', 'revision': revision,
                'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '1',
                'items-MIN_NUM_FORMS': '0', 'items-MAX_NUM_FORMS': '100',
                'items-0-id': self.item.pk, 'items-0-date': '2026-01-01', 'items-0-code': '',
                'items-0-description': 'Massage', 'items-0-quantity': '2', 'items-0-price': '24.99'}

    def test_auth_required_for_records_and_pdfs(self):
        self.client.logout()
        for url in [reverse('dashboard'), reverse('patients'), reverse('invoice_detail', args=[self.invoice.pk]), reverse('invoice_pdf', args=[self.invoice.pk])]:
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_main_pages_and_forms_render(self):
        for name, args in [('dashboard', []), ('patients', []), ('patient_new', []), ('patient_edit', [self.patient.pk]),
                           ('patient_detail', [self.patient.pk]), ('invoices', []), ('invoice_new', []),
                           ('invoice_edit', [self.invoice.pk]), ('invoice_detail', [self.invoice.pk]),
                           ('settings', []), ('templates', []), ('template_new', []), ('csv_import', ['patients'])]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_decimal_line_rounding(self):
        self.item.quantity = Decimal('1.5')
        self.item.price = Decimal('0.01')
        self.assertEqual(self.item.total, Decimal('0.02'))

    def test_finalize_freezes_and_is_idempotent(self):
        invoice = finalize_invoice(self.invoice.pk, 0)
        self.assertEqual(invoice.total, Decimal('49.98'))
        self.assertEqual(invoice.date, date.today())
        pdf = Path(self.tmp.name) / invoice.pdf_path
        duplicate = Path(self.tmp.name) / invoice.copy_path
        self.assertTrue(pdf.read_bytes().startswith(b'%PDF'))
        self.assertNotEqual(pdf.read_bytes(), duplicate.read_bytes())
        self.assertEqual(hashlib.sha256(pdf.read_bytes()).hexdigest(), invoice.pdf_sha256)
        number = invoice.number
        self.patient.last_name = 'Geändert'
        self.patient.save()
        self.practice.name = 'Neue Praxis'
        self.practice.save()
        invoice.refresh_from_db()
        self.assertEqual(invoice.recipient, 'Max Müller')
        self.assertEqual(invoice.practice_snapshot['name'], 'Praxis Muster')
        self.assertEqual(finalize_invoice(invoice.pk, 0).number, number)
        self.assertEqual(NumberSequence.objects.get(year=invoice.date.year).next_number, 2)

    def test_pdf_failure_rolls_back_number_and_cleans_files(self):
        from .pdf import render_pdf
        def fail_copy(invoice, **kwargs):
            if kwargs.get('duplicate'):
                raise OSError('Simulated failure')
            return render_pdf(invoice, **kwargs)
        with patch('billing.services.render_pdf', side_effect=fail_copy):
            with self.assertRaises(OSError):
                finalize_invoice(self.invoice.pk, 0)
        self.invoice.refresh_from_db()
        self.assertIsNone(self.invoice.number)
        self.assertFalse(NumberSequence.objects.exists())
        self.assertEqual(list((Path(self.tmp.name)/'pdfs').iterdir()), [])

    def test_stale_revision_cannot_finalize(self):
        self.invoice.revision = 2
        self.invoice.save()
        with self.assertRaises(ValidationError):
            finalize_invoice(self.invoice.pk, 1)

    def test_finalized_edit_and_import_are_blocked(self):
        invoice = finalize_invoice(self.invoice.pk, 0)
        response = self.client.post(reverse('invoice_edit', args=[invoice.pk]), self.post_data())
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.description, 'Massage & Wärme 50%')
        self.assertEqual(self.client.get(reverse('items_import', args=[invoice.pk])).status_code, 302)

    def test_stale_edit_is_rejected(self):
        self.invoice.revision = 1
        self.invoice.save()
        response = self.client.post(reverse('invoice_edit', args=[self.invoice.pk]), self.post_data())
        self.assertContains(response, 'inzwischen geändert')
        self.item.refresh_from_db()
        self.assertEqual(self.item.description, 'Massage & Wärme 50%')

    def test_valid_edit_is_saved(self):
        response = self.client.post(reverse('invoice_edit', args=[self.invoice.pk]), self.post_data())
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.description, 'Massage')
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.revision, 1)

    def test_new_draft_saved_with_items(self):
        data = self.post_data()
        data['items-INITIAL_FORMS'] = '0'
        data.pop('items-0-id')
        response = self.client.post(reverse('invoice_new'), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertEqual(Invoice.objects.latest('pk').items.count(), 1)

    def test_negative_and_nonfinite_price_rejected(self):
        for price in ['-1', 'NaN', 'Infinity']:
            data = self.post_data()
            data['items-0-price'] = price
            response = self.client.post(reverse('invoice_edit', args=[self.invoice.pk]), data)
            self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.price, Decimal('24.99'))

    def test_diagnosis_survives_deleting_first_item(self):
        data = self.post_data()
        data['diagnosis'] = self.invoice.diagnosis
        data['items-0-DELETE'] = 'on'
        self.client.post(reverse('invoice_edit', args=[self.invoice.pk]), data)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.diagnosis, 'Schulter & Nacken <test>')
        self.assertEqual(self.invoice.items.count(), 0)
        with self.assertRaises(ValidationError):
            finalize_invoice(self.invoice.pk, self.invoice.revision)

    def test_download_checks_integrity(self):
        invoice = finalize_invoice(self.invoice.pk, 0)
        url = reverse('invoice_pdf', args=[invoice.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'%PDF'))
        (Path(self.tmp.name)/invoice.pdf_path).write_bytes(b'changed')
        self.assertEqual(self.client.get(url).status_code, 409)

    def test_finalize_requires_post_and_csrf(self):
        url = reverse('invoice_finalize', args=[self.invoice.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(url, {'revision': 0}).status_code, 403)

    def test_sequence_cannot_move_backwards(self):
        finalize_invoice(self.invoice.pk, 0)
        self.client.post(reverse('sequence_update'), {'next_number': 1})
        self.assertEqual(NumberSequence.objects.get().next_number, 2)
        self.client.post(reverse('sequence_update'), {'next_number': 160})
        self.assertEqual(NumberSequence.objects.get().next_number, 160)

    def test_cash_requires_date_and_is_marked_paid(self):
        data = self.post_data()
        data['payment_method'] = 'cash'
        self.assertContains(self.client.post(reverse('invoice_edit', args=[self.invoice.pk]), data), 'Datum der Barzahlung')
        self.invoice.payment_method = 'cash'
        self.invoice.cash_date = date(2026,1,2)
        self.invoice.save()
        invoice = finalize_invoice(self.invoice.pk, 0)
        self.assertEqual(invoice.paid_at, date(2026,1,2))

    def test_patient_csv_import_stable_ids_and_atomic_rollback(self):
        header = 'ID;Anrede;Vorname;Nachname;Straße;Ort;Geburtsdatum;Telefon\n'
        good = '20;Frau;Anna;Muster;Straße 1;Erbach;01.02.1990;123\n'
        self.assertEqual(import_patients((header+good).encode()), 1)
        pk = Patient.objects.get(legacy_id='20').pk
        import_patients((header+good.replace('Anna', 'Anne')).encode())
        self.assertEqual(Patient.objects.get(legacy_id='20').pk, pk)
        with self.assertRaises(ValidationError):
            import_patients((header+good.replace('20;', '30;')+'40;Herr;Bob;Muster;S;O;14.14.1414;1\n').encode())
        self.assertFalse(Patient.objects.filter(legacy_id='30').exists())

    def test_template_csv_import_is_repeatable(self):
        data = 'Datum;Ziffer;Leistungsbeschreibung;Summe;Diagnose\n;1234;Therapie;24,99;\n'.encode()
        import_templates(data)
        import_templates(data)
        self.assertEqual(ServiceTemplate.objects.count(), 1)

    def test_login_is_rate_limited(self):
        self.client.logout()
        for _ in range(5):
            self.client.post(reverse('login'), {'username':'steffi','password':'wrong'})
        self.assertEqual(self.client.post(reverse('login'), {'username':'steffi','password':'wrong'}).status_code, 429)

    def test_form_escapes_stored_html(self):
        self.patient.last_name = '<script>alert(1)</script>'
        self.patient.save()
        response = self.client.get(reverse('patient_detail', args=[self.patient.pk]))
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_preview_does_not_allocate_number(self):
        response = self.client.get(reverse('invoice_pdf', args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))
        self.invoice.refresh_from_db()
        self.assertIsNone(self.invoice.number)
        self.assertFalse(NumberSequence.objects.exists())

    def test_item_csv_import_rolls_back_on_bad_second_row(self):
        data = 'Datum;Ziffer;Leistungsbeschreibung;Summe;Diagnose\n01.01.2026;1;Therapie;20,00;\n14.14.1414;1;Therapie;20,00;\n'
        file = SimpleUploadedFile('invoice.csv', data.encode(), content_type='text/csv')
        response = self.client.post(reverse('items_import', args=[self.invoice.pk]), {'file':file})
        self.assertContains(response, 'Ungültiges Datum')
        self.assertEqual(self.invoice.items.count(), 1)

    def test_paid_status_does_not_change_pdf(self):
        invoice = finalize_invoice(self.invoice.pk, 0)
        data = (Path(self.tmp.name)/invoice.pdf_path).read_bytes()
        self.client.post(reverse('invoice_paid', args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertIsNotNone(invoice.paid_at)
        self.assertEqual((Path(self.tmp.name)/invoice.pdf_path).read_bytes(), data)

    def test_missing_practice_address_blocks_finalization(self):
        self.practice.street = ''
        self.practice.save()
        with self.assertRaises(ValidationError):
            finalize_invoice(self.invoice.pk, 0)
        self.assertFalse(NumberSequence.objects.exists())
