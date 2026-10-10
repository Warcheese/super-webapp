import hashlib
import logging
from datetime import timedelta, date
from urllib.parse import parse_qsl, urlencode
from pathlib import Path
from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import FileResponse, HttpResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .forms import PatientForm, InvoiceForm, ItemFormSet, PracticeForm, ServiceForm, SequenceForm, CSVImportForm, CancellationForm
from .models import Patient, Invoice, Practice, ServiceTemplate, NumberSequence, LoginAttempt
from .services import finalize_invoice, frozen_preview, cancel_invoice
from .pdf import render_pdf
from .importers import import_patients, import_templates, import_items
from .greetings import random_greeting
from .sorting import sort_records, text

logger = logging.getLogger(__name__)


class SuperLoginView(LoginView):
    template_name = 'registration/login.html'
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        # REMOTE_ADDR is intentional: untrusted forwarded headers must not bypass throttling.
        self.attempt_key = hashlib.sha256(request.META.get('REMOTE_ADDR', '').encode()).hexdigest()
        attempt = LoginAttempt.objects.filter(key=self.attempt_key).first()
        if attempt and attempt.failures >= 5 and attempt.updated_at > timezone.now()-timedelta(minutes=15):
            form = self.get_form()
            form.add_error(None, 'Zu viele Anmeldeversuche. Bitte in 15 Minuten erneut versuchen.')
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        with transaction.atomic():
            attempt, _ = LoginAttempt.objects.get_or_create(key=self.attempt_key)
            if attempt.updated_at < timezone.now()-timedelta(minutes=15):
                attempt.failures = 0
            attempt.failures += 1
            attempt.updated_at = timezone.now()
            attempt.save()
        return super().form_invalid(form)

    def form_valid(self, form):
        LoginAttempt.objects.filter(key=self.attempt_key).delete()
        return super().form_valid(form)


@login_required
def dashboard(request):
    invoices = Invoice.objects.select_related('patient', 'cancellation', 'cancellation_of', 'correction_of').prefetch_related('items')
    open_invoices = invoices.filter(number__isnull=False, paid_at__isnull=True, cancellation__isnull=True, cancellation_of__isnull=True)
    practice = Practice.objects.filter(pk=1).first()
    return render(request, 'billing/dashboard.html', {
        'recent': invoices[:6], 'patients_count': Patient.objects.filter(active=True).count(),
        'greeting': random_greeting() if practice and practice.developer_greetings else '',
        'drafts_count': invoices.filter(number__isnull=True).count(),
        'open_count': open_invoices.count(), 'open_total': sum(i.total for i in open_invoices),
        'nav': 'dashboard'})


@login_required
def patients(request):
    query = request.GET.get('q', '').strip()
    records = Patient.objects.all()
    if query:
        records = records.filter(Q(first_name__icontains=query)|Q(last_name__icontains=query)|Q(city__icontains=query))
    records, sorting = sort_records(records, request, {
        'name': lambda p: (text(p.last_name), text(p.first_name)),
        'birth_date': lambda p: p.birth_date or date.min, 'city': lambda p: text(p.city),
        'phone': lambda p: text(p.phone), 'status': lambda p: 'Aktiv' if p.active else 'Archiviert'})
    return render(request, 'billing/patients.html', {'patients': records[:200], 'query': query, 'nav': 'patients', **sorting})


@login_required
def patient_edit(request, pk=None):
    obj = get_object_or_404(Patient, pk=pk) if pk else None
    form = PatientForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        messages.success(request, 'Patientendaten gespeichert.')
        return redirect('patient_detail', pk=obj.pk)
    return render(request, 'billing/form.html', {'form': form, 'title': 'Patient bearbeiten' if pk else 'Patient anlegen', 'nav': 'patients'})


@login_required
def patient_detail(request, pk):
    patient = get_object_or_404(Patient, pk=pk)
    return render(request, 'billing/patient_detail.html', {'patient': patient,
        'invoices': patient.invoices.prefetch_related('items'), 'nav': 'patients'})


