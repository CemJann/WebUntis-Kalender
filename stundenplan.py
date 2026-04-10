"""
WebUntis -> Apple Calendar Sync
Mit Schulferien (SH), Feiertagen und WebUntis-Schulevents.
"""

import requests
import os
import hashlib
import pytz
import holidays
from datetime import datetime, date, timedelta
from icalendar import Calendar, Event

SERVER   = os.environ["WEBUNTIS_SERVER"]
SCHOOL   = os.environ["WEBUNTIS_SCHOOL"]
USERNAME = os.environ["WEBUNTIS_USERNAME"]
PASSWORD = os.environ["WEBUNTIS_PASSWORD"]
TIMEZONE = "Europe/Berlin"
BUNDESLAND = "SH"
WOCHEN_VORAUS = 8


class WebUntisClient:
    def __init__(self):
        self.url = f"https://{SERVER}/WebUntis/jsonrpc.do"
        self.session = requests.Session()
        self.session_id = None
        self.person_id = None
        self.person_type = None

    def _rpc(self, method, params=None):
        payload = {
            "id": "1",
            "method": method,
            "params": params or {},
            "jsonrpc": "2.0"
        }
        cookies = {"JSESSIONID": self.session_id} if self.session_id else {}
        r = self.session.post(
            self.url,
            params={"school": SCHOOL},
            json=payload,
            cookies=cookies,
            timeout=30
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise Exception(f"WebUntis Fehler: {data['error'].get('message', data['error'])}")
        return data.get("result")

    def login(self):
        print("Verbinde mit WebUntis...")
        result = self._rpc("authenticate", {
            "user": USERNAME,
            "password": PASSWORD,
            "client": "stundenplan-sync"
        })
        self.session_id  = result["sessionId"]
        self.person_id   = result["personId"]
        self.person_type = result["personType"]
        print(f"Login erfolgreich (Person-ID: {self.person_id})")

    def logout(self):
        self._rpc("logout")

    def get_timetable(self, start: date, end: date):
        print(f"Lade Stundenplan vom {start} bis {end}...")
        result = self._rpc("getTimetable", {
            "options": {
                "startDate": int(start.strftime("%Y%m%d")),
                "endDate":   int(end.strftime("%Y%m%d")),
                "element": {
                    "id":   self.person_id,
                    "type": self.person_type
                },
                "showInfo":      True,
                "showSubstText": True,
                "showLsText":    True,
                "klasseFields":  ["id", "name", "longname"],
                "roomFields":    ["id", "name", "longname"],
                "subjectFields": ["id", "name", "longname"],
                "teacherFields": ["id", "name", "longname"]
            }
        })
        print(f"{len(result)} Stundenplan-Eintraege gefunden.")
        return result

    def get_school_events(self, start: date, end: date):
        """Laedt schulspezifische Ereignisse (Ausfluge, freie Tage etc.)"""
        print("Lade Schulereignisse...")
        try:
            result = self._rpc("getCalendarData", {
                "startDate": int(start.strftime("%Y%m%d")),
                "endDate":   int(end.strftime("%Y%m%d")),
            })
            if result:
                print(f"{len(result)} Schulereignisse gefunden.")
                return result
        except Exception as e:
            print(f"Hinweis: getCalendarData nicht verfuegbar ({e})")

        # Fallback: schoolyear events
        try:
            result = self._rpc("getHolidays")
            if result:
                heute = date.today()
                relevante = [
                    h for h in result
                    if date(
                        int(str(h["startDate"])[:4]),
                        int(str(h["startDate"])[4:6]),
                        int(str(h["startDate"])[6:8])
                    ) >= start
                ]
                print(f"{len(relevante)} WebUntis-Ferien/Ereignisse gefunden.")
                return relevante
        except Exception as e:
            print(f"Hinweis: getHolidays nicht verfuegbar ({e})")

        return []


def datum_aus_int(datum_int: int) -> date:
    s = str(datum_int)
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def zeit_zu_datetime(datum_int: int, zeit_int: int, tz) -> datetime:
    d = datum_aus_int(datum_int)
    stunde = zeit_int // 100
    minute = zeit_int % 100
    return tz.localize(datetime(d.year, d.month, d.day, stunde, minute))


def erstelle_uid(rohdaten: str) -> str:
    return hashlib.md5(rohdaten.encode()).hexdigest() + "@webuntis-sync"


def hole_schulferien_api(start: date, end: date) -> list:
    ferien_liste = []
    try:
        for jahr in set([start.year, end.year]):
            url = f"https://ferien-api.de/api/v1/holidays/SH/{jahr}"
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            for f in r.json():
                f_start = date.fromisoformat(f["start"][:10])
                f_end   = date.fromisoformat(f["end"][:10])
                if f_end >= start and f_start <= end:
                    ferien_liste.append({
                        "name":  f.get("name", "Schulferien"),
                        "start": f_start,
                        "end":   f_end
                    })
        print(f"{len(ferien_liste)} Ferieneintraege (API) gefunden.")
    except Exception as e:
        print(f"Warnung: Ferien-API nicht erreichbar: {e}")
    return ferien_liste


def hole_feiertage(start: date, end: date) -> list:
    feiertage = []
    for jahr in set([start.year, end.year]):
        sh = holidays.Germany(state=BUNDESLAND, years=jahr)
        for f_datum, f_name in sh.items():
            if start <= f_datum <= end:
                feiertage.append({"datum": f_datum, "name": f_name})
    print(f"{len(feiertage)} Feiertage gefunden.")
    return feiertage


def erstelle_kalender(stunden, schulevents, ferien, feiertage) -> bytes:
    tz  = pytz.timezone(TIMEZONE)
    cal = Calendar()
    cal.add("prodid", "-//WebUntis Stundenplan Sync//DE")
    cal.add("version", "2.0")
    cal.add("X-WR-CALNAME", "Stundenplan")
    cal.add("X-WR-TIMEZONE", TIMEZONE)
    cal.add("REFRESH-INTERVAL;VALUE=DURATION", "PT1H")
    cal.add("X-PUBLISHED-TTL", "PT1H")

    # ── Schulstunden ───────────────────────────────────────
    for stunde in stunden:
        event = Event()

        fach = "Unbekanntes Fach"
        if stunde.get("su"):
            fach = stunde["su"][0].get("longname") or stunde["su"][0].get("name", fach)

        code = stunde.get("code", "")
        if code == "cancelled":
            titel = f"Entfall: {fach}"
        elif code == "irregular":
            titel = f"Vertretung: {fach}"
        else:
            titel = fach

        event.add("summary", titel)

        start_dt = zeit_zu_datetime(stunde["date"], stunde["startTime"], tz)
        end_dt   = zeit_zu_datetime(stunde["date"], stunde["endTime"],   tz)
        event.add("dtstart", start_dt)
        event.add("dtend",   end_dt)
        event.add("dtstamp", datetime.now(tz=pytz.utc))

        if stunde.get("ro"):
            raum = ", ".join(r.get("name", "") for r in stunde["ro"])
            event.add("location", raum)

        teile = []
        if stunde.get("te"):
            lehrer = ", ".join(t.get("longname") or t.get("name", "") for t in stunde["te"])
            teile.append(f"Lehrer: {lehrer}")
        if stunde.get("substText"):
            teile.append(f"Hinweis: {stunde['substText']}")
        if stunde.get("lstext"):
            teile.append(f"Info: {stunde['lstext']}")
        if code == "cancelled":
            teile.append("Diese Stunde faellt aus!")
        if teile:
            event.add("description", "\n".join(teile))

        event.add("status", "CANCELLED" if code == "cancelled" else "CONFIRMED")
        event.add("transp",  "TRANSPARENT" if code == "cancelled" else "OPAQUE")

        uid_raw = f"{stunde['date']}-{stunde['startTime']}-{stunde['endTime']}"
        if stunde.get("su"):
            uid_raw += f"-{stunde['su'][0].get('name', '')}"
        event.add("uid", erstelle_uid(uid_raw))
        cal.add_component(event)

    # ── WebUntis Schulereignisse (Ausfluge, schulfreie Tage) ──
    for ev in schulevents:
        try:
            event = Event()
            name = ev.get("name") or ev.get("longName") or ev.get("text") or "Schulereignis"
            event.add("summary", f"Schule: {name}")

            # Datum aus startDate / endDate
            start_d = datum_aus_int(ev.get("startDate") or ev.get("date"))
            end_d   = datum_aus_int(ev.get("endDate") or ev.get("date"))

            event.add("dtstart", start_d)
            event.add("dtend",   end_d + timedelta(days=1))
            event.add("dtstamp", datetime.now(tz=pytz.utc))
            event.add("transp",  "TRANSPARENT")
            event.add("uid", erstelle_uid(f"schulevent-{name}-{start_d}"))
            cal.add_component(event)
        except Exception as e:
            print(f"Schulereignis konnte nicht verarbeitet werden: {e}")

    # ── Schulferien (API) ──────────────────────────────────
    for f in ferien:
        event = Event()
        event.add("summary", f"Ferien: {f['name']}")
        event.add("dtstart", f["start"])
        event.add("dtend",   f["end"] + timedelta(days=1))
        event.add("dtstamp", datetime.now(tz=pytz.utc))
        event.add("transp",  "TRANSPARENT")
        event.add("uid", erstelle_uid(f"ferien-{f['name']}-{f['start']}"))
        cal.add_component(event)

    # ── Feiertage ──────────────────────────────────────────
    for f in feiertage:
        event = Event()
        event.add("summary", f"Feiertag: {f['name']}")
        event.add("dtstart", f["datum"])
        event.add("dtend",   f["datum"] + timedelta(days=1))
        event.add("dtstamp", datetime.now(tz=pytz.utc))
        event.add("transp",  "TRANSPARENT")
        event.add("uid", erstelle_uid(f"feiertag-{f['name']}-{f['datum']}"))
        cal.add_component(event)

    return cal.to_ical()


def main():
    os.makedirs("docs", exist_ok=True)
    client = WebUntisClient()
    client.login()
    try:
        heute  = date.today()
        ende   = heute + timedelta(weeks=WOCHEN_VORAUS)
        stunden      = client.get_timetable(heute, ende)
        schulevents  = client.get_school_events(heute, ende)
    finally:
        client.logout()

    ferien    = hole_schulferien_api(heute, ende)
    feiertage = hole_feiertage(heute, ende)

    ics_data = erstelle_kalender(stunden, schulevents, ferien, feiertage)

    pfad = "docs/stundenplan.ics"
    with open(pfad, "wb") as f:
        f.write(ics_data)
    print(f"Kalender gespeichert: {pfad}")


if __name__ == "__main__":
    main()
