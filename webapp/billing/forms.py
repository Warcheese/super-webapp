from django import forms
from django.forms import inlineformset_factory
from django.db.models import Q
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
    class Meta:
        model = Practice
        fields = ['name', 'profession', 'street', 'city', 'phone', 'bank', 'iban', 'bic', 'tax_text', 'payment_days']


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
