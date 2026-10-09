from django import forms
from django.forms import inlineformset_factory
from django.db.models import Q
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from io import BytesIO
from PIL import Image, ImageOps, UnidentifiedImageError
from .models import Invoice, InvoiceItem, Patient, Practice, ServiceTemplate


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})
                field.input_formats = ['%Y-%m-%d', '%d.%m.%Y']


class PatientForm(StyledModelForm):
    class Meta:
        model = Patient
        fields = ['title', 'first_name', 'last_name', 'street', 'city', 'birth_date', 'phone', 'active']


class PracticeForm(StyledModelForm):
    logo = forms.FileField(label='Firmenlogo (PNG oder JPEG)', required=False,
        widget=forms.FileInput(attrs={'accept': 'image/png,image/jpeg'}),
        help_text='Optional, maximal 2 MB und 4 Millionen Pixel. Ohne Upload erscheint kein Logo auf der Rechnung.')
    remove_logo = forms.BooleanField(label='Vorhandenes Logo entfernen', required=False)

    class Meta:
        model = Practice
        fields = ['name', 'profession', 'street', 'city', 'phone', 'bank', 'iban', 'bic', 'tax_text', 'payment_days',
                  'logo', 'developer_greetings', 'greeting_messages']
        widgets = {'greeting_messages': forms.Textarea(attrs={'rows': 4})}

    def clean_logo(self):
        file = self.cleaned_data.get('logo')
        if not isinstance(file, UploadedFile):
            return file
        if file.size > 2 * 1024 * 1024:
            raise forms.ValidationError('Das Logo darf maximal 2 MB groß sein.')
        try:
            with Image.open(file) as image:
                if image.format not in {'PNG', 'JPEG'}:
                    raise forms.ValidationError('Bitte ein PNG- oder JPEG-Logo hochladen.')
                if image.width * image.height > 4_000_000:
                    raise forms.ValidationError('Das Logo darf maximal 4 Millionen Pixel enthalten.')
                image.load()
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1200, 1200))
                image = image.convert('RGBA' if 'A' in image.getbands() else 'RGB')
                normalized = BytesIO()
                image.save(normalized, format='PNG')
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise forms.ValidationError('Diese Datei kann nicht als Logo gelesen werden.') from exc
        return ContentFile(normalized.getvalue(), name='logo.png')

    def clean(self):
        data = super().clean()
        if data.get('remove_logo'):
            if isinstance(self.files.get('logo'), UploadedFile):
                self.add_error('logo', 'Bitte entweder ein neues Logo hochladen oder das vorhandene entfernen.')
            else:
                data['logo'] = False
        return data


class ServiceForm(StyledModelForm):
    class Meta:
        model = ServiceTemplate
        fields = ['description', 'code', 'price', 'active']


class InvoiceForm(StyledModelForm):
    class Meta:
        model = Invoice
        fields = ['patient', 'date', 'invoice_type', 'payment_method', 'cash_date', 'diagnosis', 'beihilfe']
        widgets = {'diagnosis': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['patient'].queryset = Patient.objects.filter(active=True)
        if self.instance.pk:
            self.fields['patient'].queryset = Patient.objects.filter(
                Q(active=True) | Q(pk=self.instance.patient_id))

    def clean(self):
        data = super().clean()
        if data.get('payment_method') == 'cash' and not data.get('cash_date'):
            self.add_error('cash_date', 'Bitte das Datum der Barzahlung angeben.')
        elif data.get('payment_method') != 'cash':
            data['cash_date'] = None
        return data


class ItemForm(StyledModelForm):
    class Meta:
        model = InvoiceItem
        fields = ['date', 'code', 'description', 'quantity', 'price']


ItemFormSet = inlineformset_factory(Invoice, InvoiceItem, form=ItemForm, extra=1,
                                   can_delete=True, max_num=100, validate_max=True,
                                   absolute_max=110)


class SequenceForm(forms.Form):
    next_number = forms.IntegerField(label='Nächste Rechnungsnummer im laufenden Jahr', min_value=1, max_value=999999)


class CSVImportForm(forms.Form):
    file = forms.FileField(label='CSV-Datei', widget=forms.ClearableFileInput(attrs={'accept': '.csv'}))

    def clean_file(self):
        file = self.cleaned_data['file']
        if file.size > 2 * 1024 * 1024:
            raise forms.ValidationError('Maximal 2 MB pro CSV-Datei.')
        return file
