"""Gesimuleerde Jira Cloud-sandbox (JSM Organizations/Customers, Assets, Change-issues, Epics, worklogs).

Twee kanten:
  * Adapter-methoden die de integratielaag aanroept (als serviceaccount 'svc-integratie'), zoals de echte REST API.
  * Gebruikersacties (portaal, agent, CAB, engineers) die - net als echte Jira - webhooks met HMAC-handtekening
    naar het CRM sturen.
"""
import hashlib
import hmac
import json
import threading
import urllib.request
import uuid

from .db import conn, now, q, q1, tx

SVC = "svc-integratie"
STATE = {"offline": False, "webhook_url": None, "secret": b""}


class JiraFout(Exception):
    """Technische fout (onbereikbaar, 5xx, 429) → retry met backoff."""


class JiraValidatie(Exception):
    """Jira weigert de actie (bijv. workflow-validator)."""


def _check():
    if STATE["offline"]:
        raise JiraFout("Jira Cloud niet bereikbaar (gesimuleerde storing)")


def _key(c, project):
    c.execute("INSERT OR IGNORE INTO j_seq VALUES(?,100)", (project,))
    c.execute("UPDATE j_seq SET n=n+1 WHERE project=?", (project,))
    return f"{project}-{c.execute('SELECT n FROM j_seq WHERE project=?', (project,)).fetchone()[0]}"


def _issue(key):
    r = q1("SELECT * FROM j_issues WHERE key=?", (key,))
    if r:
        r["fields"] = json.loads(r["fields"] or "{}")
    return r


# ------------------------------------------------------------------ webhooks
def webhook(event, data, user):
    """Echte Jira stuurt ook webhooks bij wijzigingen door het serviceaccount; de integratie filtert die (lusvoorkoming)."""
    if not STATE["webhook_url"]:
        return
    body = json.dumps({"webhookEvent": event, "id": str(uuid.uuid4()), "timestamp": now(),
                       "user": {"accountId": user, "displayName": user}, **data}, ensure_ascii=False).encode()
    sig = "sha256=" + hmac.new(STATE["secret"], body, hashlib.sha256).hexdigest()

    def send():
        try:
            req = urllib.request.Request(STATE["webhook_url"], data=body, method="POST",
                                         headers={"Content-Type": "application/json", "X-Hub-Signature": sig})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:  # Jira probeert in werkelijkheid opnieuw; reconciliatie vangt gemiste events op
            print("[jira-sim] webhook mislukt:", e)

    threading.Thread(target=send, daemon=True).start()


