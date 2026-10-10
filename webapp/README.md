# SUPER WebApp

Lokaler Rechnungsmanager mit deutscher Oberfläche, Django, SQLite und direkter PDF-Erzeugung. Läuft als Docker-Container auf Unraid. Das ursprüngliche CLI-Programm bleibt separat im Repository `Warcheese/super`.

## Funktionen

- Anmeldung mit Django-Passwortschutz, CSRF-Schutz und Begrenzung fehlgeschlagener Anmeldeversuche.
- Patienten suchen, anlegen, ändern und archivieren; stabile Datenbank-IDs.
- Leistungsvorlagen mit Ziffer und Preis verwalten.
- Rechnungsentwürfe mit Patientenzuordnung, Diagnose, Leistungsdatum, Anzahl, Preis und Zahlungsart.
- PDF-Vorschau und endgültige Rechnung; optionale Beihilfe-Zweitschrift.
- Jahresweise Rechnungsnummern, Dezimalberechnung, eingefrorene Patientendaten und Praxisdaten.
- Unveränderliche fertige Rechnungen in der WebApp, PDF-Prüfsummen, Zahlungsstatus.
- Übernahme der bisherigen Patienten-, Vorlagen- und Rechnungs-CSVs.
- Responsive Oberfläche ohne externe Schriftarten, CDNs oder externe Dienste.
- Optionales Firmenlogo (PNG/JPEG) und Brief-Layout nach der ursprünglichen Rechnungsvorlage.
- „Grüße vom Entwickler“ standardmäßig deaktiviert; 59 zufällige Originalnachrichten.

## Unraid: erster Start

Die Einrichtung erfordert einmal das Unraid-Terminal; die tägliche Bedienung erfolgt vollständig im Browser. Alternativ kannst du später das lokal gebaute Image über ein Unraid-Docker-Template verwalten.

1. Dieses Repository lokal oder auf Unraid auschecken. Bei einem privaten Repository GitHub-Anmeldung verwenden; keine Zugangstokens in Skripte oder Compose-Dateien schreiben.
2. Im Ordner `webapp` die Datei `.env.example` nach `.env` kopieren.
3. `DJANGO_SECRET_KEY` mit einem zufälligen Wert füllen. Erzeugen z.B. mit:

   ```sh
   python -c "import secrets; print(secrets.token_urlsafe(64))"
   ```

4. `SUPER_DATA_PATH` auf einen **direkten lokalen Pool-Pfad** setzen. Wenn dein Pool `appdata` heißt: `/mnt/appdata/super`. Nicht in einen über Nextcloud synchronisierten Ordner legen. Bei anderen Poolnamen den Pfad anpassen.
5. Den Datenordner anlegen und dem Containerbenutzer UID/GID 1000 Schreibzugriff geben:

   ```sh
   mkdir -p /mnt/appdata/super
   chown 1000:1000 /mnt/appdata/super
   chmod 700 /mnt/appdata/super
   ```

   Diese Befehle betreffen ausschließlich den neuen SUPER-Ordner. Bestehende Appdata-Verzeichnisse nicht pauschal umberechtigen.

6. `DJANGO_ALLOWED_HOSTS` auf die tatsächlich verwendete IP/Hostnamen setzen. Für Solstice beispielsweise `192.168.178.39,localhost,127.0.0.1`. Hier keine Protokolle oder Ports eintragen.
7. Mit Docker Compose starten. Falls `docker compose` auf deiner Unraid-Installation nicht verfügbar ist, benötigst du eine Compose-Verwaltung oder musst das Image separat bauen und als Docker-Container konfigurieren.

   ```sh
   docker compose up -d --build
   docker compose exec super python manage.py createsuperuser
   ```

8. `http://192.168.178.39:8088` öffnen und mit dem gerade angelegten Benutzer anmelden.
9. Unter **Einstellungen** Praxisadresse, Bankverbindung und nächste freie Rechnungsnummer eintragen. Für den geprüften alten Stand war diese Nummer 160; vor dem Umstieg den aktuellen Wert verwenden.
10. Patienten und Leistungsvorlagen über **Einstellungen → Bisherige Daten übernehmen** importieren oder neu anlegen.

### Unraid Docker-Maske ohne laufende Compose-Verwaltung