@login_required
def invoices(request):
    records = Invoice.objects.select_related('patient', 'cancellation', 'cancellation_of', 'correction_of').prefetch_related('items')
    status = request.GET.get('status', '')
    if status == 'draft':
        records = records.filter(number__isnull=True)
    elif status == 'open':
        records = records.filter(number__isnull=False, paid_at__isnull=True, cancellation__isnull=True, cancellation_of__isnull=True)
    elif status == 'paid':
        records = records.filter(paid_at__isnull=False, cancellation__isnull=True, cancellation_of__isnull=True)
    elif status == 'cancelled':
        records = records.filter(Q(cancellation__isnull=False) | Q(cancellation_of__isnull=False))
    query = request.GET.get('q', '').strip()
    if query:
        current_name = Q()
        saved_name = Q()
        for part in query.split():
            current_name &= Q(patient__first_name__icontains=part) | Q(patient__last_name__icontains=part)
            saved_name &= Q(patient_snapshot__first_name__icontains=part) | Q(patient_snapshot__last_name__icontains=part)
        records = records.filter(Q(number__icontains=query) | current_name | saved_name)
    records, sorting = sort_records(records, request, {
        'number': lambda i: (i.date.year, int(i.number.rsplit('-', 1)[-1])) if i.number else (0, 0),
        'patient': lambda i: text(i.recipient), 'date': lambda i: i.date,
        'status': lambda i: text(i.status), 'total': lambda i: i.total})
    return render(request, 'billing/invoices.html', {'invoices': records[:200], 'query': query, 'status': status, 'nav': 'invoices', **sorting})


@login_required
def invoice_edit(request, pk=None):
    with transaction.atomic():
        obj = get_object_or_404(Invoice, pk=pk) if pk else Invoice()
        if obj.number:
            return redirect('invoice_detail', pk=obj.pk)
        initial = {'patient': request.GET.get('patient')} if not pk else None
        form = InvoiceForm(request.POST or None, instance=obj, initial=initial)
        formset = ItemFormSet(request.POST or None, instance=obj, prefix='items')
        if request.method == 'POST':
            valid = form.is_valid()
            items_valid = formset.is_valid()
            try:
                submitted_revision = int(request.POST.get('revision', '-1'))
            except ValueError:
                submitted_revision = -1
            if submitted_revision != obj.revision:
                form.add_error(None, 'Der Entwurf wurde inzwischen geändert. Bitte die Seite neu laden.')
            elif valid and items_valid:
                obj = form.save(commit=False)
                obj.revision += 1
                obj.save()
                formset.instance = obj
                formset.save()
                messages.success(request, 'Entwurf gespeichert. Du kannst ihn jetzt prüfen.')
                return redirect('invoice_detail', pk=obj.pk)
    templates = [{'id': t.pk, 'code': t.code, 'description': t.description, 'price': str(t.price)}
                 for t in ServiceTemplate.objects.filter(active=True)]
    return render(request, 'billing/invoice_edit.html', {'form': form, 'formset': formset, 'invoice': obj,
        'templates_data': templates, 'nav': 'invoices'})


@login_required
def invoice_detail(request, pk):
    obj = get_object_or_404(Invoice.objects.select_related('patient', 'cancellation', 'cancellation_of', 'correction_of').prefetch_related('items'), pk=pk)
    return render(request, 'billing/invoice_detail.html', {'invoice': obj, 'nav': 'invoices'})


@login_required
@require_POST
def invoice_finalize(request, pk):
    get_object_or_404(Invoice, pk=pk)
    try:
        revision = int(request.POST.get('revision', '-1'))
        finalize_invoice(pk, revision)
        messages.success(request, 'Rechnung erstellt und unveränderlich gespeichert.')
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
    except ValueError:
        messages.error(request, 'Bitte den Entwurf erneut öffnen.')
    except Exception:
        logger.exception('Rechnungserstellung fehlgeschlagen, invoice_id=%s', pk)
        messages.error(request, 'Die PDF-Erstellung ist fehlgeschlagen. Der Entwurf bleibt erhalten; bitte erneut versuchen.')
    return redirect('invoice_detail', pk=pk)


