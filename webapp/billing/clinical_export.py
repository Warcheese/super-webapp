"""Paginated PDF extracts of patient documentation, including draft labels and addenda."""
from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from .pdf import NumberedCanvas  # also registers the bundled SuperSans fonts


def export_documentation(patient, notes, attachments=()):
    output = BytesIO()
    notes = list(notes)
    identity = notes[0].patient_snapshot if len(notes) == 1 and notes[0].finalized_at else patient.snapshot()
    styles = {
        'title': ParagraphStyle('ClinicalTitle', fontName='SuperSans-Bold', fontSize=20, leading=26, spaceAfter=16),
        'heading': ParagraphStyle('ClinicalHeading', fontName='SuperSans-Bold', fontSize=13, leading=18, spaceBefore=18, spaceAfter=8, keepWithNext=True),
        'body': ParagraphStyle('ClinicalBody', fontName='SuperSans', fontSize=10, leading=16, spaceAfter=7),
        'meta': ParagraphStyle('ClinicalMeta', fontName='SuperSans', fontSize=8, leading=12, textColor=colors.HexColor('#52635c'), spaceAfter=10),
    }
    story = []

    def paragraph(text, style='body'):
        story.append(Paragraph(escape(str(text)).replace('\n', '<br/>'), styles[style]))

    def body(text):
        # Separate paragraphs can flow across pages even for long treatment entries.
        for line in text.splitlines():
            if line.strip():
                paragraph(line)
            else:
                story.append(Spacer(1, 5))

    paragraph('Behandlungsdokumentation', 'title')
    paragraph(f"Patient: {identity['last_name']}, {identity['first_name']}")
    if identity.get('birth_date'):
        paragraph('Geburtsdatum: '+date.fromisoformat(identity['birth_date']).strftime('%d.%m.%Y'))
    paragraph('Behandlungen und zugehörige Nachträge. Entwürfe sind ausdrücklich gekennzeichnet.', 'meta')
    for note in notes:
        paragraph(note.date.strftime('%d.%m.%Y')+(' - ENTWURF' if not note.finalized_at else ''), 'heading')
        if note.finalized_at:
            paragraph(f"Abgeschlossen am {timezone.localtime(note.finalized_at):%d.%m.%Y %H:%M} von {note.author_name}", 'meta')
        else:
            paragraph(f"Entwurf zuletzt gespeichert am {timezone.localtime(note.updated_at):%d.%m.%Y %H:%M}", 'meta')
        body(note.body or 'Noch kein Text vorhanden.')
        for addendum in note.addenda.all():
            paragraph('Nachtrag', 'heading')
            paragraph(f"{timezone.localtime(addendum.created_at):%d.%m.%Y %H:%M} - {addendum.author_name}", 'meta')
            body(addendum.body)
    if not notes:
        paragraph('Noch keine Behandlungen dokumentiert.')
    attachments = list(attachments)
    if attachments:
        paragraph('Dateianhänge', 'heading')
        paragraph('Die Originaldateien sind separat in der Patientenakte verfügbar. Dieser Auszug enthält nur ihre Dateinamen.', 'meta')
        for attachment in attachments:
            paragraph(f"{attachment.original_name} - hinzugefügt am {timezone.localtime(attachment.uploaded_at):%d.%m.%Y %H:%M}")

    def footer(canvas, pages):
        canvas.saveState()
        canvas.setFont('SuperSans', 8)
        canvas.setFillColor(colors.HexColor('#52635c'))
        canvas.drawRightString(A4[0]-22*mm, 14*mm, f'Seite {canvas._pageNumber} von {pages}')
        canvas.restoreState()

    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=22*mm, rightMargin=22*mm,
        topMargin=20*mm, bottomMargin=24*mm, title='Behandlungsdokumentation', author='SUPER')
    document.build(story, canvasmaker=lambda *args, **kwargs: NumberedCanvas(*args, footer=footer, **kwargs))
    return output.getvalue()
