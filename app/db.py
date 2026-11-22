"""Database: schema, connectie en seed-data (SQLite, alleen standaardbibliotheek)."""
import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta

DB_PATH = os.environ.get("CRM_DB", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "crm.db"))
_lock = threading.RLock()
_local = threading.local()


def now():
    return datetime.now().isoformat(timespec="seconds")


def today():
    return date.today()


def conn():
    c = getattr(_local, "conn", None)
    if c is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        _local.conn = c
    return c


class tx:
    """Transactie met globale schrijflock (SQLite heeft één schrijver)."""

    def __enter__(self):
        _lock.acquire()
        self.c = conn()
        self.c.execute("BEGIN IMMEDIATE")
        return self.c

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                self.c.execute("COMMIT")
            else:
                self.c.execute("ROLLBACK")
        finally:
            _lock.release()
        return False


def q(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, naam TEXT NOT NULL, email TEXT UNIQUE NOT NULL, rol TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS klanten(
  id INTEGER PRIMARY KEY, nummer TEXT UNIQUE, naam TEXT NOT NULL, kvk TEXT, btw TEXT,
  bezoekadres TEXT, postadres TEXT, factuuradres TEXT, plaats TEXT, email_domein TEXT,
  branche TEXT, accountmanager_id INTEGER REFERENCES users(id), status TEXT NOT NULL DEFAULT 'Prospect',
  gearchiveerd INTEGER NOT NULL DEFAULT 0,
  jira_org_id TEXT, jira_key TEXT, jira_sync_status TEXT DEFAULT 'n.v.t.',
  created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS contactpersonen(
  id INTEGER PRIMARY KEY, klant_id INTEGER NOT NULL REFERENCES klanten(id),
  voornaam TEXT, achternaam TEXT NOT NULL, email TEXT NOT NULL, telefoon TEXT, functie TEXT,
  rollen TEXT NOT NULL DEFAULT '[]', communicatievoorkeur TEXT, avg_grondslag TEXT NOT NULL,
  actief INTEGER NOT NULL DEFAULT 1, geanonimiseerd INTEGER NOT NULL DEFAULT 0,
  jira_account_id TEXT, jira_key TEXT, jira_sync_status TEXT DEFAULT 'n.v.t.',
  created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS contracten(
  id INTEGER PRIMARY KEY, nummer TEXT UNIQUE NOT NULL, klant_id INTEGER NOT NULL REFERENCES klanten(id),
  soort TEXT NOT NULL, startdatum TEXT NOT NULL, einddatum TEXT, onbepaalde_tijd INTEGER NOT NULL DEFAULT 0,
  verlengingsvorm TEXT NOT NULL, opzegtermijn INTEGER NOT NULL, waarde REAL, urenbudget REAL,
  sla_niveau TEXT, change_regime TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Concept',
  versie INTEGER NOT NULL DEFAULT 1, documenten TEXT DEFAULT '[]',
  jira_key TEXT, jira_sync_status TEXT DEFAULT 'n.v.t.', created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS contract_versies(
  id INTEGER PRIMARY KEY, contract_id INTEGER NOT NULL REFERENCES contracten(id), versie INTEGER,
  soort_wijziging TEXT, toelichting TEXT, snapshot TEXT, gebruiker TEXT, ts TEXT);

CREATE TABLE IF NOT EXISTS opdrachten(
  id INTEGER PRIMARY KEY, nummer TEXT UNIQUE NOT NULL, contract_id INTEGER NOT NULL REFERENCES contracten(id),
  omschrijving TEXT NOT NULL, type TEXT NOT NULL, startdatum TEXT NOT NULL, einddatum TEXT NOT NULL,
  begrote_uren REAL, budget REAL, bestede_uren REAL NOT NULL DEFAULT 0, voortgang REAL NOT NULL DEFAULT 0,
  opdrachtgever_id INTEGER REFERENCES contactpersonen(id), status TEXT NOT NULL DEFAULT 'Gepland',
  jira_epic_done INTEGER NOT NULL DEFAULT 0,
  jira_key TEXT, jira_sync_status TEXT DEFAULT 'n.v.t.', created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS rfcs(
  id INTEGER PRIMARY KEY, jira_key TEXT UNIQUE, titel TEXT NOT NULL, omschrijving TEXT,
  klant_id INTEGER NOT NULL REFERENCES klanten(id), contract_id INTEGER NOT NULL REFERENCES contracten(id),
  opdracht_id INTEGER REFERENCES opdrachten(id), aanvrager_id INTEGER REFERENCES contactpersonen(id),
  change_type TEXT NOT NULL, prioriteit TEXT NOT NULL, gewenste_datum TEXT,
  status TEXT NOT NULL DEFAULT 'Nieuw', binnen_contract TEXT NOT NULL DEFAULT 'Te bepalen',
  contracttoets TEXT, geschatte_kosten REAL, geschatte_uren REAL, cab_besluit TEXT, cab_datum TEXT,
  cab_vereist INTEGER NOT NULL DEFAULT 0, bron TEXT, veld_ts TEXT DEFAULT '{}', last_sync TEXT,
  jira_sync_status TEXT DEFAULT 'n.v.t.', created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS rfc_contacten(
  rfc_id INTEGER REFERENCES rfcs(id), contact_id INTEGER REFERENCES contactpersonen(id), rol TEXT,
  PRIMARY KEY(rfc_id, contact_id, rol));

CREATE TABLE IF NOT EXISTS impactanalyses(
  id INTEGER PRIMARY KEY, rfc_id INTEGER NOT NULL REFERENCES rfcs(id), versie INTEGER NOT NULL,
  impact_techniek TEXT, toelichting_techniek TEXT, impact_organisatie TEXT, toelichting_organisatie TEXT,
  impact_planning TEXT, kosten REAL, uren REAL, kans INTEGER, impact INTEGER, risicoscore INTEGER,
  impact_contract TEXT, security_privacy TEXT, dpia_nodig INTEGER DEFAULT 0, terugvalplan TEXT,
  opgesteld_door TEXT, datum TEXT, contractverschil TEXT);

CREATE TABLE IF NOT EXISTS activiteiten(
  id INTEGER PRIMARY KEY, klant_id INTEGER REFERENCES klanten(id), type TEXT, tekst TEXT, gebruiker TEXT, ts TEXT);

CREATE TABLE IF NOT EXISTS signalen(
  id INTEGER PRIMARY KEY, sleutel TEXT UNIQUE, type TEXT, entiteit TEXT, entiteit_id INTEGER,
  bericht TEXT, ontvanger_id INTEGER, gelezen INTEGER DEFAULT 0, ts TEXT);

CREATE TABLE IF NOT EXISTS offertes(
  id INTEGER PRIMARY KEY, rfc_id INTEGER REFERENCES rfcs(id), klant_id INTEGER, accountmanager_id INTEGER,
  omschrijving TEXT, waarde REAL, status TEXT DEFAULT 'Concept', ts TEXT);

CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts TEXT, gebruiker TEXT, bron TEXT, entiteit TEXT, entiteit_id INTEGER,
  actie TEXT, wijzigingen TEXT);

-- Integratielaag: één berichtenqueue voor beide richtingen
CREATE TABLE IF NOT EXISTS berichten(
  id INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, richting TEXT NOT NULL, type TEXT NOT NULL,
  entiteit TEXT, entiteit_id TEXT, payload TEXT, status TEXT NOT NULL DEFAULT 'wachtend',
  pogingen INTEGER NOT NULL DEFAULT 0, volgende_poging TEXT, fout TEXT, ts TEXT, verwerkt TEXT, duur_ms INTEGER);
CREATE INDEX IF NOT EXISTS ix_ber_status ON berichten(status, volgende_poging);

CREATE TABLE IF NOT EXISTS conflicten(
  id INTEGER PRIMARY KEY, entiteit TEXT, entiteit_id INTEGER, veld TEXT, crm_waarde TEXT, jira_waarde TEXT,
  winnaar TEXT, ts TEXT, opgelost INTEGER DEFAULT 0);

CREATE TABLE IF NOT EXISTS config(sleutel TEXT PRIMARY KEY, waarde TEXT);

-- ===== Gesimuleerde Jira Cloud (sandbox) =====
CREATE TABLE IF NOT EXISTS j_orgs(id INTEGER PRIMARY KEY, naam TEXT);
CREATE TABLE IF NOT EXISTS j_customers(account_id TEXT PRIMARY KEY, email TEXT UNIQUE, naam TEXT, org_id INTEGER, actief INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS j_assets(key TEXT PRIMARY KEY, objtype TEXT, label TEXT, attrs TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS j_issues(
  key TEXT PRIMARY KEY, project TEXT, issuetype TEXT, summary TEXT, description TEXT, status TEXT,
  fields TEXT DEFAULT '{}', parent TEXT, created TEXT, updated TEXT, updated_by TEXT);
CREATE TABLE IF NOT EXISTS j_worklogs(id INTEGER PRIMARY KEY, issue_key TEXT, uren REAL, auteur TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS j_seq(project TEXT PRIMARY KEY, n INTEGER);
"""

DEFAULT_CONFIG = {
    # NFR-BH-01: mapping configureerbaar door functioneel beheer
    "statusmapping": {
        "Open": "Nieuw", "Beoordeling": "Beoordeling", "CAB": "CAB", "Goedgekeurd": "Goedgekeurd",
        "Afgewezen": "Afgewezen", "Uitgevoerd": "Uitgevoerd", "Gesloten": "Gesloten",
    },
    "veldmapping_rfc": [
        {"crm": "jira_key", "jira": "key", "richting": "Jira → CRM"},
        {"crm": "titel", "jira": "summary", "richting": "Bidirectioneel"},
        {"crm": "omschrijving", "jira": "description", "richting": "Bidirectioneel"},
        {"crm": "klant_id", "jira": "organization + assets:Klant", "richting": "CRM → Jira"},
        {"crm": "contract_id", "jira": "assets:Contract", "richting": "Bidirectioneel"},
        {"crm": "contractinfo", "jira": "cf_contract_soort/einddatum/regime (read-only)", "richting": "CRM → Jira"},
        {"crm": "opdracht_id", "jira": "parent (Epic)", "richting": "Bidirectioneel"},
        {"crm": "aanvrager_id", "jira": "reporter", "richting": "Bidirectioneel"},
        {"crm": "change_type", "jira": "changeType", "richting": "Bidirectioneel"},
        {"crm": "prioriteit", "jira": "priority", "richting": "Bidirectioneel"},
        {"crm": "status", "jira": "status", "richting": "Jira → CRM"},
        {"crm": "binnen_contract", "jira": "cf_binnen_contract", "richting": "CRM → Jira"},
        {"crm": "geschatte_kosten/uren", "jira": "cf_kosten / cf_uren", "richting": "Jira → CRM"},
        {"crm": "cab_besluit", "jira": "approval", "richting": "Jira → CRM"},
    ],
    "drempels": {"uren_waarschuwing": [75, 90], "opzeg_signaal_maanden": [6, 3, 1], "cab_risicoscore": 15},
    "retry": {"max_pogingen": 5, "basis_seconden": 2},
    "bewaartermijn_maanden": {"contactpersoon": 24},
    "jira_sites": [{"naam": "Hoofdsite (sandbox)", "url": "https://voorbeeld.atlassian.net", "adapter": "simulator"}],
}


def add_months(d, m):
    y, mo = d.year + (d.month - 1 + m) // 12, (d.month - 1 + m) % 12 + 1
    import calendar
    return date(y, mo, min(d.day, calendar.monthrange(y, mo)[1]))


def init(seed=True):
    c = conn()
    c.executescript(SCHEMA)
    for k, v in DEFAULT_CONFIG.items():
        c.execute("INSERT OR IGNORE INTO config(sleutel, waarde) VALUES(?,?)", (k, json.dumps(v)))
    if seed and not q1("SELECT 1 x FROM users"):
        _seed(c)


def _seed(c):
    t = today()
    ts = now()
    users = [
        (1, "Sanne de Vries", "sanne@bedrijf.nl", "accountmanager"),
        (2, "Mark Jansen", "mark@bedrijf.nl", "accountmanager"),
        (3, "Fatima El Amrani", "fatima@bedrijf.nl", "servicemanager"),
        (4, "Joris Bakker", "joris@bedrijf.nl", "consultant"),
        (5, "Ellen Visser", "ellen@bedrijf.nl", "directie"),
        (6, "Tom Smit", "tom@bedrijf.nl", "finance"),
        (7, "Admin Beheer", "beheer@bedrijf.nl", "beheerder"),
    ]
    c.executemany("INSERT INTO users VALUES(?,?,?,?)", users)
    klanten = [
        (1, "KL-0001", "Gemeente Noordstad", "12345678", "NL001234567B01", "Stadhuisplein 1", "Postbus 100", "Postbus 100",
         "Noordstad", "noordstad.nl", "Overheid", 1, "Prospect"),
        (2, "KL-0002", "Zorggroep Horizon B.V.", "23456789", "NL002345678B01", "Zorglaan 12", "Zorglaan 12", "Postbus 55",
         "Utrecht", "horizonzorg.nl", "Zorg", 1, "Prospect"),
        (3, "KL-0003", "Bakker Logistiek B.V.", "34567890", None, "Havenweg 8", "Havenweg 8", "Havenweg 8",
         "Rotterdam", "bakkerlogistiek.nl", "Logistiek", 2, "Prospect"),
        (4, "KL-0004", "Studio Kwast", "45678901", None, "Kerkstraat 3", "Kerkstraat 3", "Kerkstraat 3",
         "Haarlem", "studiokwast.nl", "Creatief", 2, "Prospect"),
    ]
    for k in klanten:
        c.execute("""INSERT INTO klanten(id,nummer,naam,kvk,btw,bezoekadres,postadres,factuuradres,plaats,email_domein,
                     branche,accountmanager_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  k + (ts, ts))
    cps = [
        (1, 1, "Petra", "Hoogland", "p.hoogland@noordstad.nl", "06-11111111", "CIO", ["Beslisser", "CAB-lid"], "Overeenkomst"),
        (2, 1, "Kees", "Wolters", "k.wolters@noordstad.nl", "06-22222222", "Applicatiebeheerder", ["Technisch contact"], "Overeenkomst"),
        (3, 2, "Anouk", "Verbeek", "a.verbeek@horizonzorg.nl", "06-33333333", "Manager ICT", ["Beslisser", "CAB-lid", "Escalatie"], "Overeenkomst"),
        (4, 2, "Ruben", "Koster", "r.koster@horizonzorg.nl", None, "Controller", ["Financieel contact"], "Gerechtvaardigd belang"),
        (5, 3, "Lisa", "Bakker", "lisa@bakkerlogistiek.nl", "010-1234567", "Directeur", ["Beslisser"], "Overeenkomst"),
        (6, 3, "Daan", "Mulder", "daan@bakkerlogistiek.nl", None, "IT-coördinator", ["Technisch contact"], "Overeenkomst"),
    ]
    for cp in cps:
        c.execute("""INSERT INTO contactpersonen(id,klant_id,voornaam,achternaam,email,telefoon,functie,rollen,avg_grondslag,
                     communicatievoorkeur,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  cp[:7] + (json.dumps(cp[7]), cp[8], "E-mail", ts, ts))
    d = lambda x: x.isoformat()
    contracten = [
        # Noordstad: managed service, opzegdatum binnen 3 maanden
        (1, "CT-2024-0007", 1, "Managed service / SLA", d(add_months(t, -22)), d(add_months(t, 5)), 0, "Stilzwijgend", 3,
         240000, 1200, "Gold", "Strippenkaart", "Concept"),
        # Noordstad: raamovereenkomst ruim lopend
        (2, "CT-2025-0031", 1, "Raamovereenkomst", d(add_months(t, -10)), d(add_months(t, 26)), 0, "Optie tot verlenging", 6,
         500000, 3000, None, "Meerwerk via offerte", "Concept"),
        # Horizon: T&M met urenbudget bijna op
        (3, "CT-2025-0042", 2, "Time & material", d(add_months(t, -8)), d(add_months(t, 10)), 0, "Geen", 1,
         120000, 800, None, "Binnen contract", "Concept"),
        # Bakker: verlopen contract
        (4, "CT-2023-0015", 3, "Fixed price project", d(add_months(t, -20)), d(t - timedelta(days=12)), 0, "Geen", 1,
         85000, 600, None, "Meerwerk via offerte", "Concept"),
        # Bakker: licentie/support onbepaalde tijd
        (5, "CT-2026-0003", 3, "Licentie / support", d(add_months(t, -3)), None, 1, "Stilzwijgend", 2,
         18000, None, "Silver", "Binnen contract", "Concept"),
    ]
    for ct in contracten:
        c.execute("""INSERT INTO contracten(id,nummer,klant_id,soort,startdatum,einddatum,onbepaalde_tijd,verlengingsvorm,
                     opzegtermijn,waarde,urenbudget,sla_niveau,change_regime,status,created_at,updated_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ct + (ts, ts))
        c.execute("INSERT INTO contract_versies(contract_id,versie,soort_wijziging,toelichting,snapshot,gebruiker,ts) VALUES(?,?,?,?,?,?,?)",
                  (ct[0], 1, "Initieel", "Aangemaakt bij migratie", "{}", "migratie", ts))
    opdrachten = [
        (1, "OP-2024-0101", 1, "Beheer en doorontwikkeling zaaksysteem", "Managed service", d(add_months(t, -22)), d(add_months(t, 5)), 1200, None, 2),
        (2, "OP-2025-0120", 2, "Migratie archief naar e-Depot", "Project", d(add_months(t, -6)), d(add_months(t, 4)), 900, 110000, 1),
        (3, "OP-2025-0133", 3, "Detachering integratiespecialist", "Detachering", d(add_months(t, -8)), d(add_months(t, 10)), 800, None, 3),
    ]
    for op in opdrachten:
        c.execute("""INSERT INTO opdrachten(id,nummer,contract_id,omschrijving,type,startdatum,einddatum,begrote_uren,budget,
                     opdrachtgever_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", op + (ts, ts))
    c.execute("INSERT INTO activiteiten(klant_id,type,tekst,gebruiker,ts) VALUES(1,'Afspraak','Kwartaaloverleg met CIO; verlenging managed service besproken','Sanne de Vries',?)", (ts,))
    c.execute("INSERT INTO activiteiten(klant_id,type,tekst,gebruiker,ts) VALUES(2,'Notitie','Urenbudget loopt hard op; uitbreiding bespreken met controller','Sanne de Vries',?)", (ts,))
