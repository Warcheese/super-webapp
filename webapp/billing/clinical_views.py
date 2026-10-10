import hashlib
from io import BytesIO
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from datetime import timedelta
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import Patient, TreatmentNote, TreatmentAddendum, ClinicalAttachment
from .clinical_forms import TreatmentNoteForm, AddendumForm, ClinicalAttachmentForm
from .clinical_export import export_documentation


@login_required
def documentation(request):
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', 'all')
    if status not in ['all', 'draft', 'final']:
        status = 'all'
    patient_id = request.GET.get('patient', '')
    selected_patient = Patient.objects.filter(pk=patient_id).first() if patient_id.isdecimal() else None
    notes = TreatmentNote.objects.select_related('patient').order_by('-date', '-updated_at', '-pk')
    if selected_patient:
        notes = notes.filter(patient=selected_patient)
    if query:
        notes = notes.filter(Q(patient__first_name__icontains=query) | Q(patient__last_name__icontains=query) |
                             Q(body__icontains=query) | Q(addenda__body__icontains=query) | Q(attachments__original_name__icontains=query)).distinct()
    if status == 'draft':
        notes = notes.filter(finalized_at__isnull=True)
    elif status == 'final':
        notes = notes.filter(finalized_at__isnull=False)
    params = request.GET.copy()
    params.pop('page', None)
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    return render(request, 'billing/documentation.html', {
        'page_obj': Paginator(notes, 25).get_page(request.GET.get('page')),
        'query': query, 'status': status, 'selected_patient': selected_patient,
        'active_patients': Patient.objects.filter(active=True).order_by('last_name', 'first_name', 'pk'),
        'patients': Patient.objects.order_by('last_name', 'first_name', 'pk'),
        'query_params': params.urlencode(), 'nav': 'documentation',
        'draft_count': TreatmentNote.objects.filter(finalized_at__isnull=True).count(),
        'week_count': TreatmentNote.objects.filter(date__gte=monday, date__lt=monday+timedelta(days=7)).count(),
    })


@login_required
@require_POST
def documentation_new(request):
    patient_id = request.POST.get('patient', '')
    patient = Patient.objects.filter(pk=patient_id, active=True).first() if patient_id.isdecimal() else None
    if not patient:
        messages.error(request, 'Bitte einen aktiven Patienten auswählen.')
        return redirect('documentation')
    note = TreatmentNote.objects.create(patient=patient, created_by=request.user, edited_by=request.user)
    return redirect('note_edit', patient_pk=patient.pk, pk=note.pk)


@login_required
def clinical_record(request, patient_pk):
    patient = get_object_or_404(Patient, pk=patient_pk)
    query = request.GET.get('q', '').strip()
    notes = patient.treatment_notes.prefetch_related('addenda', 'attachments')
    attachments = patient.clinical_attachments.filter(note__isnull=True)
    if query:
        notes = notes.filter(Q(body__icontains=query) | Q(addenda__body__icontains=query) | Q(attachments__original_name__icontains=query)).distinct()
        attachments = attachments.filter(original_name__icontains=query)
    return render(request, 'billing/clinical_record.html', {'patient': patient, 'notes': notes,
        'attachments': attachments, 'query': query, 'nav': 'patients'})


@login_required
@require_POST
def note_new(request, patient_pk):
    patient = get_object_or_404(Patient, pk=patient_pk, active=True)
    note = TreatmentNote.objects.create(patient=patient, created_by=request.user, edited_by=request.user)
    return redirect('note_edit', patient_pk=patient_pk, pk=note.pk)