Das Image zuerst bauen: `docker build -t super-webapp:local .` im Ordner `webapp`.

| Einstellung | Wert |
|---|---|
| Repository / Image | `super-webapp:local` |
| Netzwerk | `bridge` |
| WebUI | `http://[IP]:[PORT:8000]` |
| Container-Port | `8000` |
| Host-Port | `8088` (oder ein freier Port) |
| Container-Pfad | `/data` |
| Host-Pfad | `/mnt/appdata/super` bzw. dein direkter Pool-Pfad |
| Variable | `SUPER_DATA_DIR=/data` |
| Variable | `DJANGO_SECRET_KEY=<zufälliger geheimer Wert>` |
| Variable | `DJANGO_ALLOWED_HOSTS=192.168.178.39,localhost,127.0.0.1` |

Anmeldung einmalig über die Container-Konsole einrichten: `python manage.py createsuperuser`. Keine Standardpasswörter werden mitgeliefert.

## Zugriff

Ausgelegt für Heimnetz/VPN und einen kleinen gemeinsamen Praxisbestand. Alle angemeldeten Benutzer haben Zugriff auf dieselben Patienten und Rechnungen; keine getrennten Mandanten oder Rollen. HTTP nur im vertrauenswürdigen Heimnetz bzw. über WireGuard verwenden. Keine direkte Portfreigabe ins Internet.

Für einen eigenen HTTPS-Reverse-Proxy `SUPER_HTTPS=1`, gegebenenfalls `SUPER_TRUST_PROXY=1` und `DJANGO_CSRF_TRUSTED_ORIGINS=https://dein-hostname` setzen. Der Proxy muss `X-Forwarded-Proto` selbst setzen und eingehende Fremdwerte entfernen. Die Compose-Healthcheck-URL muss bei HTTPS-Weiterleitung entsprechend an deinen Proxy angepasst werden.

## Bisherige Daten übernehmen

CSV-Dateien: UTF-8 (BOM erlaubt), Semikolon als Trennzeichen.

- **Patienten**: `ID;Anrede;Vorname;Nachname;Straße;Ort;Geburtsdatum;Telefon`. Wiederholter Import aktualisiert anhand der bisherigen ID; bestehende fertige Rechnungen bleiben gleich. Ungültige Geburtstage wie `14.14.1414` müssen vor dem Import korrigiert oder geleert werden. Der Import ist pro Datei vollständig oder gar nicht.
- **Vorlagen**: `Ziffer;Leistungsbeschreibung;Summe`, zusätzliche Spalten erlaubt. Wiederholter Import aktualisiert Preis anhand von Ziffer und Beschreibung.
- **Rechnungsleistungen**: im gewünschten Patientenentwurf über „Rechnungs-CSV übernehmen“. `Datum;Ziffer;Leistungsbeschreibung;Summe`, optional `Diagnose`. Die Positionen werden angehängt; dieselbe Datei nicht doppelt importieren. Die Diagnose gehört zur Rechnung, nicht zur ersten Position.
- Alte fertige PDFs werden nicht automatisch in die Datenbank importiert. Behalte dein bisheriges Rechnungsarchiv; importiere alte fertige Rechnungen nicht als neue Rechnung. Die WebApp erzeugt Rechnungen erst ab dem Umstieg.

## Rechnungen und Nummern

Eine Nummer wird erst beim endgültigen Erstellen vergeben. Erstellung und Nummernvergabe erfolgen in einer SQLite-`IMMEDIATE`-Transaktion. Wiederholte Klicks erzeugen keine zweite Rechnung. Ein zweites Browserfenster mit einer alten Entwurfsrevision kann eine neuere Änderung nicht überschreiben.

Beim Erstellen ist das Rechnungsdatum der aktuelle Tag. Die PDF-Dateien erhalten eindeutige Namen und werden exklusiv angelegt. Bei normalen Schreib-/PDF-Fehlern wird die Transaktion zurückgerollt und neu erzeugte Dateien werden entfernt. Ein harter Prozessabbruch kann unreferenzierte PDF-Dateien hinterlassen; sie werden nicht als Rechnung angezeigt. Der Betrieb setzt eine einzelne App-Instanz auf derselben lokalen SQLite-Datenbank voraus.

