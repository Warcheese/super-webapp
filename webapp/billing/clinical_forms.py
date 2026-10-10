from pathlib import Path
from zipfile import ZipFile, BadZipFile
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
    file = forms.FileField(label='Bestehendes Word-Dokument',
        widget=forms.FileInput(attrs={'accept': '.docx'}),
        help_text='DOCX, maximal 10 MB. Das Original wird unverändert gespeichert; es wird nicht automatisch in Behandlungen aufgeteilt.')

    def clean_file(self):
        file = self.cleaned_data['file']
        name = Path(file.name.replace('\\', '/')).name
        if not name.lower().endswith('.docx') or len(name) > 200:
            raise forms.ValidationError('Bitte ein DOCX-Dokument mit Dateinamen bis 200 Zeichen auswählen.')
        if file.size > 10 * 1024 * 1024:
            raise forms.ValidationError('Das Dokument darf maximal 10 MB groß sein.')
        try:
            with ZipFile(file) as archive:
                entries = archive.infolist()
                names = {entry.filename for entry in entries}
                if not {'[Content_Types].xml', 'word/document.xml'}.issubset(names):
                    raise forms.ValidationError('Die Datei ist kein gültiges DOCX-Dokument.')
                if len(entries) > 2000 or sum(entry.file_size for entry in entries) > 50 * 1024 * 1024:
                    raise forms.ValidationError('Das entpackte Dokument ist zu groß.')
                if any(entry.flag_bits & 1 for entry in entries) or any('vbaproject' in name.lower() for name in names):
                    raise forms.ValidationError('Verschlüsselte Dokumente oder Dokumente mit Makros werden nicht unterstützt.')
                if archive.testzip() is not None:
                    raise forms.ValidationError('Das Dokument ist beschädigt.')
        except (BadZipFile, OSError, RuntimeError, NotImplementedError):
            raise forms.ValidationError('Die Datei ist kein lesbares DOCX-Dokument.')
        finally:
            file.seek(0)
        return file
