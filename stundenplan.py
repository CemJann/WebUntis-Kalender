"""
WebUntis → Apple Calendar Sync
Dieses Skript liest deinen Stundenplan aus WebUntis und erstellt eine .ics-Datei,
die Apple Calendar automatisch abonnieren kann.
"""

import webuntis
import webuntis.session
from icalendar import Calendar, Event, vText
from datetime import datetime, timedelta, date
import pytz
import os
import hashlib

# ─────────────────────────────────────────────
# KONFIGURATION (wird aus GitHub Secrets geladen)
# ─────────────────────────────────────────────
SERVER   = os.environ["WEBUNTIS_SERVER"]    # z.B. "borys.webuntis.com"
SCHULE   = os.environ["WEBUNTIS_SCHOOL"]    # z.B. "Meine-Schule"
USERNAME = os.environ["WEBUNTIS_USERNAME"]  # dein WebUntis-Benutzername
PASSWORD = os.environ["WEBUNTIS_PASSWORD"]  # dein WebUntis-Passwort
TIMEZONE = "Europe/Berlin"

# Wie viele Wochen in die Zukunft soll der Kalender gehen?
WOCHEN_VORAUS = 8


def zeit_zu_datetime(datum: date, zeit_int: int, tz) -> datetime:
    """Wandelt WebUntis-Zeitformat (z.B. 800 → 08:00) in datetime um."""
    stunde = zeit_int // 100
    minute = zeit_int % 100
    dt = datetime(datum.year, datum.month, datum.day, stunde, minute)
    return tz.localize(dt)


def erstelle_uid(stunde) -> str:
    """Erstellt eine eindeutige ID für jeden Kalendereintrag."""
    rohdaten = f"{stunde.date}-{stunde.startTime}-{stunde.endTime}"
    if stunde.subjects:
        rohdaten += f"-{stunde.subjects[0].name}"
    return hashlib.md5(rohdaten.encode()).hexdigest() + "@webuntis-sync"


def hole_stundenplan():
    """Verbindet sich mit WebUntis und ruft den Stundenplan ab."""
    print("🔗 Verbinde mit WebUntis...")

    with webuntis.session.Session(
        username=USERNAME,
        password=PASSWORD,
        server=SERVER,
        school=SCHULE,
        useKeyring=False,
    ).login() as session:

        heute = date.today()
        ende  = heute + timedelta(weeks=WOCHEN_VORAUS)

        print(f"📅 Lade Stundenplan vom {heute} bis {ende}...")
        stunden = session.timetable(start=heute, end=ende)

        print(f"✅ {len(stunden)} Einträge gefunden.")
        return stunden


def erstelle_kalender(stunden) -> bytes:
    """Wandelt die WebUntis-Stunden in eine .ics-Datei um."""
    tz  = pytz.timezone(TIMEZONE)
    cal = Calendar()

    cal.add("prodid", "-//WebUntis Stundenplan Sync//DE")
    cal.add("version", "2.0")
    cal.add("X-WR-CALNAME", "Stundenplan")
    cal.add("X-WR-TIMEZONE", TIMEZONE)
    cal.add("REFRESH-INTERVAL;VALUE=DURATION", "PT1H")  # Stündliche Aktualisierung
    cal.add("X-PUBLISHED-TTL", "PT1H")

    for stunde in stunden:
        event = Event()

        # ── Titel ──────────────────────────────────────────
        if stunde.subjects:
            fach = stunde.subjects[0].long_name or stunde.subjects[0].name
        else:
            fach = "Unbekanntes Fach"

        entfall     = getattr(stunde, "code", "") == "cancelled"
        unregelmäßig = getattr(stunde, "code", "") == "irregular"

        if entfall:
            titel = f"❌ {fach} – Entfall"
        elif unregelmäßig:
            titel = f"⚠️ {fach} (Änderung)"
        else:
            titel = fach

        event.add("summary", titel)

        # ── Zeiten ─────────────────────────────────────────
        start_dt = zeit_zu_datetime(stunde.date, stunde.startTime, tz)
        end_dt   = zeit_zu_datetime(stunde.date, stunde.endTime,   tz)

        event.add("dtstart", start_dt)
        event.add("dtend",   end_dt)
        event.add("dtstamp", datetime.now(tz=pytz.utc))

        # ── Raum ───────────────────────────────────────────
        if stunde.rooms:
            raum = ", ".join(r.name for r in stunde.rooms)
            event.add("location", raum)

        # ── Lehrer ─────────────────────────────────────────
        beschreibung_teile = []
        if stunde.teachers:
            lehrer = ", ".join(t.name for t in stunde.teachers)
            beschreibung_teile.append(f"Lehrer: {lehrer}")

        if entfall:
            beschreibung_teile.append("⚠️ Diese Stunde fällt aus!")
        elif unregelmäßig:
            beschreibung_teile.append("⚠️ Diese Stunde weicht vom regulären Plan ab.")

        if beschreibung_teile:
            event.add("description", "\n".join(beschreibung_teile))

        # ── Status ─────────────────────────────────────────
        if entfall:
            event.add("status", "CANCELLED")
            event.add("transp", "TRANSPARENT")
        else:
            event.add("status", "CONFIRMED")
            event.add("transp", "OPAQUE")

        # ── Eindeutige ID ──────────────────────────────────
        event.add("uid", erstelle_uid(stunde))

        cal.add_component(event)

    return cal.to_ical()


def main():
    os.makedirs("docs", exist_ok=True)

    stunden  = hole_stundenplan()
    ics_data = erstelle_kalender(stunden)

    ausgabe_pfad = "docs/stundenplan.ics"
    with open(ausgabe_pfad, "wb") as f:
        f.write(ics_data)

    print(f"💾 Kalender gespeichert: {ausgabe_pfad}")
    print("🎉 Fertig! Der Kalender wurde erfolgreich aktualisiert.")


if __name__ == "__main__":
    main()
