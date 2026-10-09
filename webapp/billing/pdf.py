"""Generate PDFs from a frozen invoice snapshot; no external compiler required."""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ReportLab ships these fonts: identical typography in Docker and development.
_fonts = Path(reportlab.__file__).parent / 'fonts'
pdfmetrics.registerFont(TTFont('SuperSans', str(_fonts / 'Vera.ttf')))
pdfmetrics.registerFont(TTFont('SuperSans-Bold', str(_fonts / 'VeraBd.ttf')))
pdfmetrics.registerFontFamily('SuperSans', normal='SuperSans', bold='SuperSans-Bold')


def money(value):
    return f'{value:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.') + ' €'


def render_pdf(invoice, *, duplicate=False, preview=False):
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm,
                            topMargin=20*mm, bottomMargin=28*mm,
                            title=f'Rechnung {invoice.number or "Entwurf"}')
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        if hasattr(style, 'fontName'):
            style.fontName = 'SuperSans-Bold' if style.fontName.endswith('Bold') else 'SuperSans'
    styles.add(ParagraphStyle('SmallSuper', parent=styles['Normal'], fontSize=8, leading=11))
    styles.add(ParagraphStyle('RightSuper', parent=styles['Normal'], alignment=TA_RIGHT))
    styles['Normal'].leading = 14
    styles['Heading1'].textColor = colors.HexColor('#154e46')

    def p(text, style='Normal'):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), styles[style])

    patient, practice = invoice.patient_snapshot, invoice.practice_snapshot
    title = 'ENTWURF · Vorschau' if preview else 'RECHNUNG'
    if duplicate:
        title += '\nZweitschrift zur Vorlage bei der Beihilfe'
    story = [p(practice['name'], 'Heading1'), p(practice['profession']),
             p(f"{practice['street']}\n{practice['city']}\n{practice['phone']}", 'SmallSuper'), Spacer(1, 12*mm)]
    recipient = '\n'.join(filter(None, [patient['title'],
        f"{patient['first_name']} {patient['last_name']}", patient['street'], patient['city']]))
    born = patient.get('birth_date', '')
    if born:
        from datetime import date
        born = date.fromisoformat(born).strftime('%d.%m.%Y')
    info = f"Rechnungsnummer: {invoice.number or 'Noch nicht vergeben'}\nDatum: {invoice.date:%d.%m.%Y}"
    if born:
        info += f'\nGeburtsdatum: {born}'
    addresses = Table([[p(recipient), p(info, 'RightSuper')]], colWidths=[90*mm, 80*mm])
    addresses.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)]))
    story.extend([addresses, Spacer(1, 12*mm), p(title, 'Heading1'),
                  p('Ich erlaube mir, Ihnen die folgenden Leistungen in Rechnung zu stellen.'), Spacer(1, 4*mm)])
    if invoice.diagnosis:
        story.extend([p('Diagnose(n): ' + invoice.diagnosis), Spacer(1, 4*mm)])
    private = invoice.invoice_type == 'private'
    headers = ['Datum'] + (['Ziffer'] if private else []) + ['Leistung', 'Anzahl', 'Einzelpreis', 'Summe']
    widths = [23*mm] + ([15*mm] if private else []) + ([64*mm] if private else [79*mm]) + [17*mm,25*mm,26*mm]
    rows = [[p(h, 'SmallSuper') for h in headers]]
    for item in invoice.items.all():
        quantity = format(item.quantity.normalize(), 'f').replace('.', ',')
        cells = [item.date.strftime('%d.%m.%Y')] + ([item.code] if private else []) + [item.description,quantity,money(item.price),money(item.total)]
        rows.append([p(c, 'SmallSuper') for c in cells])
    rows.append([p('Gesamtbetrag', 'SmallSuper')] + ['']*(len(headers)-2) + [p(money(invoice.total), 'SmallSuper')])
    table = LongTable(rows, colWidths=widths, repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8f1ee')),
                              ('SPAN',(0,-1),(len(headers)-2,-1)),
                              ('LINEBELOW',(0,0),(-1,0),0.5,colors.HexColor('#8da8a0')),
                              ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),8),
                              ('BOTTOMPADDING',(0,0),(-1,-1),8),
                              ('BACKGROUND',(0,-1),(-1,-1),colors.HexColor('#e8f1ee'))]))
    story.extend([table, Spacer(1, 4*mm), p(practice['tax_text'], 'SmallSuper'), Spacer(1, 8*mm)])
    if invoice.payment_method == 'cash':
        text = f'Rechnungsbetrag in bar dankend am {invoice.cash_date:%d.%m.%Y} erhalten.'
    else:
        text = f"Bitte überweisen Sie den Rechnungsbetrag innerhalb von {practice['payment_days']} Tagen an das unten angegebene Konto."
        if invoice.payment_method == 'private':
            text = 'Unabhängig von der Erstattung durch Beihilfe oder private Krankenversicherung ist der Rechnungsbetrag in voller Höhe zu zahlen. ' + text
    story.extend([p(text), Spacer(1, 8*mm), p(f"Mit freundlichen Grüßen\n{practice['name']}")])

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont('SuperSans', 8)
        canvas.setFillColor(colors.HexColor('#54615c'))
        canvas.drawString(20*mm, 17*mm, practice['name'])
        bank_line = ' · '.join(filter(None, [practice['bank'], 'IBAN: '+practice['iban'] if practice['iban'] else '', practice['bic']]))
        canvas.drawString(20*mm, 12*mm, bank_line)
        canvas.drawRightString(190*mm, 17*mm, f'Seite {document.page}')
        if document.page > 1:
            canvas.drawString(20*mm, 282*mm, f"{practice['name']} · Rechnung {invoice.number or 'Entwurf'}")
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