# ================================================================== ADAPTER-API (serviceaccount)
class SimAdapter:
    naam = "Jira Cloud simulator"

    def create_organization(self, naam):
        _check()
        with tx() as c:
            r = c.execute("SELECT id FROM j_orgs WHERE naam=?", (naam,)).fetchone()
            if r:
                return r[0]
            return c.execute("INSERT INTO j_orgs(naam) VALUES(?)", (naam,)).lastrowid

    def rename_organization(self, org_id, naam):
        _check()
        with tx() as c:
            c.execute("UPDATE j_orgs SET naam=? WHERE id=?", (naam, org_id))

    def add_customer(self, org_id, email, naam):
        _check()
        with tx() as c:
            r = c.execute("SELECT account_id FROM j_customers WHERE email=?", (email,)).fetchone()
            if r:
                c.execute("UPDATE j_customers SET org_id=?, actief=1, naam=? WHERE account_id=?", (org_id, naam, r[0]))
                return r[0]
            aid = "qm:" + uuid.uuid4().hex[:12]
            c.execute("INSERT INTO j_customers VALUES(?,?,?,?,1)", (aid, email, naam, org_id))
            return aid

    def remove_customer(self, org_id, account_id):
        _check()
        with tx() as c:
            c.execute("UPDATE j_customers SET org_id=NULL, actief=0 WHERE account_id=?", (account_id,))

    def gdpr_request(self, account_id):
        _check()
        with tx() as c:
            c.execute("UPDATE j_customers SET email='verwijderd-'||account_id, naam='(verwijderd op verzoek)', actief=0, org_id=NULL WHERE account_id=?",
                      (account_id,))
        return {"status": "ingediend"}

    def upsert_asset(self, objtype, key, label, attrs):
        _check()
        with tx() as c:
            if not key:
                pre = {"Klant": "KLA", "Contract": "CON", "Opdracht": "OPD"}[objtype]
                key = _key(c, pre)
                c.execute("INSERT INTO j_assets VALUES(?,?,?,?,?)", (key, objtype, label, json.dumps(attrs, ensure_ascii=False), now()))
            else:
                c.execute("UPDATE j_assets SET label=?, attrs=?, updated=? WHERE key=?", (label, json.dumps(attrs, ensure_ascii=False), now(), key))
        return key

    def create_issue(self, project, issuetype, summary, description, fields, parent=None, status="Open"):
        _check()
        with tx() as c:
            key = _key(c, project)
            c.execute("INSERT INTO j_issues VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (key, project, issuetype, summary, description, status, json.dumps(fields, ensure_ascii=False), parent, now(), now(), SVC))
        webhook("jira:issue_created", {"issue": _issue(key)}, SVC)
        return key

    def update_issue(self, key, summary=None, description=None, fields=None, parent=None):
        _check()
        i = _issue(key)
        if not i:
            raise JiraValidatie(f"Issue {key} bestaat niet")
        f = {**i["fields"], **(fields or {})}
        with tx() as c:
            c.execute("UPDATE j_issues SET summary=COALESCE(?,summary), description=COALESCE(?,description), fields=?, parent=COALESCE(?,parent), updated=?, updated_by=? WHERE key=?",
                      (summary, description, json.dumps(f, ensure_ascii=False), parent, now(), SVC, key))
        webhook("jira:issue_updated", {"issue": _issue(key), "changelog": {}}, SVC)

    def get_issue(self, key):
        _check()
        return _issue(key)

    def search(self, issuetype=None):
        _check()
        rows = q("SELECT key FROM j_issues" + (" WHERE issuetype=?" if issuetype else ""), (issuetype,) if issuetype else ())
        return [_issue(r["key"]) for r in rows]

    def epic_voortgang(self, epic_key):
        _check()
        kids = q("SELECT status FROM j_issues WHERE parent=? AND issuetype!='Change'", (epic_key,))
        done = sum(1 for k in kids if k["status"] == "Done")
        uren = q1("""SELECT COALESCE(SUM(uren),0) u FROM j_worklogs WHERE issue_key=? OR issue_key IN
                     (SELECT key FROM j_issues WHERE parent=?)""", (epic_key, epic_key))["u"]
        return {"issues": len(kids), "done": done, "pct": round(100 * done / len(kids), 1) if kids else 0.0, "uren": uren}


# ================================================================== GEBRUIKERSACTIES (UI van de simulator)
WORKFLOW_DEFAULT = {"Open": ["Beoordeling"], "Beoordeling": ["CAB", "Goedgekeurd", "Afgewezen"], "CAB": [],
                    "Goedgekeurd": ["Uitgevoerd"], "Uitgevoerd": ["Gesloten"], "Afgewezen": ["Gesloten"], "Gesloten": []}


def overzicht():
    return {
        "offline": STATE["offline"],
        "orgs": q("SELECT o.*, (SELECT COUNT(*) FROM j_customers c WHERE c.org_id=o.id AND c.actief=1) klanten FROM j_orgs o"),
        "customers": q("SELECT * FROM j_customers ORDER BY org_id"),
        "assets": [{**a, "attrs": json.loads(a["attrs"])} for a in q("SELECT * FROM j_assets ORDER BY key")],
        "issues": [_issue(r["key"]) for r in q("SELECT key FROM j_issues ORDER BY created DESC")],
        "worklogs": q("SELECT * FROM j_worklogs ORDER BY id DESC LIMIT 50"),
    }


def _user_update(key, user, status=None, fields=None, summary=None, changelog=None):
    i = _issue(key)
    f = {**i["fields"], **(fields or {})}
    with tx() as c:
        c.execute("UPDATE j_issues SET status=COALESCE(?,status), summary=COALESCE(?,summary), fields=?, updated=?, updated_by=? WHERE key=?",
                  (status, summary, json.dumps(f, ensure_ascii=False), now(), user, key))
    webhook("jira:issue_updated", {"issue": _issue(key), "changelog": changelog or {}}, user)


def portaal_request(data, contract_validator):
    """Klant dient Change-request in via JSM-portaal (FR-RFC-01)."""
    cust = q1("SELECT * FROM j_customers WHERE account_id=? AND actief=1", (data["reporter"],))
    if not cust or not cust["org_id"]:
        raise JiraValidatie("Melder is geen actieve JSM Customer van een Organization")
    if not data.get("contract"):
        raise JiraValidatie("Veld 'Contract' (Assets) is verplicht")
    a = q1("SELECT * FROM j_assets WHERE key=? AND objtype='Contract'", (data["contract"],))
    if not a:
        raise JiraValidatie("Onbekend contractobject")
    fields = {"organization": cust["org_id"], "cf_contract": data["contract"], "reporter": cust["account_id"],
              "reporter_email": cust["email"], "changeType": data.get("changeType", "Normaal"),
              "priority": data.get("priority", "Midden"), "cf_gewenste_datum": data.get("gewenste_datum") or None,
              "kanaal": "portaal"}
    with tx() as c:
        key = _key(c, "CHG")
        c.execute("INSERT INTO j_issues VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (key, "CHG", "Change", data["summary"], data.get("description", ""), "Open",
                   json.dumps(fields, ensure_ascii=False), data.get("parent") or None, now(), now(), cust["account_id"]))
    webhook("jira:issue_created", {"issue": _issue(key)}, cust["account_id"])
    return key