Der PDF-Aufbau ist am ursprünglichen Brief-Layout orientiert: optionales Logo und Praxisdaten links, Rechnungsdaten rechts, Empfängeranschrift, Anrede, schlichte Leistungstabelle und Fußzeile mit Seitenzahl. Schrift und Umbrüche entsprechen nicht pixelgenau LaTeX. Praxisangaben und Steuerhinweis sind konfigurierbar. Vor Nutzung die ersten PDFs fachlich und optisch prüfen. Die Anwendung bietet noch keinen Storno-/Korrekturrechnungsablauf, keine Mahnungen, keine E-Rechnung und keine automatische rechtliche Prüfung. Fertige Rechnungen lassen sich daher in dieser Version bewusst nicht bearbeiten oder löschen. PDF-Prüfsummen ersetzen kein revisionssicheres Archiv.

## Datensicherung & Wiederherstellung

**Datenbank und PDFs immer gemeinsam sichern.** Alles liegt unter `SUPER_DATA_PATH`: `super.sqlite3`, `pdfs/` und `logos/`. Am einfachsten den Container stoppen, den gesamten Datenordner sichern und danach starten:

```sh
docker compose stop super
# Jetzt den gesamten Datenordner mit deinem Unraid-Backup sichern.
docker compose start super
```

Zusätzlich `.env` getrennt und geschützt sichern. Bei Wiederherstellung den Container stoppen, Datenordner vollständig wiederherstellen, Besitzer/Berechtigungen prüfen und starten. Code bzw. Docker-Image allein enthalten keine Patientendaten. Für Updates ebenfalls zuerst sichern, dann `docker compose up -d --build`. Datenbankmigrationen laufen beim Start automatisch.


## Logo und Begrüßungen

Unter **Einstellungen** kannst du ein Firmenlogo als PNG oder JPEG hochladen (maximal 2 MB und 4 Millionen Pixel). Ohne Upload enthält die Rechnung kein Logo. Du kannst das aktuelle Logo dort ansehen, ersetzen oder entfernen. Logos werden als normalisierte PNG-Dateien mit eindeutigen Namen im Datenordner gespeichert, nicht im Git-Repository. Frühere Logo-Dateien bleiben für die Rechnungsstände erhalten. Bereits fertig gespeicherte PDFs werden durch einen Logo-Wechsel nicht verändert.

Die Checkbox **Grüße vom Entwickler** ist standardmäßig aus. Nur wenn sie aktiv ist, erscheint auf der Übersicht zufällig eine der 59 Originalnachrichten.

Im Rechnungsfilter gibt es **Alle Rechnungen**, **Offen**, **Bezahlt** und **Entwurf**.

## Update deiner Unraid-Installation

Bei der im Gespräch eingerichteten Installation liegt der Code unter `/mnt/user/docker/super-webapp` und der Datenordner unter `/mnt/user/docker/super-data`. Den Container stoppen und den gesamten Datenordner sichern, bevor du aktualisierst. Im Unraid-Terminal:

```sh
cd /mnt/user/docker/super-webapp
git pull --ff-only
cd webapp
docker build -t super-webapp:local .
```

Bei erfolgreichem Build in Unraid **Docker → super-webapp → Edit** wählen, die Beschreibung ändern (z. B. eine Versionsnotiz ergänzen) und **Apply** klicken, damit ein Container aus dem neuen Image erstellt wird. Die vorhandene Einbindung `/mnt/user/docker/super-data` nach `/data` und alle Variablen beibehalten. Ein bloßer Neustart verwendet noch das alte Image. Die neue Datenbankmigration ergänzt die Einstellungen automatisch; vorhandene Patienten, Rechnungen und PDFs bleiben erhalten.

## Lokal entwickeln und prüfen

Python 3.12. In einem eigenen virtuellen Environment:

```sh
python -m venv .venv
# Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-test.txt
```

Umgebungsvariable `DJANGO_SECRET_KEY` setzen; für lokalen Entwicklungsserver zusätzlich `DJANGO_DEBUG=1`. Unter Linux z.B. `export DJANGO_SECRET_KEY='nur-fuer-lokale-entwicklung'`; unter PowerShell `$env:DJANGO_SECRET_KEY='nur-fuer-lokale-entwicklung'`.

