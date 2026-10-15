from io import BytesIO
from pathlib import Path
import tempfile
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings, Client
from django.urls import reverse
from pypdf import PdfReader
from .models import Patient, TreatmentNote, TreatmentAddendum, ClinicalAttachment


class ClinicalTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = override_settings(DATA_DIR=Path(self.tmp.name), MEDIA_ROOT=Path(self.tmp.name))
        config.enable()
        self.addCleanup(config.disable)
        self.user = get_user_model().objects.create_user('steffi', password='test-password')
        self.client.force_login(self.user)
        self.patient = Patient.objects.create(first_name='Max', last_name='Müller')
        self.other = Patient.objects.create(first_name='Eva', last_name='Test')
        self.note = TreatmentNote.objects.create(patient=self.patient)
        self.edit = reverse('note_edit', args=[self.patient.pk, self.note.pk])

    def data(self, **changes):
        result = {'date': '2026-10-10', 'body': 'Symptome & Befund: Schulter\nBehandlung dokumentiert.', 'revision': self.note.revision, 'action': 'save'}
        result.update(changes)
        return result

    def finalize(self):
        response = self.client.post(self.edit, self.data(action='finalize'))
        self.assertRedirects(response, reverse('documentation'))
        self.note.refresh_from_db()

    def docx(self):
        # An existing Word file is now treated as an opaque, unchanged attachment.
        return b'Original attachment bytes'

    def upload(self, data=None, name='Altakte.docx'):
        return self.client.post(reverse('attachment_upload', args=[self.patient.pk, self.note.pk]), {'file': SimpleUploadedFile(name, self.docx() if data is None else data)})

    def test_pages_render_and_authentication(self):
        urls = [reverse('clinical_record', args=[self.patient.pk]), self.edit, reverse('documentation_export', args=[self.patient.pk])]
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 200)
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)

    def test_new_note_post_only_and_author(self):
        url = reverse('note_new', args=[self.patient.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(TreatmentNote.objects.latest('pk').created_by, self.user)

    def test_autosave_and_stale_edit(self):
        response = self.client.post(self.edit, self.data(), HTTP_ACCEPT='application/json')
        self.assertEqual(response.json()['revision'], 1)
        response = self.client.post(self.edit, self.data(body='Überholt'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 409)
        self.note.refresh_from_db()
        self.assertIn('Schulter', self.note.body)
        self.assertEqual(self.note.edited_by, self.user)

    def test_invalid_date_not_saved(self):
        response = self.client.post(self.edit, self.data(date='keine Angabe'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 400)
        self.note.refresh_from_db()
        self.assertEqual(self.note.revision, 0)

    def test_finalize_requires_text_but_no_checkbox(self):
        self.client.post(self.edit, self.data(action='finalize', body=''))
        self.note.refresh_from_db()
        self.assertIsNone(self.note.finalized_at)
        self.assertEqual(self.client.post(self.edit, self.data(action='finalize')).status_code, 302)
        self.note.refresh_from_db()
        self.assertIsNotNone(self.note.finalized_at)

    def test_finalized_note_is_immutable_and_snapshots_identity(self):
        self.finalize()
        self.assertEqual(self.note.author_name, 'steffi')
        self.assertEqual(self.note.patient_snapshot['last_name'], 'Müller')
        response = self.client.post(self.edit, self.data(body='Änderung'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 409)
        self.note.body = 'Änderung'
        with self.assertRaises(ValidationError):
            self.note.save()
        self.assertContains(self.client.get(reverse('note_detail', args=[self.patient.pk, self.note.pk])), 'Schulter')

    def test_addendum_saved_without_checkbox_and_original_preserved(self):
        url = reverse('note_addendum', args=[self.patient.pk, self.note.pk])
        self.assertEqual(self.client.post(url, {'body': 'Nachtrag', 'confirm': 'on'}).status_code, 404)
        self.finalize()
        original = self.note.body
        self.assertEqual(self.client.post(url, {'body': ''}).status_code, 400)
        self.assertEqual(self.client.post(url, {'body': 'Zusatzbefund'}).status_code, 302)
        addition = self.note.addenda.get()
        self.assertEqual(addition.author_name, 'steffi')
        with self.assertRaises(ValidationError):
            addition.save()
        self.note.refresh_from_db()
        self.assertEqual(self.note.body, original)
        self.assertContains(self.client.get(reverse('clinical_record', args=[self.patient.pk])+'?q=Zusatzbefund'), 'Schulter')

    def test_patient_documentation_protects_deletion(self):
        self.client.post(reverse('patient_delete', args=[self.patient.pk]))
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())
        self.patient.active = False
        self.patient.save()
        self.assertEqual(self.client.get(reverse('clinical_record', args=[self.patient.pk])).status_code, 200)

    def test_upload_original_download_integrity(self):
        content = self.docx()
        self.assertEqual(self.upload(content).status_code, 302)
        attachment = ClinicalAttachment.objects.get()
        self.assertTrue(attachment.file.name.startswith('documents/'))
        self.assertEqual(Path(attachment.file.path).read_bytes(), content)
        url = reverse('attachment_download', args=[self.patient.pk, attachment.pk])
        response = self.client.get(url)
        self.assertEqual(b''.join(response.streaming_content), content)
        self.assertIn('no-store', response['Cache-Control'])
        Path(attachment.file.path).write_bytes(b'changed')
        self.assertEqual(self.client.get(url).status_code, 409)

    def test_generic_uploads_and_limit(self):
        for name, content in [('Befund.pdf', b'%PDF-original'), ('Foto.jpg', b'photo'), ('Messwerte.csv', b'a,b'), ('notizen.txt', b'notizen'), ('datei', b'opaque')]:
            self.assertEqual(self.upload(content, name).status_code, 302)
            attachment = ClinicalAttachment.objects.latest('pk')
            self.assertEqual(Path(attachment.file.path).read_bytes(), content)
            response = self.client.get(reverse('attachment_download', args=[self.patient.pk, attachment.pk]))
            self.assertEqual(response['Content-Type'], 'application/octet-stream')
            self.assertTrue(response['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(self.upload(b'a' * (20 * 1024 * 1024 + 1), 'gross.bin').status_code, 400)
        self.assertEqual(ClinicalAttachment.objects.count(), 5)

    def test_wrong_patient_cannot_access_notes_or_files(self):
        self.upload()
        attachment = ClinicalAttachment.objects.get()
        for route, pk in [('note_edit', self.note.pk), ('note_detail', self.note.pk), ('note_export', self.note.pk), ('attachment_download', attachment.pk)]:
            self.assertEqual(self.client.get(reverse(route, args=[self.other.pk, pk])).status_code, 404)

    def test_pdf_export_draft_final_addenda_and_original_listing(self):
        url = reverse('note_export', args=[self.patient.pk, self.note.pk])
        response = self.client.get(url)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response['Content-Disposition'].endswith('.pdf\"'))
        text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(b''.join(response.streaming_content))).pages)
        self.assertIn('ENTWURF', text)
        self.finalize()
        TreatmentAddendum.objects.create(note=self.note, body='Nachtrag: Wärme', author_name='steffi')
        self.patient.last_name = 'Geändert'
        self.patient.save()
        response = self.client.get(url)
        text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(b''.join(response.streaming_content))).pages)
        self.assertIn('Müller', text)
        self.assertIn('Nachtrag: Wärme', text)
        self.assertNotIn('ENTWURF', text)
        self.upload()
        response = self.client.get(reverse('documentation_export', args=[self.patient.pk]))
        text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(b''.join(response.streaming_content))).pages)
        self.assertIn('Altakte.docx', text)
        self.assertNotIn('Originaldokument mit', text)

    def test_csrf_required_for_mutations(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.edit, self.data()).status_code, 403)

    def test_invalid_control_characters_rejected(self):
        response = self.client.post(self.edit, self.data(body='Befund\x01'), HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 400)
        self.note.refresh_from_db()
        self.assertEqual(self.note.body, '')

    def test_failed_database_upload_removes_file(self):
        with patch('billing.clinical_views.ClinicalAttachment.save', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaises(RuntimeError):
                self.upload()
        self.assertEqual(list(Path(self.tmp.name).rglob('*.docx')), [])
        self.assertFalse(ClinicalAttachment.objects.exists())

    def test_attachment_path_escape_and_authentication(self):
        self.upload()
        attachment = ClinicalAttachment.objects.get()
        url = reverse('attachment_download', args=[self.patient.pk, attachment.pk])
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        attachment.file.name = '../outside.docx'
        attachment.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_search_attachment_filename_and_note_text(self):
        self.upload()
        self.note.body = 'Kopfschmerz'
        self.note.save()
        url = reverse('clinical_record', args=[self.patient.pk])
        response = self.client.get(url, {'q': 'Altakte'})
        self.assertContains(response, 'Altakte.docx')
        self.assertContains(response, 'Kopfschmerz')
        response = self.client.get(url, {'q': 'Kopf'})
        self.assertContains(response, 'Kopfschmerz')
        self.assertContains(response, 'Altakte.docx')
        response = self.client.get(url, {'q': 'Unbekannter Befund'})
        self.assertNotContains(response, 'Kopfschmerz')
        self.assertNotContains(response, 'Altakte.docx')

    def test_documentation_menu_and_global_filters(self):
        self.note.body = 'Schulterbefund'
        self.note.save()
        closed = TreatmentNote.objects.create(patient=self.other, body='Kniebefund', finalized_at=__import__('django.utils.timezone', fromlist=['now']).now(), patient_snapshot=self.other.snapshot(), author_name='steffi')
        url = reverse('documentation')
        response = self.client.get(url)
        self.assertContains(response, 'href="/dokumentation/"')
        self.assertContains(response, 'Schulterbefund')
        self.assertContains(response, 'Kniebefund')
        self.assertNotContains(response, 'als Word')
        response = self.client.get(url, {'status': 'draft'})
        self.assertContains(response, 'Schulterbefund')
        self.assertNotContains(response, 'Kniebefund')
        response = self.client.get(url, {'status': 'final'})
        self.assertNotContains(response, 'Schulterbefund')
        self.assertContains(response, 'Kniebefund')
        response = self.client.get(url, {'patient': self.other.pk})
        self.assertNotContains(response, 'Schulterbefund')
        self.assertContains(response, 'Kniebefund')
        self.assertContains(self.client.get(url, {'q': 'Müller'}), 'Schulterbefund')
        self.assertNotContains(self.client.get(url, {'q': 'Müller'}), 'Kniebefund')
        self.assertEqual(self.client.get(url, {'patient': 'invalid', 'status': 'unknown'}).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_documentation_pagination_preserves_filters(self):
        TreatmentNote.objects.bulk_create([TreatmentNote(patient=self.patient, body='Befund '+str(i)) for i in range(30)])
        response = self.client.get(reverse('documentation'), {'status': 'draft', 'patient': self.patient.pk, 'q': 'Befund'})
        self.assertEqual(len(response.context['page_obj']), 25)
        self.assertEqual(response.context['page_obj'].paginator.count, 30)
        self.assertContains(response, 'status=draft')
        response = self.client.get(reverse('documentation'), {'status': 'draft', 'patient': self.patient.pk, 'q': 'Befund', 'page': 2})
        self.assertEqual(len(response.context['page_obj']), 5)

    def test_global_new_note_selection(self):
        url = reverse('documentation_new')
        self.assertEqual(self.client.get(url).status_code, 405)
        count = TreatmentNote.objects.count()
        self.assertEqual(self.client.post(url, {'patient': 'invalid'}).status_code, 302)
        self.assertEqual(TreatmentNote.objects.count(), count)
        self.assertEqual(self.client.post(url, {'patient': self.other.pk}).status_code, 302)
        self.assertEqual(TreatmentNote.objects.latest('pk').patient, self.other)

    def test_pdf_long_text_paginates_and_escapes_markup(self):
        self.note.body = ('Befund <b> & Wärme\n' * 250) + 'ENDE DER DOKUMENTATION'
        self.note.save()
        response = self.client.get(reverse('note_export', args=[self.patient.pk, self.note.pk]))
        reader = PdfReader(BytesIO(b''.join(response.streaming_content)))
        self.assertGreater(len(reader.pages), 1)
        text = '\n'.join(page.extract_text() for page in reader.pages)
        self.assertIn('Befund <b> & Wärme', text)
        self.assertIn('ENDE DER DOKUMENTATION', text)
        self.assertIn(f'Seite {len(reader.pages)} von {len(reader.pages)}', reader.pages[-1].extract_text())

    def test_new_documentation_allows_only_active_patients(self):
        self.other.active = False
        self.other.save()
        response = self.client.get(reverse('documentation'))
        self.assertNotIn(self.other, list(response.context['active_patients']))
        self.assertIn(self.other, list(response.context['patients']))
        self.assertContains(response, 'Aktiven Patienten auswählen')
        count = TreatmentNote.objects.count()
        self.client.post(reverse('documentation_new'), {'patient': self.other.pk})
        self.assertEqual(TreatmentNote.objects.count(), count)
        self.assertEqual(self.client.post(reverse('note_new', args=[self.other.pk])).status_code, 404)

    def test_navigation_and_checkbox_removal(self):
        response = self.client.get(reverse('documentation'))
        self.assertContains(response, 'href="'+self.edit+'"')
        self.assertNotContains(response, 'href="'+reverse('clinical_record', args=[self.patient.pk])+'"')
        self.assertNotContains(response, 'Weiter schreiben')
        response = self.client.get(reverse('clinical_record', args=[self.patient.pk]))
        self.assertContains(response, 'Patientenakte:')
        self.assertContains(response, 'Zurück zu')
        self.assertNotContains(response, 'Patient öffnen')
        self.assertNotContains(response, 'Datei anhängen')
        response = self.client.get(self.edit)
        self.assertNotContains(response, 'name="confirm"')
        self.assertContains(response, 'Export als PDF')
        self.assertContains(response, 'Entwurf speichern')
        self.finalize()
        response = self.client.get(reverse('note_detail', args=[self.patient.pk, self.note.pk]))
        self.assertNotContains(response, 'name="confirm"')
        self.assertContains(response, 'Export als PDF')

    def test_delete_draft_and_final_note_with_files_and_addenda(self):
        self.upload()
        file = Path(ClinicalAttachment.objects.get().file.path)
        url = reverse('note_delete', args=[self.patient.pk, self.note.pk])
        self.assertContains(self.client.get(url), 'endgültig gelöscht')
        self.client.post(url)
        self.assertTrue(TreatmentNote.objects.filter(pk=self.note.pk).exists())
        self.finalize()
        TreatmentAddendum.objects.create(note=self.note, body='Zusatz', author_name='steffi')
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.post(url, {'confirm': 'yes'}).status_code, 302)
        self.assertFalse(TreatmentNote.objects.filter(pk=self.note.pk).exists())
        self.assertFalse(TreatmentAddendum.objects.exists())
        self.assertFalse(ClinicalAttachment.objects.exists())
        self.assertFalse(file.exists())
        draft = TreatmentNote.objects.create(patient=self.patient)
        self.client.post(reverse('note_delete', args=[self.patient.pk, draft.pk]), {'confirm': 'yes'})
        self.assertFalse(TreatmentNote.objects.filter(pk=draft.pk).exists())
        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())

    def test_deletion_and_upload_patient_boundaries_and_csrf(self):
        for name in ['note_delete', 'attachment_upload']:
            url = reverse(name, args=[self.other.pk, self.note.pk])
            self.assertEqual(self.client.post(url, {'confirm': 'yes'}).status_code, 404)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('note_delete', args=[self.patient.pk, self.note.pk]), {'confirm': 'yes'}).status_code, 403)

    def test_attachments_belong_to_one_treatment_and_legacy_assignment(self):
        self.upload()
        attachment = ClinicalAttachment.objects.get()
        self.assertEqual(attachment.note, self.note)
        second = TreatmentNote.objects.create(patient=self.patient)
        response = self.client.get(reverse('note_edit', args=[self.patient.pk, second.pk]))
        self.assertNotContains(response, 'Altakte.docx')
        response = self.client.get(self.edit)
        self.assertContains(response, 'Altakte.docx')
        pdf = self.client.get(reverse('note_export', args=[self.patient.pk, self.note.pk]))
        self.assertIn('Altakte.docx', '\n'.join(page.extract_text() for page in PdfReader(BytesIO(b''.join(pdf.streaming_content))).pages))
        ClinicalAttachment.objects.filter(pk=attachment.pk).update(note=None)
        self.assertContains(self.client.get(reverse('clinical_record', args=[self.patient.pk])), 'Bisherige Anhänge')
        url = reverse('attachment_assign', args=[self.patient.pk, attachment.pk])
        foreign_note = TreatmentNote.objects.create(patient=self.other)
        self.assertEqual(self.client.post(url, {'note': foreign_note.pk}).status_code, 404)
        self.assertEqual(self.client.post(url, {'note': second.pk}).status_code, 302)
        attachment.refresh_from_db()
        self.assertEqual(attachment.note, second)
        self.assertEqual(self.client.post(url, {'note': self.note.pk}).status_code, 404)

    def test_finalize_without_javascript_uses_clicked_button(self):
        self.assertEqual(self.client.post(self.edit, self.data(action='save', submit_action='finalize')).status_code, 302)
        self.note.refresh_from_db()
        self.assertIsNotNone(self.note.finalized_at)