def transitie(key, naar, user, contract_validator, workflow=None):
    wf = workflow or WORKFLOW_DEFAULT
    i = _issue(key)
    if not i:
        raise JiraValidatie("Issue niet gevonden")
    if i["issuetype"] == "Change":
        if naar not in wf.get(i["status"], []):
            raise JiraValidatie(f"Transitie {i['status']} → {naar} bestaat niet in de Change-workflow")
        if i["status"] == "Open" and naar == "Beoordeling":
            # Validator (Forge-app/Automation) bevraagt het CRM: geldig, actief contract?
            ok, reden = contract_validator(i["fields"].get("cf_contract"))
            if not ok:
                raise JiraValidatie(f"Validator 'Indienen' geweigerd door CRM: {reden}")
        if naar == "Goedgekeurd" and (i["fields"].get("cf_risicoscore") or 0) >= 15:
            raise JiraValidatie("Risicoscore ≥ 15: CAB-behandeling vereist (transitie naar CAB)")
        if naar == "CAB" and not i["fields"].get("cf_risicoscore"):
            raise JiraValidatie("Impactanalyse ontbreekt: vul eerst het formulier in")
    _user_update(key, user, status=naar, changelog={"status": {"van": i["status"], "naar": naar}})


def wijzig_velden(key, user, data):
    """Agent wijzigt velden (bidirectionele velden testen, inclusief conflicten)."""
    fields = {k: v for k, v in data.items() if k in ("changeType", "priority", "cf_contract")}
    _user_update(key, user, summary=data.get("summary") or None, fields=fields,
                 changelog={k: {"naar": v} for k, v in data.items()})


def impactanalyse(key, user, ia):
    i = _issue(key)
    score = int(ia["kans"]) * int(ia["impact"])
    versie = int(i["fields"].get("cf_ia_versie") or 0) + 1
    ia = {**ia, "versie": versie, "risicoscore": score, "opgesteld_door": user, "datum": now()}
    _user_update(key, user, fields={"cf_ia_versie": versie, "cf_risicoscore": score, "cf_kosten": ia.get("kosten"),
                                    "cf_uren": ia.get("uren"), "cf_impactanalyse": ia})
    webhook("jira:form_submitted", {"issue": {"key": key}, "form": {"naam": "Impactanalyse", "antwoorden": ia}}, user)


def approval(key, user, besluit):
    i = _issue(key)
    if i["status"] != "CAB":
        raise JiraValidatie("Approval is alleen mogelijk in status CAB")
    naar = "Goedgekeurd" if besluit == "approve" else "Afgewezen"
    _user_update(key, user, status=naar, changelog={"status": {"van": "CAB", "naar": naar}})
    webhook("jira:approval_completed", {"issue": {"key": key}, "approval": {"besluit": naar, "door": user, "datum": now()}}, user)


def maak_taak(epic, summary, user):
    with tx() as c:
        project = epic.split("-")[0]
        key = _key(c, project)
        c.execute("INSERT INTO j_issues VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (key, project, "Task", summary, "", "To Do", "{}", epic, now(), now(), user))
    webhook("jira:issue_created", {"issue": _issue(key)}, user)
    return key


def taak_status(key, status, user):
    i = _issue(key)
    _user_update(key, user, status=status, changelog={"status": {"van": i["status"], "naar": status}})


def worklog(key, uren, user):
    with tx() as c:
        wid = c.execute("INSERT INTO j_worklogs(issue_key,uren,auteur,ts) VALUES(?,?,?,?)", (key, float(uren), user, now())).lastrowid
    webhook("worklog_created", {"worklog": {"id": wid, "issueKey": key, "uren": float(uren), "auteur": user}}, user)
