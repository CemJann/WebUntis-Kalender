"""
WebUntis -> Apple Calendar Sync
Nutzt die WebUntis JSON-RPC API direkt (keine externe Bibliothek noetig).
"""

import requests
import json
import os
import hashlib
import pytz
from datetime import datetime, date, timedelta
from icalendar import Calendar, Event

SERVER   = os.environ["WEBUNTIS_SERVER"]
SCHOOL   = os.environ["WEBUNTIS_SCHOOL"]
USERNAME = os.environ["WEBUNTIS_USERNAME"]
PASSWORD = os.environ["WEBUNTIS_PASSWORD"]
TIMEZONE = "Europe/Berlin"
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
        print(f"{len(result)} Eintraege gefunden.")
        return result


def zeit_zu_datetime(datum_int: int, zeit_int: int, tz) -> datetime:
    datum_str = str(datum_int)
    jahr  = int(datum_str[0:4])
    monat = int(datum_str[4:6])
    tag   = int(datum_str[6:8])
    stunde = zeit_int // 100
    minute = zeit_int % 100
    return tz.localize(datetime(jahr, monat, tag, stunde, minute))


def erstelle_uid(stunde: dict) -> str:
    rohdaten = f"{stunde['date']}-{stunde['startTime']}-{stunde['endTime']}"
    if stunde.get("su"):
        rohdaten += f"-{stunde['su'][0].get('name', '')}"
    return hashlib.md5(rohdaten.encode()).hexdigest() + "@webuntis-sync"


def erstelle_kalender(stunden: list) -> bytes:
    tz  = pytz.timezone(TIMEZONE)
    cal = Calendar()
    cal.add("prodid", "-//WebUntis Stundenplan Sync//DE")
    cal.add("version", "2.0")
    cal.add("X-WR-CALNAME", "Stundenplan")
    cal.add("X-WR-TIMEZONE", TIMEZONE)
    cal.add("REFRESH-INTERVAL;VALUE=DURATION", "PT1H")
    cal.add("X-PUBLISHED-TTL", "PT1H")

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
        if code == "cancelled":
            teile.append("Diese Stunde faellt aus!")
        if teile:
            event.add("description", "\n".join(teile))

        if code == "cancelled":
            event.add("status", "CANCELLED")
            event.add("transp", "TRANSPARENT")
        else:
            event.add("status", "CONFIRMED")
            event.add("transp", "OPAQUE")

        event.add("uid", erstelle_uid(stunde))
        cal.add_component(event)

    return cal.to_ical()


def main():
    os.makedirs("docs", exist_ok=True)
    client = WebUntisClient()
    client.login()
    try:
        heute  = date.today()
        ende   = heute + timedelta(weeks=WOCHEN_VORAUS)
        stunden = client.get_timetable(heute, ende)
    finally:
        client.logout()

    ics_data = erstelle_kalender(stunden)
    pfad = "docs/stundenplan.ics"
    with open(pfad, "wb") as f:
        f.write(ics_data)
    print(f"Kalender gespeichert: {pfad}")


if __name__ == "__main__":
    main()