```sh
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
python manage.py check
python manage.py test
```

Nur für eine leere Testinstallation: `python manage.py demo_data`. Legt synthetische Patienten, Vorlagen und einen Entwurf an, aber keinen Benutzer. Keine produktiven Praxisdaten werden automatisch aus dem alten Programm übernommen.

GitHub Actions prüft die WebApp, Migrationen, Tests und Docker-Build bei Änderungen. Der lokale Django-Entwicklungsserver ist nicht der Produktionsserver; im Container läuft Gunicorn.

## Storno, Korrektur und Löschen

Auch fertige Rechnungen können nach Bestätigung dauerhaft gelöscht werden. Storno ist weiterhin als separate Funktion verfügbar. „Stornieren“ erstellt nach Bestätigung und Angabe eines Grundes eine Stornorechnung mit eigener Nummer, Bezug zum Original und negativem Betrag. Die Originalrechnung wird als „Storniert“ angezeigt und zählt nicht mehr als offene Forderung. Die PDFs des Originals bleiben unverändert.

„Stornieren und korrigieren“ legt zusätzlich einen bearbeitbaren Korrekturentwurf an. Nach Prüfung erhält die Ersatzrechnung eine neue Nummer. Original, Storno und Korrektur sind auf ihren Detailseiten verknüpft. Auch bei bezahltem Original wird die Ersatzrechnung nicht automatisch als bezahlt markiert. Die Zahlungsart des Korrekturentwurfs ist zunächst Überweisung; Barzahlung kann bei Bedarf ausdrücklich gewählt werden.

Bei bezahlten Originalen muss Rückzahlung / Verrechnung festgehalten werden (noch offen, Rückzahlung erledigt, Verrechnung vereinbart). Der Stand und eine Notiz lassen sich auf der Stornoseite später aktualisieren. Das löst keine Zahlung aus und verändert das gespeicherte Storno-PDF nicht.

Entwürfe, fertige Rechnungen und Leistungsvorlagen können nach Bestätigung gelöscht werden. Rechnungsleistungen, PDFs und Zweitschriften werden dabei ebenfalls entfernt; die Nummernfolge wird nicht zurückgesetzt. Verknüpfte Storno- und Korrekturrechnungen müssen zuerst einzeln gelöscht werden; es gibt kein automatisches Mitlöschen. Wird eine Stornorechnung gelöscht, wird das Original nicht mehr als storniert angezeigt. Patienten mit bestehenden Rechnungen bleiben geschützt. Das Löschen einer Leistungsvorlage verändert vorhandene Rechnungsleistungen nicht.

## Oberfläche

Das hochgeladene Firmenlogo ersetzt das S im Menü. Ohne Logo bleibt das S erhalten. In den Einstellungen stehen Grün (Standard), Blau, Violett, Rosé und Dunkel zur Wahl; das Farbschema gilt für die gemeinsame Praxisoberfläche und verändert keine Rechnungs-PDFs.

Die Checkbox „Grüße vom Entwickler“ ist standardmäßig aus. Wenn aktiviert, wird bei jedem Aufruf der Übersicht zufällig eine der 59 Originalnachrichten angezeigt. Eigene Begrüßungsnachrichten werden nicht mehr angeboten; das bisherige Feld wird bei der Migration entfernt.

Zahlungsarten: Überweisung und Barzahlung. Die frühere Auswahl „Privatkasse“ wird beim Update automatisch in „Überweisung“ umgewandelt; gespeicherte PDFs bleiben unverändert.

## Listen bedienen

Auf Patienten-, Rechnungs- und Leistungslisten sortiert ein Klick auf eine Spaltenüberschrift aufsteigend, ein zweiter absteigend. Ein Pfeil zeigt die Richtung. Beträge werden numerisch und Datumswerte chronologisch sortiert; Patienten-Namen nach Nachname und Vorname. Die Sortierung erfolgt vor der Begrenzung auf 200 Treffer. Suche und Rechnungsstatus bleiben erhalten.

In den Patienten- und Leistungslisten schaltet ein Klick auf „Aktiv“ bzw. „Archiviert“ direkt den Status um. Daten und bestehende Rechnungen bleiben erhalten. Archivierte Leistungsvorlagen werden im Rechnungsauswahldialog nicht mehr angeboten.