@login_required
def note_edit(request, patient_pk, pk):
    json_request = 'application/json' in request.headers.get('Accept', '')
    with transaction.atomic():
        note = get_object_or_404(TreatmentNote.objects.select_related('patient').prefetch_related('addenda', 'attachments'),
                                 patient_id=patient_pk, pk=pk)
        if note.finalized_at:
            if request.method == 'POST' and json_request:
                return JsonResponse({'error': 'Dieser Eintrag ist bereits abgeschlossen. Bitte neu laden.'}, status=409)
            return redirect('note_detail', patient_pk=patient_pk, pk=pk)
        form = TreatmentNoteForm(request.POST or None, instance=note)
        status = 200
        if request.method == 'POST':
            valid = form.is_valid()
            try:
                revision = int(request.POST.get('revision', '-1'))
            except ValueError:
                revision = -1
            action = request.POST.get('submit_action') or request.POST.get('action', 'save')
            if revision != note.revision:
                form.add_error(None, 'Der Eintrag wurde inzwischen geändert. Bitte neu laden; deine Eingabe wurde nicht überschrieben.')
                status = 409
            elif action not in ['save', 'finalize']:
                form.add_error(None, 'Ungültige Aktion.')
                status = 400
            elif valid:
                if action == 'finalize' and not form.cleaned_data['body'].strip():
                    form.add_error('body', 'Bitte die Behandlung dokumentieren, bevor du abschließt.')
                else:
                    note = form.save(commit=False)
                    note.revision += 1
                    note.updated_at = timezone.now()
                    note.edited_by = request.user
                    if action == 'finalize':
                        note.finalized_at = timezone.now()
                        note.author_name = request.user.get_username()
                        note.patient_snapshot = note.patient.snapshot()
                    note.save()
                    if json_request:
                        return JsonResponse({'revision': note.revision, 'saved_at': timezone.localtime(note.updated_at).strftime('%H:%M:%S')})
                    messages.success(request, 'Dokumentation abgeschlossen.' if action == 'finalize' else 'Entwurf gespeichert.')
                    return redirect('note_detail' if action == 'finalize' else 'note_edit', patient_pk=patient_pk, pk=pk)
            if json_request:
                return JsonResponse({'error': ' '.join(str(error) for errors in form.errors.values() for error in errors)}, status=status if status != 200 else 400)
    return render(request, 'billing/note_edit.html', {'note': note, 'patient': note.patient, 'form': form, 'upload_form': ClinicalAttachmentForm(), 'nav': 'documentation'}, status=status)


@login_required
def note_detail(request, patient_pk, pk):
    note = get_object_or_404(TreatmentNote.objects.select_related('patient').prefetch_related('addenda', 'attachments'), pk=pk, patient_id=patient_pk)
    if not note.finalized_at:
        return redirect('note_edit', patient_pk=patient_pk, pk=pk)
    return render(request, 'billing/note_detail.html', {'note': note, 'patient': note.patient, 'form': AddendumForm(), 'upload_form': ClinicalAttachmentForm(), 'nav': 'documentation'})


@login_required
@require_POST
def note_addendum(request, patient_pk, pk):
    note = get_object_or_404(TreatmentNote.objects.select_related('patient'), pk=pk, patient_id=patient_pk, finalized_at__isnull=False)
    form = AddendumForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.note = note
            obj.author = request.user
            obj.author_name = request.user.get_username()
            obj.save()
        messages.success(request, 'Nachtrag endgültig gespeichert. Der ursprüngliche Eintrag bleibt unverändert.')
        return redirect('note_detail', patient_pk=patient_pk, pk=pk)
    return render(request, 'billing/note_detail.html', {'note': note, 'patient': note.patient, 'form': form, 'upload_form': ClinicalAttachmentForm(), 'nav': 'documentation'}, status=400)


@login_required
@require_POST
def attachment_upload(request, patient_pk, pk):
    note = get_object_or_404(TreatmentNote.objects.select_related('patient'), pk=pk, patient_id=patient_pk)
    patient = note.patient
    form = ClinicalAttachmentForm(request.POST, request.FILES)
    if form.is_valid():
        file = form.cleaned_data['file']
        digest = hashlib.sha256()
        for chunk in file.chunks():
            digest.update(chunk)
        file.seek(0)
        attachment = ClinicalAttachment(patient=patient, note=note, original_name=Path(file.name.replace(chr(92), '/')).name,
                                        sha256=digest.hexdigest(), uploaded_by=request.user)
        try:
            with transaction.atomic():
                attachment.file.save(file.name, file, save=False)
                attachment.save()
        except Exception:
            if attachment.file.name:
                attachment.file.delete(save=False)
            raise
        messages.success(request, 'Datei unverändert zur Behandlung hinzugefügt.')
        return redirect('note_detail' if note.finalized_at else 'note_edit', patient_pk=patient_pk, pk=pk)
    return render(request, 'billing/note_detail.html' if note.finalized_at else 'billing/note_edit.html', {
        'patient': patient, 'note': note, 'upload_form': form,
        'form': AddendumForm() if note.finalized_at else TreatmentNoteForm(instance=note),
        'nav': 'documentation'}, status=400)


