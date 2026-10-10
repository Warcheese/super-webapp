from pathlib import Path
from django import forms
from .forms import StyledModelForm
from .models import TreatmentNote, TreatmentAddendum


def validate_text(value):
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise forms.ValidationError("Der Text enthält ungültige Steuerzeichen.")
    return value


class TreatmentNoteForm(StyledModelForm):
    def clean_body(self):
        return validate_text(self.cleaned_data["body"])

    class Meta:
        model = TreatmentNote
        fields = ['date', 'body']
        widgets = {'body': forms.Textarea(attrs={'rows': 16, 'placeholder': 'Symptome, Befund, Behandlung und Verlauf …'})}


class AddendumForm(forms.ModelForm):
    def clean_body(self):
        return validate_text(self.cleaned_data["body"])

    confirm = forms.BooleanField(label='Nachtrag endgültig speichern')
    class Meta:
        model = TreatmentAddendum
        fields = ['body']
        widgets = {'body': forms.Textarea(attrs={'rows': 6})}


class ClinicalAttachmentForm(forms.Form):
    file = forms.FileField(label='Dateianhang',
        help_text='Beliebige Datei, maximal 20 MB. Das Original wird unverändert gespeichert.')

    def clean_file(self):
        file = self.cleaned_data['file']
        name = Path(file.name.replace(chr(92), '/')).name
        if not name or len(name) > 200 or any(ord(char) < 32 for char in name):
            raise forms.ValidationError('Bitte einen Dateinamen mit höchstens 200 Zeichen ohne Steuerzeichen verwenden.')
        if file.size > 20 * 1024 * 1024:
            raise forms.ValidationError('Die Datei darf maximal 20 MB groß sein.')
        return file
