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
        setting = override_settings(DATA_DIR=Path(self.tmp.name), MEDIA_ROOT=Path(self.tmp.name))
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

    def practice_data(self):
        return {name:getattr(self.practice,name) for name in [
            'name','profession','street','city','phone','bank','iban','bic','tax_text','payment_days','color_scheme']}

    def logo_file(self, color='green'):
        from io import BytesIO
        from PIL import Image
        stream = BytesIO()
        Image.new('RGB',(120,80),color).save(stream,format='PNG')
        return SimpleUploadedFile('logo.png',stream.getvalue(),content_type='image/png')

    def test_greetings_disabled_by_default(self):
        self.assertFalse(self.practice.developer_greetings)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'personal-greeting')
        self.assertNotContains(self.client.get(reverse('settings')), 'name="developer_greetings" checked')

    def test_greetings_setting_enables_and_disables_message(self):
        data=self.practice_data() | {'developer_greetings':'on'}
        response=self.client.post(reverse('settings'),data)
        self.assertEqual(response.status_code,302)
        self.assertContains(self.client.get(reverse('dashboard')), 'personal-greeting')
        self.assertNotContains(self.client.get(reverse('settings')), 'greeting_messages')
        self.client.post(reverse('settings'),self.practice_data())
        self.assertNotContains(self.client.get(reverse('dashboard')),'personal-greeting')

    def test_draft_filter_keeps_only_drafts(self):
        draft=Invoice.objects.create(patient=self.patient)
        issued=finalize_invoice(self.invoice.pk,0)
        response=self.client.get(reverse('invoices'),{'status':'draft'})
        self.assertContains(response,reverse('invoice_detail',args=[draft.pk]))
        self.assertNotContains(response,issued.number)
        self.assertContains(response,'>Entwurf</option>')

    def test_logo_upload_preview_and_clear(self):
        self.assertEqual(self.client.post(reverse('settings'),self.practice_data() | {'logo':self.logo_file()}).status_code,302)
        self.practice.refresh_from_db()
        old_path=self.practice.logo.name
        self.assertTrue(old_path.startswith('logos/'))
        self.assertTrue((Path(self.tmp.name)/old_path).is_file())
        self.assertEqual(self.client.get(reverse('practice_logo')).status_code,200)
        self.client.post(reverse('settings'),self.practice_data() | {'remove_logo':'on'})
        self.practice.refresh_from_db()
        self.assertFalse(self.practice.logo)
        self.assertEqual(self.client.get(reverse('practice_logo')).status_code,404)
        # Preserve versions potentially referenced by existing invoices.
        self.assertTrue((Path(self.tmp.name)/old_path).is_file())

    def test_logo_requires_authentication(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('practice_logo')).status_code,302)

    def test_invalid_logo_rejected(self):
        invalid=SimpleUploadedFile('bad.png',b'not an image',content_type='image/png')
        self.assertContains(self.client.post(reverse('settings'),self.practice_data() | {'logo':invalid}),'nicht als Logo')
        self.practice.refresh_from_db()
        self.assertFalse(self.practice.logo)

    def test_oversize_logo_rejected(self):
        huge=SimpleUploadedFile('huge.png',b'x'*(2*1024*1024+1),content_type='image/png')
        self.assertContains(self.client.post(reverse('settings'),self.practice_data() | {'logo':huge}),'maximal 2 MB')

    def test_logo_cannot_be_removed_and_uploaded_together(self):
        response=self.client.post(reverse('settings'),self.practice_data() | {'logo':self.logo_file(),'remove_logo':'on'})
        self.assertContains(response,'entweder ein neues Logo')

    def test_logo_snapshot_and_pdf_survive_replacement(self):
        self.client.post(reverse('settings'),self.practice_data() | {'logo':self.logo_file()})
        self.practice.refresh_from_db()
        first_logo=self.practice.logo.name
        issued=finalize_invoice(self.invoice.pk,0)
        original=(Path(self.tmp.name)/issued.pdf_path).read_bytes()
        self.assertEqual(issued.practice_snapshot['logo'],first_logo)
        self.client.post(reverse('settings'),self.practice_data() | {'logo':self.logo_file('blue')})
        self.practice.refresh_from_db()
        self.assertNotEqual(self.practice.logo.name,first_logo)
        self.assertTrue((Path(self.tmp.name)/first_logo).exists())
        self.assertEqual((Path(self.tmp.name)/issued.pdf_path).read_bytes(),original)

    def test_pdf_images_optional_and_has_page_totals(self):
        from io import BytesIO
        from billing.services import frozen_preview
        from billing.pdf import render_pdf
        from pypdf import PdfReader
        # The runtime-only inspection dependency is not required by the app.
        data=render_pdf(frozen_preview(self.invoice),preview=True)
        pdf=PdfReader(BytesIO(data))
        self.assertFalse(list(pdf.pages[0].images))
        self.assertIn('Seite 1 von 1',pdf.pages[0].extract_text())
        self.client.post(reverse('settings'),self.practice_data() | {'logo':self.logo_file()})
        issued=finalize_invoice(self.invoice.pk,0)
        pdf=PdfReader(Path(self.tmp.name)/issued.pdf_path)
        self.assertTrue(list(pdf.pages[0].images))

    def test_multipage_pdf_repeats_header_logo_and_page_totals(self):
        from billing.services import frozen_preview
        from billing.pdf import render_pdf
        from pypdf import PdfReader
        from io import BytesIO
        self.client.post(reverse('settings'),self.practice_data() | {'logo':self.logo_file()})
        for _ in range(65):
            InvoiceItem.objects.create(invoice=self.invoice,description='Längere Leistungsbeschreibung mit Übung und Massage',price='15.00')
        pdf=PdfReader(BytesIO(render_pdf(frozen_preview(self.invoice),preview=True)))
        self.assertGreater(len(pdf.pages),1)
        for index,page in enumerate(pdf.pages,start=1):
            self.assertIn(f'Seite {index} von {len(pdf.pages)}',page.extract_text())
            self.assertIn('RECHNUNGSNUMMER',page.extract_text())
            self.assertTrue(list(page.images))

    def test_private_pdf_text_depends_on_invoice_type_not_payment_method(self):
        from io import BytesIO
        from pypdf import PdfReader
        from .pdf import render_pdf
        from .services import frozen_preview
        for payment in ['transfer', 'cash']:
            self.invoice.invoice_type = 'private'
            self.invoice.payment_method = payment
            self.invoice.cash_date = date(2026, 1, 1)
            text = PdfReader(BytesIO(render_pdf(frozen_preview(self.invoice), preview=True))).pages[0].extract_text()
            self.assertIn('analogen Leistungsziffern', text)
            self.assertIn('Heilpraktikerleistungen', text)
            if payment != 'cash':
                self.assertIn('innerhalb von 14 Tagen', text)
        self.invoice.invoice_type = 'self'
        self.invoice.payment_method = 'transfer'
        text = PdfReader(BytesIO(render_pdf(frozen_preview(self.invoice), preview=True))).pages[0].extract_text()
        self.assertNotIn('analogen Leistungsziffern', text)

    def test_delete_requires_auth_confirmation_and_csrf(self):
        template = ServiceTemplate.objects.create(description='Massage', price='12')
        for name, obj in [('patient_delete', self.patient), ('invoice_delete', self.invoice), ('template_delete', template)]:
            url = reverse(name, args=[obj.pk])
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())
            self.client.post(url, {})
            self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())
            csrf_client = Client(enforce_csrf_checks=True)
            csrf_client.force_login(self.user)
            self.assertEqual(csrf_client.post(url, {'confirm': 'yes'}).status_code, 403)
            self.client.logout()
            self.assertEqual(self.client.post(url, {'confirm': 'yes'}).status_code, 302)
            self.client.force_login(self.user)

    def test_draft_and_final_invoices_can_be_deleted(self):
        draft = Invoice.objects.create(patient=self.patient)
        InvoiceItem.objects.create(invoice=draft, date=date.today(), description='Test', price='1')
        self.client.post(reverse('invoice_delete', args=[draft.pk]), {'confirm': 'yes'})
        self.assertFalse(Invoice.objects.filter(pk=draft.pk).exists())
        self.assertFalse(InvoiceItem.objects.filter(invoice_id=draft.pk).exists())
        invoice = finalize_invoice(self.invoice.pk, 0)
        pdf = (Path(self.tmp.name) / invoice.pdf_path).read_bytes()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('invoice_delete', args=[invoice.pk]), {'confirm': 'yes'})
        self.assertFalse(Invoice.objects.filter(pk=invoice.pk).exists())
        self.assertFalse((Path(self.tmp.name) / invoice.pdf_path).exists())
        self.assertFalse((Path(self.tmp.name) / invoice.copy_path).exists())
        self.assertEqual(NumberSequence.objects.get(year=invoice.date.year).next_number, 2)

    def test_patient_delete_protects_existing_invoices(self):
        response = self.client.post(reverse('patient_delete', args=[self.patient.pk]), {'confirm': 'yes'}, follow=True)
        self.assertContains(response, 'hat noch Rechnungen')
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())
        self.invoice.delete()
        self.client.post(reverse('patient_delete', args=[self.patient.pk]), {'confirm': 'yes'})
        self.assertFalse(Patient.objects.filter(pk=self.patient.pk).exists())

    def test_template_delete_preserves_invoice_items(self):
        template = ServiceTemplate.objects.create(description=self.item.description, price=self.item.price)
        self.client.post(reverse('template_delete', args=[template.pk]), {'confirm': 'yes'})
        self.assertFalse(ServiceTemplate.objects.filter(pk=template.pk).exists())
        self.assertTrue(InvoiceItem.objects.filter(pk=self.item.pk).exists())

    def test_checkbox_labels_have_no_colon(self):
        self.assertContains(self.client.get(reverse('template_new')), '>Aktiv</label>', html=False)
        self.assertContains(self.client.get(reverse('settings')), '>Grüße vom Entwickler</label>', html=False)


    def test_payment_choices_and_legacy_conversion(self):
        import importlib
        from django.apps import apps
        from django.db import connection
        from .forms import InvoiceForm
        form = InvoiceForm()
        self.assertEqual(list(form.fields['payment_method'].choices), [('transfer', 'Überweisung'), ('cash', 'Barzahlung')])
        data = self.post_data()
        data['payment_method'] = 'private'
        form = InvoiceForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn('payment_method', form.errors)
        invoice = finalize_invoice(self.invoice.pk, 0)
        pdf = (Path(self.tmp.name) / invoice.pdf_path).read_bytes()
        Invoice.objects.filter(pk=invoice.pk).update(payment_method='private')
        migration = importlib.import_module('billing.migrations.0004_remove_private_payment')
        from types import SimpleNamespace
        migration.convert_private_payment(apps, SimpleNamespace(connection=connection))
        invoice.refresh_from_db()
        self.assertEqual(invoice.payment_method, 'transfer')
        self.assertEqual((Path(self.tmp.name) / invoice.pdf_path).read_bytes(), pdf)


    def test_all_59_default_greetings_can_appear_on_dashboard(self):
        from .greetings import MESSAGES
        self.assertEqual(len(MESSAGES), 59)
        self.assertEqual(len(set(MESSAGES)), 59)
        self.practice.developer_greetings = True
        self.practice.save()
        for index, message in enumerate(MESSAGES):
            with patch('billing.greetings.choice', side_effect=lambda options, index=index: options[index]):
                response = self.client.get(reverse('dashboard'))
            self.assertEqual(response.context['greeting'], message)
            from django.utils.html import escape
            self.assertContains(response, escape(message))


    def test_cancel_and_correct_preserves_original_and_links_new_documents(self):
        from .services import cancel_invoice
        from io import BytesIO
        from pypdf import PdfReader
        original = finalize_invoice(self.invoice.pk, 0)
        pdf = (Path(self.tmp.name) / original.pdf_path).read_bytes()
        cancellation, draft = cancel_invoice(original.pk, reason='Falsche Leistung', correct=True)
        original.refresh_from_db()
        self.assertEqual(original.status, 'Storniert')
        self.assertEqual(cancellation.status, 'Stornorechnung')
        self.assertEqual(cancellation.total, -original.total)
        self.assertNotEqual(cancellation.number, original.number)
        self.assertEqual(cancellation.patient_snapshot, original.patient_snapshot)
        self.assertEqual((Path(self.tmp.name) / original.pdf_path).read_bytes(), pdf)
        text = ''.join(page.extract_text() for page in PdfReader(BytesIO((Path(self.tmp.name)/cancellation.pdf_path).read_bytes())).pages)
        self.assertIn('STORNORECHNUNG', text)
        self.assertIn(original.number, text)
        self.assertIn('-49,98', text)
        self.assertNotIn('Bitte überweisen', text)
        self.assertIsNone(draft.number)
        self.assertEqual(draft.correction_of_id, original.pk)
        self.assertEqual(draft.items.count(), original.items.count())
        self.assertIsNone(draft.paid_at)
        item = draft.items.first()
        item.price = '30'
        item.save()
        replacement = finalize_invoice(draft.pk, 0)
        self.assertEqual(replacement.total, Decimal('60'))
        self.assertNotEqual(replacement.number, original.number)
        self.assertNotEqual(replacement.number, cancellation.number)
        self.assertEqual(self.client.get(reverse('invoice_detail', args=[original.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse('invoice_detail', args=[cancellation.pk])), replacement.number)

    def test_cancel_excludes_documents_from_open_and_paid_filters(self):
        from .services import cancel_invoice
        original = finalize_invoice(self.invoice.pk, 0)
        cancellation, _ = cancel_invoice(original.pk, reason='Fehler')
        self.assertEqual(self.client.get(reverse('dashboard')).context['open_count'], 0)
        self.assertEqual(len(self.client.get(reverse('invoices'), {'status':'open'}).context['invoices']), 0)
        ids = {i.pk for i in self.client.get(reverse('invoices'), {'status':'cancelled'}).context['invoices']}
        self.assertEqual(ids, {original.pk, cancellation.pk})
        self.client.post(reverse('invoice_paid', args=[original.pk]))
        original.refresh_from_db()
        self.assertIsNone(original.paid_at)

    def test_paid_cancellation_records_refund_without_marking_replacement_paid(self):
        from .services import cancel_invoice
        original = finalize_invoice(self.invoice.pk, 0)
        original.paid_at = date.today()
        original.save()
        with self.assertRaises(ValidationError):
            cancel_invoice(original.pk, reason='Fehler')
        cancellation, draft = cancel_invoice(original.pk, reason='Fehler', correct=True, settlement='pending')
        self.assertEqual(cancellation.settlement, 'pending')
        self.assertIsNone(draft.paid_at)
        pdf = (Path(self.tmp.name)/cancellation.pdf_path).read_bytes()
        self.client.post(reverse('invoice_settlement',args=[cancellation.pk]), {'settlement':'refund','settlement_note':'Überwiesen'})
        cancellation.refresh_from_db()
        self.assertEqual(cancellation.settlement, 'refund')
        self.assertEqual((Path(self.tmp.name)/cancellation.pdf_path).read_bytes(), pdf)
        self.assertEqual(len(self.client.get(reverse('invoices'), {'status':'paid'}).context['invoices']), 0)

    def test_cancel_failure_rolls_back_and_duplicate_cancellation_is_blocked(self):
        from .services import cancel_invoice
        original = finalize_invoice(self.invoice.pk, 0)
        next_number = NumberSequence.objects.get(year=original.date.year).next_number
        with patch('billing.services.render_pdf', side_effect=OSError('failed')):
            with self.assertRaises(OSError):
                cancel_invoice(original.pk, reason='Fehler', correct=True)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(NumberSequence.objects.get(year=original.date.year).next_number, next_number)
        cancellation, _ = cancel_invoice(original.pk, reason='Fehler')
        with self.assertRaises(ValidationError):
            cancel_invoice(original.pk, reason='Nochmals')
        with self.assertRaises(ValidationError):
            cancel_invoice(cancellation.pk, reason='Nochmals')
        self.assertEqual(Invoice.objects.count(), 2)

    def test_cancellation_requires_confirmation_reason_auth_and_csrf(self):
        original = finalize_invoice(self.invoice.pk, 0)
        url = reverse('invoice_correct',args=[original.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {'reason':'Fehler'})
        self.assertEqual(Invoice.objects.count(), 1)
        self.client.post(url, {'confirm':'on'})
        self.assertEqual(Invoice.objects.count(), 1)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(url, {'confirm':'on','reason':'Fehler'}).status_code,403)
        self.client.logout()
        self.assertEqual(self.client.post(url, {'confirm':'on','reason':'Fehler'}).status_code,302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url, {'confirm':'on','reason':'Fehler'}).status_code,302)
        self.assertEqual(Invoice.objects.count(), 3)

    def test_menu_logo_and_all_color_schemes(self):
        from .forms import PracticeForm
        self.assertNotIn('greeting_messages', PracticeForm().fields)
        self.assertContains(self.client.get(reverse('dashboard')), 'brand-icon')
        self.client.post(reverse('settings'),self.practice_data()|{'logo':self.logo_file()})
        for scheme in ['green','blue','purple','rose','dark']:
            response=self.client.post(reverse('settings'),self.practice_data()|{'color_scheme':scheme})
            self.assertEqual(response.status_code,302)
            response=self.client.get(reverse('dashboard'))
            self.assertContains(response, f'data-theme="{scheme}"')
            self.assertContains(response, 'class="brand-logo"')
            self.assertNotContains(response, 'class="brand-icon"')
        response=self.client.post(reverse('settings'),self.practice_data()|{'color_scheme':'invalid'})
        self.assertEqual(response.status_code,200)



    def test_failed_cancellation_save_removes_generated_file_and_rolls_back(self):
        from .services import cancel_invoice
        original = finalize_invoice(self.invoice.pk, 0)
        paths = set((Path(self.tmp.name)/'pdfs').iterdir())
        save = Invoice.save
        def fail_on_frozen_cancellation(obj, *args, **kwargs):
            if obj.cancellation_of_id and obj.pdf_path:
                raise OSError('Failed after writing cancellation PDF')
            return save(obj, *args, **kwargs)
        with patch.object(Invoice, 'save', fail_on_frozen_cancellation):
            with self.assertRaises(OSError):
                cancel_invoice(original.pk, reason='Fehler', correct=True)
        self.assertEqual(set((Path(self.tmp.name)/'pdfs').iterdir()), paths)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_linked_documents_require_explicit_deletion_in_order(self):
        from .services import cancel_invoice
        original = finalize_invoice(self.invoice.pk, 0)
        cancellation, draft = cancel_invoice(original.pk, reason='Fehler', correct=True)
        url = reverse('invoice_delete', args=[original.pk])
        self.assertContains(self.client.get(url), 'verknüpften Rechnungen')
        self.client.post(url, {'confirm':'yes'})
        self.assertEqual(Invoice.objects.count(), 3)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('invoice_delete', args=[cancellation.pk]), {'confirm':'yes'})
        original.refresh_from_db()
        self.assertEqual(original.status, 'Offen')
        self.client.post(reverse('invoice_delete', args=[draft.pk]), {'confirm':'yes'})
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {'confirm':'yes'})
        self.assertEqual(Invoice.objects.count(), 0)

    def test_paid_invoice_can_be_deleted_after_confirmation(self):
        original = finalize_invoice(self.invoice.pk, 0)
        original.paid_at = date.today()
        original.save()
        url = reverse('invoice_delete', args=[original.pk])
        self.assertContains(self.client.get(url), 'Bist du dir sicher?')
        self.assertTrue(Invoice.objects.filter(pk=original.pk).exists())
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {'confirm':'yes'})
        self.assertFalse(Invoice.objects.filter(pk=original.pk).exists())