@login_required
def attachment_download(request, patient_pk, pk):
    attachment = get_object_or_404(ClinicalAttachment, pk=pk, patient_id=patient_pk)
    root = Path(settings.DATA_DIR).resolve()
    path = (root / attachment.file.name).resolve()
    if not path.is_relative_to(root / 'documents') or not path.is_file():
        raise Http404('Dokument nicht gefunden. Bitte Datensicherung prüfen.')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != attachment.sha256:
        return HttpResponse('Prüfsumme stimmt nicht. Bitte Datensicherung prüfen.', status=409)
    return FileResponse(BytesIO(data), as_attachment=True, filename=attachment.original_name,
        content_type='application/octet-stream')


@login_required
def documentation_export(request, patient_pk, pk=None):
    patient = get_object_or_404(Patient, pk=patient_pk)
    if pk is not None:
        note = get_object_or_404(TreatmentNote, pk=pk, patient_id=patient_pk)
        notes = [note]
        attachments = note.attachments.all()
    else:
        notes = patient.treatment_notes.prefetch_related('addenda', 'attachments').order_by('date', 'created_at', 'pk')
        attachments = patient.clinical_attachments.all()
    data = export_documentation(patient, notes, attachments)
    return FileResponse(BytesIO(data), as_attachment=True, filename=f'Dokumentation-Patient-{patient.pk}{"-"+str(pk) if pk else ""}.pdf',
        content_type='application/pdf')


@login_required
def note_delete(request, patient_pk, pk):
    with transaction.atomic():
        note = get_object_or_404(TreatmentNote.objects.select_related('patient'), pk=pk, patient_id=patient_pk)
        if request.method == 'POST' and request.POST.get('confirm') == 'yes':
            files = [(attachment.file.storage, attachment.file.name) for attachment in note.attachments.all()]
            root = Path(settings.DATA_DIR).resolve() / 'documents'
            note.delete()
            def remove_files():
                for storage, name in files:
                    path = Path(storage.path(name)).resolve()
                    if path.is_relative_to(root):
                        storage.delete(name)
            transaction.on_commit(remove_files)
            messages.success(request, 'Behandlungseintrag mit Nachträgen und Anhängen gelöscht.')
            return redirect('clinical_record', patient_pk=patient_pk)
        return render(request, 'billing/delete_confirm.html', {
            'title': 'Behandlungseintrag löschen?', 'object_name': f'{note.patient} · {note.date:%d.%m.%Y}',
            'warning': 'Dieser Eintrag wird endgültig gelöscht, einschließlich aller Nachträge und angehängten Dateien. Das kann nicht rückgängig gemacht werden.',
            'cancel_url': reverse('note_detail' if note.finalized_at else 'note_edit', args=[patient_pk, pk]),
            'nav': 'documentation'})


@login_required
@require_POST
def attachment_assign(request, patient_pk, pk):
    attachment = get_object_or_404(ClinicalAttachment, pk=pk, patient_id=patient_pk, note__isnull=True)
    note_id = request.POST.get('note', '')
    if not note_id.isdecimal():
        messages.error(request, 'Bitte eine Behandlung auswählen.')
        return redirect('clinical_record', patient_pk=patient_pk)
    note = get_object_or_404(TreatmentNote, pk=note_id, patient_id=patient_pk)
    # Only legacy, unassigned attachments can be assigned; assignment never changes note text.
    changed = ClinicalAttachment.objects.filter(pk=attachment.pk, note__isnull=True).update(note=note)
    if changed:
        messages.success(request, 'Vorhandener Anhang der Behandlung zugeordnet.')
    return redirect('note_detail' if note.finalized_at else 'note_edit', patient_pk=patient_pk, pk=note.pk)