@login_required
@require_POST
def invoice_paid(request, pk):
    with transaction.atomic():
        obj = get_object_or_404(Invoice, pk=pk)
        if obj.cancellation_of_id or hasattr(obj, 'cancellation'):
            messages.error(request, 'Stornierte Rechnungen können nicht als bezahlt markiert werden.')
        elif not obj.number:
            messages.error(request, 'Bitte zuerst die Rechnung erstellen.')
        else:
            if not obj.paid_at:
                obj.paid_at = timezone.localdate()
                obj.save(update_fields=['paid_at'])
            messages.success(request, 'Rechnung als bezahlt markiert.')
    return redirect('invoice_detail', pk=pk)


@login_required
def invoice_pdf(request, pk):
    obj = get_object_or_404(Invoice.objects.select_related('patient', 'cancellation', 'cancellation_of', 'correction_of'), pk=pk)
    if not obj.number:
        response = HttpResponse(render_pdf(frozen_preview(obj), preview=True), content_type='application/pdf')
        response['Content-Disposition'] = 'inline; filename="SUPER-Entwurf.pdf"'
    else:
        duplicate = request.GET.get('copy') == '1'
        relative = obj.copy_path if duplicate else obj.pdf_path
        if not relative:
            raise Http404('Keine Zweitschrift vorhanden.')
        root = Path(django_settings.DATA_DIR).resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root / 'pdfs') or not path.is_file():
            raise Http404('PDF nicht gefunden. Bitte die Datensicherung prüfen.')
        data = path.read_bytes()
        digest = obj.copy_sha256 if duplicate else obj.pdf_sha256
        if hashlib.sha256(data).hexdigest() != digest:
            logger.error('PDF-Prüfsumme stimmt nicht, invoice_id=%s', pk)
            return HttpResponse('PDF-Prüfsumme stimmt nicht. Bitte die Datensicherung prüfen.', status=409)
        from io import BytesIO
        response = FileResponse(BytesIO(data), content_type='application/pdf', as_attachment=True,
                                filename=f'{obj.number}{"-Zweitschrift" if duplicate else ""}.pdf')
    response['Cache-Control'] = 'no-store'
    return response


