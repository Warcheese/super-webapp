"""Plain letter-style invoices, based on the original SUPER layout."""
from datetime import date
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
import reportlab
from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, Table, TableStyle, Image, KeepTogether
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

_fonts = Path(reportlab.__file__).parent / 'fonts'
pdfmetrics.registerFont(TTFont('SuperSans', str(_fonts / 'Vera.ttf')))
pdfmetrics.registerFont(TTFont('SuperSans-Bold', str(_fonts / 'VeraBd.ttf')))
pdfmetrics.registerFontFamily('SuperSans', normal='SuperSans', bold='SuperSans-Bold')


def money(value, *, currency=True):
    result = f'{value:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    return result + (' €' if currency else '')


def logo_path(relative):
    if not relative:
        return None
    root = Path(settings.DATA_DIR).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root / 'logos') or not path.is_file():
        raise FileNotFoundError('Das gespeicherte Firmenlogo ist nicht verfügbar.')
    return path


class NumberedCanvas(canvas.Canvas):
    """Replay completed pages to add 'Seite n von m', including on page one."""
    def __init__(self, *args, footer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._super_states = []
        self._super_footer = footer

    def showPage(self):
        self._super_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        pages = len(self._super_states)
        for state in self._super_states:
            self.__dict__.update(state)
            if self._super_footer:
                self._super_footer(self, pages)
            super().showPage()
        super().save()


def render_pdf(invoice, *, duplicate=False, preview=False):
    output = BytesIO()
    patient, practice = invoice.patient_snapshot, invoice.practice_snapshot
    width, height = A4
    margin = 25.4 * mm
    body_width = width - 2 * margin
    styles = {
        'body': ParagraphStyle('BodySuper', fontName='SuperSans', fontSize=9.8, leading=12.5),
        'small': ParagraphStyle('SmallSuper', fontName='SuperSans', fontSize=6.8, leading=9),
        'header': ParagraphStyle('HeaderSuper', fontName='SuperSans', fontSize=9.4, leading=11.5),
        'meta': ParagraphStyle('MetaSuper', fontName='SuperSans', fontSize=7.8, leading=9.3, alignment=TA_RIGHT),
        'title': ParagraphStyle('TitleSuper', fontName='SuperSans-Bold', fontSize=12, leading=15),
        'table': ParagraphStyle('TableSuper', fontName='SuperSans', fontSize=9.3, leading=11.5),
        'amount': ParagraphStyle('AmountSuper', fontName='SuperSans', fontSize=9.3, leading=11.5, alignment=TA_RIGHT),
        'total': ParagraphStyle('TotalSuper', fontName='SuperSans-Bold', fontSize=9.3, leading=11.5, alignment=TA_RIGHT),
        'foot': ParagraphStyle('FootSuper', fontName='SuperSans', fontSize=8.6, leading=10.5),
        'tax': ParagraphStyle('TaxSuper', fontName='SuperSans', fontSize=8.8, leading=11.5, alignment=TA_RIGHT),
    }

    def p(text, style='body', *, bold=False):
        markup = escape(str(text)).replace('\n', '<br/>')
        if bold:
            markup = f'<b>{markup}</b>'
        return Paragraph(markup, styles[style])

    # The logo is optional, versioned and stored only in the persistent data folder.
    logo = logo_path(practice.get('logo', ''))
    image = ''
    if logo:
        image = Image(str(logo), kind='proportional', width=18*mm, height=20*mm, mask='auto')
        image.hAlign = 'LEFT'
    practice_lines = [f"<b>{escape(practice['name'])}</b>"]
    practice_lines += [escape(practice[key]) for key in ['profession','street','city','phone'] if practice.get(key)]
    practice_header = Paragraph('<br/>'.join(practice_lines), styles['header'])
    born = patient.get('birth_date', '')
    if born:
        born = date.fromisoformat(born).strftime('%d.%m.%Y')
    metadata = ['<b>RECHNUNGSNUMMER</b>', escape(invoice.number or 'Noch nicht vergeben')]
    if born:
        metadata += ['<b>GEBURTSDATUM</b>', escape(born)]
    metadata += ['<b>RECHNUNGSDATUM</b>', invoice.date.strftime('%d.%m.%Y')]
    right_header = Paragraph('<br/>'.join(metadata), styles['meta'])
    columns = ([20*mm, body_width-63*mm,43*mm] if logo else [body_width-43*mm,43*mm])
    row = [image, practice_header, right_header] if logo else [practice_header, right_header]
    header = Table([row], colWidths=columns)
    header.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),
                               ('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),
                               ('BOTTOMPADDING',(0,0),(-1,-1),0)]))
    _, header_height = header.wrap(body_width, height)
    doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=margin, leftMargin=margin,
                            topMargin=max(45*mm, header_height+18*mm), bottomMargin=27*mm,
                            title=f'Rechnung {invoice.number or "Entwurf"}', author=practice['name'])
    sender = ' • '.join(practice[key] for key in ['name','street','city'] if practice.get(key))
    sender_table = Table([[p(sender, 'small')]], colWidths=[min(85*mm,body_width)])
    sender_table.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),
                                     ('BOTTOMPADDING',(0,0),(-1,-1),3),('TOPPADDING',(0,0),(-1,-1),0),
                                     ('LINEBELOW',(0,0),(-1,-1),0.4,colors.black)]))
    sender_table.hAlign = 'LEFT'
    recipient = '\n'.join(filter(None, [patient['title'], f"{patient['first_name']} {patient['last_name']}", patient['street'], patient['city']]))
    title = 'ENTWURF · Vorschau' if preview else 'RECHNUNG'
    story = [sender_table, Spacer(1,2*mm),p(recipient),Spacer(1,16*mm),p(title,'title')]
    if duplicate:
        story += [Spacer(1,1*mm),p('Zweitschrift zur Vorlage bei der Beihilfe','small')]
    salutation = ' '.join(filter(None,[patient['title'],patient['first_name'],patient['last_name']]))
    story += [Spacer(1,2*mm),p(f'Sehr geehrte(r) {salutation},'),Spacer(1,1*mm),
              p('ich erlaube mir, Ihnen die unten aufgeführten Leistungen in Rechnung zu stellen.'),Spacer(1,10*mm)]
    if invoice.diagnosis:
        diagnosis = Paragraph('<b>Diagnose(n):</b> '+escape(invoice.diagnosis).replace('\n','<br/>'),styles['body'])
        story += [diagnosis,Spacer(1,3*mm)]

    private = invoice.invoice_type == 'private'
    if private:
        story += [p('Privatpreise gem. §§ 611 und 315 BGB','small'),Spacer(1,2*mm)]
    headers = ['Datum'] + (['Ziffer'] if private else []) + ['Leistungsbeschreibung','Summe €']
    widths = [22*mm] + ([17*mm] if private else []) + [body_width-(65 if private else 48)*mm,26*mm]
    rows = [[p(h,'amount' if n==len(headers)-1 else 'table') for n,h in enumerate(headers)]]
    for item in invoice.items.all():
        description = item.description
        if item.quantity != 1:
            quantity = format(item.quantity.normalize(),'f').replace('.',',')
            description = f'{description}\n{quantity} × {money(item.price)}'
        cells = [item.date.strftime('%d.%m.%Y')] + ([item.code] if private else []) + [description,money(item.total,currency=False)]
        rows.append([p(value,'amount' if n==len(cells)-1 else 'table') for n,value in enumerate(cells)])
    table = LongTable(rows, colWidths=widths, repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([('LINEBELOW',(0,0),(-1,0),0.4,colors.black),
                              ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),3),
                              ('RIGHTPADDING',(0,0),(-1,-1),3),('TOPPADDING',(0,0),(-1,-1),1),
                              ('BOTTOMPADDING',(0,0),(-1,-1),1)]))
    summary = Table([[p('Rechnungsbetrag','total'),p(money(invoice.total),'total')]],
                    colWidths=[body_width-26*mm,26*mm])
    summary.setStyle(TableStyle([('LINEABOVE',(0,0),(-1,0),0.5,colors.black),
                                ('TOPPADDING',(0,0),(-1,-1),4),('LEFTPADDING',(0,0),(-1,-1),3),
                                ('RIGHTPADDING',(0,0),(-1,-1),3)]))
    story += [table,KeepTogether([Spacer(1,5*mm),summary,Spacer(1,4*mm),p(practice['tax_text'],'tax')]),Spacer(1,14*mm)]
    if invoice.payment_method == 'cash':
        payment = f'Rechnungsbetrag in bar dankend am {invoice.cash_date:%d.%m.%Y} erhalten.'
    else:
        payment = f"Bitte überweisen Sie den Rechnungsbetrag innerhalb von {practice['payment_days']} Tagen an das unten angegebene Konto."
        if invoice.payment_method == 'private':
            story += [p('Die in meiner Rechnung enthaltenen analogen Leistungsziffern und deren entsprechenden Leistungsbeschreibungen zeigen die teilweise oder vollständige Ähnlichkeit mit durchgeführten und therapeutischen Leistungen des GebüH an. Unabhängig vom Erstattungsverhalten der Beihilfestellen und privaten Krankenversicherungen ist die Rechnung in voller Höhe zu zahlen.'),Spacer(1,4*mm)]
    story += [KeepTogether([p(payment,bold=True),Spacer(1,10*mm),p(f"Mit freundlichen Grüßen\n{practice['name']}")])]

    def draw_header(page_canvas, document):
        page_canvas.saveState()
        header.drawOn(page_canvas,margin,height-8*mm-header_height)
        page_canvas.restoreState()

    def draw_footer(page_canvas, total_pages):
        page_canvas.saveState()
        foot_name = ' - '.join(filter(None,[practice.get('profession'),practice['name']]))
        bank = ' - '.join(filter(None,[practice.get('bank'),f"IBAN: {practice['iban']}" if practice.get('iban') else '',f"BIC: {practice['bic']}" if practice.get('bic') else '']))
        footer = Table([[p(f'{foot_name}\n{bank}','foot'),p(f'Seite {page_canvas._pageNumber} von {total_pages}','meta')]],
                       colWidths=[body_width-30*mm,30*mm])
        footer.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'BOTTOM'),('LEFTPADDING',(0,0),(-1,-1),0),
                                   ('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),
                                   ('BOTTOMPADDING',(0,0),(-1,-1),0)]))
        _, foot_height = footer.wrap(body_width,20*mm)
        footer.drawOn(page_canvas,margin,12*mm)
        page_canvas.restoreState()

    doc.build(story,onFirstPage=draw_header,onLaterPages=draw_header,
              canvasmaker=lambda *args,**kwargs:NumberedCanvas(*args,footer=draw_footer,**kwargs))
    return output.getvalue()
