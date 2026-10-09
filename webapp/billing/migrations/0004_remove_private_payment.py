from django.db import migrations, models


def convert_private_payment(apps, schema_editor):
    Invoice = apps.get_model('billing', 'Invoice')
    Invoice.objects.using(schema_editor.connection.alias).filter(payment_method='private').update(payment_method='transfer')


class Migration(migrations.Migration):
    dependencies = [('billing', '0003_practice_developer_greetings_and_more')]
    operations = [
        migrations.RunPython(convert_private_payment, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='invoice', name='payment_method',
            field=models.CharField('Zahlungsart', choices=[('transfer', 'Überweisung'), ('cash', 'Barzahlung')], max_length=10, default='transfer'),
        ),
    ]