@login_required
def practice_settings(request):
    obj, _ = Practice.objects.get_or_create(pk=1)
    form = PracticeForm(request.POST or None, request.FILES or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Praxisdaten gespeichert. Fertige Rechnungen bleiben unverändert.')
        return redirect('settings')
    sequence, _ = NumberSequence.objects.get_or_create(year=timezone.localdate().year)
    return render(request, 'billing/settings.html', {'form': form, 'practice': obj,
        'sequence_form': SequenceForm(initial={'next_number': sequence.next_number}), 'nav': 'settings'})


@login_required
def practice_logo(request):
    practice = get_object_or_404(Practice, pk=1)
    if not practice.logo:
        raise Http404('Kein Logo vorhanden.')
    try:
        response = FileResponse(practice.logo.open('rb'), content_type='image/png')
    except OSError:
        raise Http404('Logo nicht gefunden.')
    response['Cache-Control'] = 'no-store, private'
    return response


@login_required
@require_POST
def sequence_update(request):
    form = SequenceForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            sequence, _ = NumberSequence.objects.get_or_create(year=timezone.localdate().year)
            number = form.cleaned_data['next_number']
            if number < sequence.next_number:
                messages.error(request, 'Die Nummer darf nicht zurückgesetzt werden. Bereits vergebene Nummern bleiben reserviert.')
            else:
                sequence.next_number = number
                sequence.save()
                messages.success(request, 'Nächste Rechnungsnummer aktualisiert.')
    else:
        messages.error(request, 'Bitte eine gültige positive Rechnungsnummer angeben.')
    return redirect('settings')


@login_required
def templates(request):
    records, sorting = sort_records(ServiceTemplate.objects.all(), request, {
        'description': lambda t: text(t.description), 'code': lambda t: text(t.code),
        'price': lambda t: t.price, 'status': lambda t: 'Aktiv' if t.active else 'Archiviert'})
    return render(request, 'billing/templates.html', {'templates': records, 'nav': 'templates', **sorting})


@login_required
def template_edit(request, pk=None):
    obj = get_object_or_404(ServiceTemplate, pk=pk) if pk else None
    form = ServiceForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Leistungsvorlage gespeichert.')
        return redirect('templates')
    return render(request, 'billing/form.html', {'form': form, 'title': 'Leistungsvorlage', 'nav': 'templates'})


@login_required
def csv_import(request, kind, pk=None):
    if kind not in ['patients', 'templates', 'items']:
        raise Http404
    obj = get_object_or_404(Invoice, pk=pk) if kind == 'items' else None
    if obj and obj.number:
        return redirect('invoice_detail', pk=pk)
    form = CSVImportForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            data = form.cleaned_data['file'].read()
            with transaction.atomic():
                if kind == 'items':
                    obj = get_object_or_404(Invoice, pk=pk)
                    if obj.number:
                        raise ValidationError('Fertige Rechnungen können nicht geändert werden.')
                    count = import_items(obj, data)
                else:
                    count = import_patients(data) if kind == 'patients' else import_templates(data)
            messages.success(request, f'{count} Einträge übernommen.')
            return redirect('invoice_detail', pk=pk) if kind == 'items' else redirect(kind)
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
    return render(request, 'billing/import.html', {'form': form, 'kind': kind, 'invoice': obj, 'nav': 'settings'})


@login_required
def patient_delete(request, pk):
    obj = get_object_or_404(Patient, pk=pk)
    if request.method == 'POST' and request.POST.get('confirm') == 'yes':
        try:
            with transaction.atomic():
                obj.delete()
        except ProtectedError:
            messages.error(request, 'Der Patient hat noch Rechnungen. Bitte diese zuerst löschen; danach kannst du den Patienten löschen.')
            return redirect('patient_detail', pk=pk)
        messages.success(request, 'Patient gelöscht.')
        return redirect('patients')
    return render(request, 'billing/delete_confirm.html', {'title': 'Patient löschen', 'object_name': str(obj),
        'warning': 'Patientendaten werden dauerhaft gelöscht. Bestehende Rechnungen müssen zuerst gelöscht werden.',
        'cancel_url': reverse('patient_detail', args=[pk]), 'nav': 'patients'})


@login_required
def invoice_delete(request, pk):
    obj = get_object_or_404(Invoice, pk=pk)
    linked = Invoice.objects.filter(Q(cancellation_of_id=pk) | Q(correction_of_id=pk))
    if request.method == 'POST' and linked.exists():
        messages.error(request, 'Bitte zuerst die verknüpften Storno- und Korrekturrechnungen löschen.')
        return redirect('invoice_delete', pk=pk)
    if request.method == 'POST' and request.POST.get('confirm') == 'yes':
        with transaction.atomic():
            obj = get_object_or_404(Invoice, pk=pk)
            root = Path(django_settings.DATA_DIR).resolve()
            paths = []
            for relative in [obj.pdf_path, obj.copy_path]:
                if relative:
                    path = (root / relative).resolve()
                    if path.is_relative_to(root / 'pdfs'):
                        paths.append(path)
            try:
                obj.delete()
            except ProtectedError:
                messages.error(request, 'Bitte zuerst die verknüpften Storno- und Korrekturrechnungen löschen.')
                return redirect('invoice_delete', pk=pk)
            def remove_pdfs():
                for path in paths:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        logger.exception('Gelöschte Rechnungsdatei konnte nicht entfernt werden.')
            transaction.on_commit(remove_pdfs)
        messages.success(request, 'Rechnung gelöscht. Bereits vergebene Rechnungsnummern werden nicht zurückgesetzt.')
        return redirect('invoices')
    return render(request, 'billing/delete_confirm.html', {'title': 'Rechnung löschen',
        'linked_invoices': linked, 'delete_blocked': linked.exists(), 'is_cancellation': bool(obj.cancellation_of_id),
        'object_name': f'{obj.number or "Entwurf"} · {obj.recipient}',
        'warning': 'Bist du dir sicher? Die Rechnung, ihre Leistungen und gespeicherten PDFs werden dauerhaft gelöscht. Diese Aktion kann nicht rückgängig gemacht werden.',
        'cancel_url': reverse('invoice_detail', args=[pk]), 'nav': 'invoices'})


@login_required
def template_delete(request, pk):
    obj = get_object_or_404(ServiceTemplate, pk=pk)
    if request.method == 'POST' and request.POST.get('confirm') == 'yes':
        obj.delete()
        messages.success(request, 'Leistungsvorlage gelöscht. Leistungen in bestehenden Rechnungen bleiben erhalten.')
        return redirect('templates')
    return render(request, 'billing/delete_confirm.html', {'title': 'Leistungsvorlage löschen', 'object_name': str(obj),
        'warning': 'Die Vorlage wird dauerhaft gelöscht. Bestehende Rechnungen bleiben unverändert.',
        'cancel_url': reverse('template_edit', args=[pk]), 'nav': 'templates'})


@login_required
def invoice_cancel(request, pk, correct=False):
    obj = get_object_or_404(Invoice, pk=pk)
    if not obj.number or obj.cancellation_of_id or hasattr(obj, 'cancellation'):
        messages.error(request, 'Diese Rechnung kann nicht storniert werden.')
        return redirect('invoice_detail', pk=pk)
    form = CancellationForm(request.POST or None, paid=bool(obj.paid_at),
        initial={'settlement': 'pending' if obj.paid_at else ''})
    if request.method == 'POST' and form.is_valid():
        try:
            cancellation, draft = cancel_invoice(pk, reason=form.cleaned_data['reason'], correct=correct,
                settlement=form.cleaned_data['settlement'], settlement_note=form.cleaned_data['settlement_note'])
            messages.success(request, 'Stornorechnung erstellt. Die Originalrechnung bleibt erhalten.')
            return redirect('invoice_edit', pk=draft.pk) if draft else redirect('invoice_detail', pk=cancellation.pk)
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
        except Exception:
            logger.exception('Stornierung fehlgeschlagen, invoice_id=%s', pk)
            form.add_error(None, 'Die Stornierung ist fehlgeschlagen. Die Originalrechnung bleibt unverändert; bitte erneut versuchen.')
    return render(request, 'billing/cancel.html', {'invoice': obj, 'form': form, 'correct': correct, 'nav': 'invoices'})


@login_required
@require_POST
def invoice_settlement(request, pk):
    obj = get_object_or_404(Invoice, pk=pk, cancellation_of__isnull=False)
    if not obj.cancellation_of.paid_at:
        raise Http404
    form = CancellationForm(request.POST, paid=True)
    # Settlement update is separate from the frozen cancellation PDF.
    form.fields.pop('reason')
    form.fields.pop('confirm')
    if form.is_valid():
        obj.settlement = form.cleaned_data['settlement']
        obj.settlement_note = form.cleaned_data['settlement_note']
        obj.save(update_fields=['settlement', 'settlement_note'])
        messages.success(request, 'Rückzahlung / Verrechnung aktualisiert. Das gespeicherte PDF bleibt unverändert.')
    else:
        messages.error(request, 'Bitte gültige Angaben zur Rückzahlung / Verrechnung eingeben.')
    return redirect('invoice_detail', pk=pk)


@login_required
@require_POST
def patient_toggle(request, pk):
    return toggle_record(request, Patient, pk, 'patients')


@login_required
@require_POST
def template_toggle(request, pk):
    return toggle_record(request, ServiceTemplate, pk, 'templates')


def toggle_record(request, model, pk, destination):
    obj = get_object_or_404(model, pk=pk)
    active = request.POST.get('active')
    if active not in ['0', '1']:
        return HttpResponse('Ungültiger Status.', status=400)
    model.objects.filter(pk=obj.pk).update(active=active == '1')
    messages.success(request, 'Status auf Aktiv gesetzt.' if active == '1' else 'Status auf Archiviert gesetzt.')
    params = [(key, value) for key, value in parse_qsl(request.POST.get('list_query', ''))
              if key in ['q', 'sort', 'direction', 'status']]
    url = reverse(destination)
    return redirect(url + ('?' + urlencode(params) if params else ''))
