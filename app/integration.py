"""Integratielaag: berichtenqueue, adapters, veld-/statusmapping, idempotentie, lusvoorkoming, conflicten,
retry met exponentiële backoff + dead-letter queue, reconciliatie en geplande taken (hoofdstuk 5)."""
import hashlib
import hmac
import json
import os
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from . import domain, jira_sim
from .db import add_months, now, q, q1, tx
from .domain import Fout, audit, config, emit, signaal
from .jira_sim import JiraFout, JiraValidatie, SVC

SYS = {"id": 0, "naam": SVC, "rol": "beheerder"}
STOP = threading.Event()


def _adapter():
    if os.environ.get("JIRA_MODE") == "cloud":
        from .jira_cloud import CloudAdapter
        return CloudAdapter()
    return jira_sim.SimAdapter()


ADAPTER = None


def adapter():
    global ADAPTER
    if ADAPTER is None:
        ADAPTER = _adapter()
    return ADAPTER


# ======================================================================== webhooks (inkomend)
def ontvang_webhook(body, signature, secret):
    """NFR-SE-05: HMAC-validatie; idempotent op event-ID; serviceaccount-wijzigingen worden genegeerd (lus)."""
    verwacht = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(verwacht, signature):
        with tx() as c:
            audit(c, "onbekend", "Integratie", "webhook", None, "geweigerd: ongeldige HMAC-handtekening")
        return 401, {"fout": "ongeldige handtekening"}
    ev = json.loads(body)
    status = "genegeerd" if ev.get("user", {}).get("accountId") == SVC else "wachtend"
    with tx() as c:
        cur = c.execute("""INSERT OR IGNORE INTO berichten(event_id,richting,type,entiteit,entiteit_id,payload,status,volgende_poging,ts,fout)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (ev["id"], "in", ev["webhookEvent"], "jira",
                         (ev.get("issue") or ev.get("worklog") or {}).get("key") or (ev.get("worklog") or {}).get("issueKey"),
                         body.decode(), status, now(), now(), "lusvoorkoming: wijziging door serviceaccount" if status == "genegeerd" else None))
        if cur.rowcount == 0:
            return 200, {"resultaat": "dubbel bericht genegeerd (idempotent)"}
    return 202, {"resultaat": "in queue", "status": status}


# ======================================================================== worker
def worker():
    while not STOP.is_set():
        try:
            if not verwerk_volgende():
                time.sleep(0.4)
        except Exception:
            traceback.print_exc()
            time.sleep(1)


def verwerk_volgende():
    m = q1("""SELECT * FROM berichten WHERE status IN ('wachtend','retry') AND volgende_poging<=?
              ORDER BY id LIMIT 1""", (now(),))
    if not m:
        return False
    verwerk(m)
    return True


def verwerk(m):
    t0 = time.time()
    retry = config("retry")
    try:
        payload = json.loads(m["payload"] or "{}")
        if m["richting"] == "uit":
            UIT[m["type"]](m["entiteit_id"], payload)
        else:
            IN.get(m["type"], lambda p: None)(payload)
        with tx() as c:
            c.execute("UPDATE berichten SET status='verwerkt', verwerkt=?, fout=NULL, duur_ms=?, pogingen=pogingen+1 WHERE id=?",
                      (now(), int((time.time() - t0) * 1000), m["id"]))
    except (JiraFout, Afhankelijk, OSError) as e:
        n = m["pogingen"] + 1
        if n >= retry["max_pogingen"]:
            st, volgende = "dlq", None
            _sync_status(m, "fout")
        else:
            st = "retry"
            volgende = (datetime.now() + timedelta(seconds=retry["basis_seconden"] * 2 ** (n - 1))).isoformat(timespec="seconds")
            _sync_status(m, "wachtend")
        with tx() as c:
            c.execute("UPDATE berichten SET status=?, pogingen=?, volgende_poging=?, fout=? WHERE id=?", (st, n, volgende, str(e), m["id"]))
    except (Fout, JiraValidatie) as e:  # functionele weigering: niet opnieuw proberen, wel zichtbaar voor beheer
        with tx() as c:
            c.execute("UPDATE berichten SET status='geweigerd', pogingen=pogingen+1, fout=?, verwerkt=? WHERE id=?", (str(e), now(), m["id"]))
        _sync_status(m, "fout")
    except Exception as e:
        traceback.print_exc()
        with tx() as c:
            c.execute("UPDATE berichten SET status='dlq', pogingen=pogingen+1, fout=? WHERE id=?", (f"{type(e).__name__}: {e}", m["id"]))


class Afhankelijk(Exception):
    """Afhankelijk object is nog niet gesynchroniseerd → later opnieuw."""


TABEL = {"klant": "klanten", "contactpersoon": "contactpersonen", "contract": "contracten", "opdracht": "opdrachten", "rfc": "rfcs"}


def _sync_status(m, st):
    t = TABEL.get(m["entiteit"])
    if t and m["richting"] == "uit":
        with tx() as c:
            c.execute(f"UPDATE {t} SET jira_sync_status=? WHERE id=?", (st, m["entiteit_id"]))


def _klaar(tabel, eid, **velden):
    velden["jira_sync_status"] = "gesynchroniseerd"
    with tx() as c:
        c.execute(f"UPDATE {tabel} SET {','.join(f'{k}=?' for k in velden)} WHERE id=?", list(velden.values()) + [eid])


# ======================================================================== UITGAAND (CRM → Jira)
def uit_klant(eid, p):
    k = q1("SELECT * FROM klanten WHERE id=?", (eid,))
    a = adapter()
    org = k["jira_org_id"]
    if not org:
        org = a.create_organization(k["naam"])
    else:
        a.rename_organization(org, k["naam"])
    asset = a.upsert_asset("Klant", k["jira_key"], k["naam"],
                           {"Klantnummer": k["nummer"], "KvK": k["kvk"], "Status": k["status"], "Organization": org})
    _klaar("klanten", eid, jira_org_id=str(org), jira_key=asset)
    with tx() as c:
        audit(c, SVC, "Integratie", "klant", eid, "gesynchroniseerd naar Jira", {"organization": org, "asset": asset})


def uit_contact(eid, p):
    cp = q1("SELECT cp.*, k.jira_org_id, k.status klantstatus FROM contactpersonen cp JOIN klanten k ON k.id=cp.klant_id WHERE cp.id=?", (eid,))
    rollen = set(json.loads(cp["rollen"] or "[]"))
    hoort = cp["actief"] and not cp["geanonimiseerd"] and cp["klantstatus"] == "Actief" and rollen & domain.JSM_ROLLEN
    a = adapter()
    if hoort:
        if not cp["jira_org_id"]:
            raise Afhankelijk("JSM Organization van klant nog niet aangemaakt")
        # NFR-PR-03 dataminimalisatie: alleen e-mail en naam naar Jira
        aid = a.add_customer(int(cp["jira_org_id"]), cp["email"], f"{cp['voornaam'] or ''} {cp['achternaam']}".strip())
        _klaar("contactpersonen", eid, jira_account_id=aid)
        actie = "toegevoegd als JSM Customer"
    elif cp["jira_account_id"]:
        a.remove_customer(int(cp["jira_org_id"] or 0), cp["jira_account_id"])
        _klaar("contactpersonen", eid, jira_account_id=None)
        actie = "verwijderd uit JSM Organization"
    else:
        _klaar("contactpersonen", eid)
        return
    with tx() as c:
        audit(c, SVC, "Integratie", "contactpersoon", eid, actie)


def uit_anonimiseer(eid, p):
    a = adapter()
    a.remove_customer(0, p["account_id"])
    a.gdpr_request(p["account_id"])
    _klaar("contactpersonen", eid, jira_account_id=None)
    with tx() as c:
        audit(c, SVC, "Integratie", "contactpersoon", eid, "AVG-verwijderverzoek ingediend bij Jira")


def _contract_velden(ct):
    """FR-CT-06: contractgegevens read-only op elke gekoppelde RFC/opdracht."""
    return {"cf_contract": ct["jira_key"], "cf_contract_nummer": ct["nummer"], "cf_contract_soort": ct["soort"],
            "cf_contract_einddatum": ct["einddatum"] or "onbepaald", "cf_sla": ct["sla_niveau"],
            "cf_change_regime": ct["change_regime"], "cf_contract_status": ct["status"]}


def uit_contract(eid, p):
    ct = q1("SELECT ct.*, k.jira_key klant_asset FROM contracten ct JOIN klanten k ON k.id=ct.klant_id WHERE ct.id=?", (eid,))
    if not ct["klant_asset"]:
        raise Afhankelijk("Assets-object Klant nog niet aangemaakt")
    a = adapter()
    key = a.upsert_asset("Contract", ct["jira_key"], ct["nummer"], {
        "Contractnummer": ct["nummer"], "Klant": ct["klant_asset"], "Soort": ct["soort"], "Startdatum": ct["startdatum"],
        "Einddatum": ct["einddatum"] or "onbepaald", "SLA": ct["sla_niveau"], "Change-regime": ct["change_regime"], "Status": ct["status"]})
    _klaar("contracten", eid, jira_key=key)
    ct["jira_key"] = key
    for r in q("SELECT jira_key FROM rfcs WHERE contract_id=? AND jira_key IS NOT NULL", (eid,)) + \
             q("SELECT jira_key FROM opdrachten WHERE contract_id=? AND jira_key IS NOT NULL", (eid,)):
        a.update_issue(r["jira_key"], fields=_contract_velden(ct))
    with tx() as c:
        audit(c, SVC, "Integratie", "contract", eid, "Assets-object Contract bijgewerkt", {"asset": key, "status": ct["status"]})


def uit_opdracht(eid, p):
    o = q1("""SELECT o.*, ct.jira_key contract_asset, k.jira_key klant_asset, k.naam klantnaam FROM opdrachten o
              JOIN contracten ct ON ct.id=o.contract_id JOIN klanten k ON k.id=ct.klant_id WHERE o.id=?""", (eid,))
    ct = q1("SELECT * FROM contracten WHERE id=?", (o["contract_id"],))
    if not o["contract_asset"]:
        raise Afhankelijk("Assets-object Contract nog niet aangemaakt")
    a = adapter()
    velden = {**_contract_velden(ct), "cf_klant": o["klant_asset"], "cf_opdrachtnummer": o["nummer"], "cf_type": o["type"],
              "cf_begrote_uren": o["begrote_uren"], "startdatum": o["startdatum"], "duedate": o["einddatum"]}
    titel = f"{o['nummer']} {o['omschrijving']}"[:250]
    if o["jira_key"]:
        a.update_issue(o["jira_key"], summary=titel, fields=velden)
        key = o["jira_key"]
    else:
        key = a.create_issue("DEL", "Epic", titel, o["omschrijving"], velden, status="To Do")
    _klaar("opdrachten", eid, jira_key=key)
    with tx() as c:
        audit(c, SVC, "Integratie", "opdracht", eid, "Epic gesynchroniseerd", {"epic": key})


def _rfc_jira_velden(r, ct):
    opd = q1("SELECT jira_key FROM opdrachten WHERE id=?", (r["opdracht_id"],)) if r["opdracht_id"] else None
    aan = q1("SELECT jira_account_id, email FROM contactpersonen WHERE id=?", (r["aanvrager_id"],))
    k = q1("SELECT jira_org_id, jira_key FROM klanten WHERE id=?", (r["klant_id"],))
    return {**_contract_velden(ct), "organization": int(k["jira_org_id"]) if k["jira_org_id"] else None, "cf_klant": k["jira_key"],
            "reporter": aan["jira_account_id"] if aan else None, "reporter_email": aan["email"] if aan else None,
            "changeType": r["change_type"], "priority": r["prioriteit"], "cf_gewenste_datum": r["gewenste_datum"],
            "cf_binnen_contract": r["binnen_contract"], "cf_crm_id": r["id"]}, (opd or {}).get("jira_key")


def uit_rfc_aanmaken(eid, p):
    r = q1("SELECT * FROM rfcs WHERE id=?", (eid,))
    if r["jira_key"]:
        return uit_rfc_sync(eid, p)
    ct = q1("SELECT * FROM contracten WHERE id=?", (r["contract_id"],))
    if not ct["jira_key"]:
        raise Afhankelijk("Contract nog niet in Assets")
    velden, parent = _rfc_jira_velden(r, ct)
    velden["kanaal"] = "CRM"
    key = adapter().create_issue("CHG", "Change", r["titel"], r["omschrijving"] or "", velden, parent=parent)
    _klaar("rfcs", eid, jira_key=key, last_sync=now())
    with tx() as c:
        audit(c, SVC, "Integratie", "rfc", eid, "Change-issue aangemaakt in JSM", {"jira_key": key})


def uit_rfc_sync(eid, p):
    r = q1("SELECT * FROM rfcs WHERE id=?", (eid,))
    if not r["jira_key"]:
        raise Afhankelijk("Change-issue nog niet aangemaakt")
    ct = q1("SELECT * FROM contracten WHERE id=?", (r["contract_id"],))
    velden, parent = _rfc_jira_velden(r, ct)
    adapter().update_issue(r["jira_key"], summary=r["titel"], description=r["omschrijving"] or "", fields=velden, parent=parent)
    _klaar("rfcs", eid, last_sync=now())


UIT = {"klant.sync": uit_klant, "contact.sync": uit_contact, "contact.anonimiseer": uit_anonimiseer,
       "contract.sync": uit_contract, "opdracht.sync": uit_opdracht, "rfc.aanmaken": uit_rfc_aanmaken, "rfc.sync": uit_rfc_sync}


# ======================================================================== INKOMEND (Jira → CRM)
def _bron_naam(ev):
    return f"{ev.get('user', {}).get('displayName', 'jira')} (Jira)"


def in_issue_created(ev):
    i = ev["issue"]
    if i["issuetype"] == "Change":
        if q1("SELECT 1 x FROM rfcs WHERE jira_key=?", (i["key"],)):
            return
        rfc_uit_jira(i, _bron_naam(ev))
    elif i.get("parent"):
        _epic_voortgang(i["parent"])


def rfc_uit_jira(i, door):
    """Scenario 'RFC via klantportaal': koppel klant via Organization, contract via Assets, aanvrager via e-mail."""
    f = i["fields"]
    k = q1("SELECT * FROM klanten WHERE jira_org_id=?", (str(f.get("organization")),))
    if not k:
        raise Fout(f"{i['key']}: Organization {f.get('organization')} is niet gekoppeld aan een CRM-klant")
    ct = q1("SELECT * FROM contracten WHERE jira_key=?", (f.get("cf_contract"),))
    if not ct:
        raise Fout(f"{i['key']}: contractobject {f.get('cf_contract')} onbekend in CRM")
    cp = q1("SELECT * FROM contactpersonen WHERE lower(email)=lower(?) OR jira_account_id=?", (f.get("reporter_email") or "", f.get("reporter") or ""))
    opd = q1("SELECT id FROM opdrachten WHERE jira_key=?", (i.get("parent"),)) if i.get("parent") else None
    status = config("statusmapping").get(i["status"], "Nieuw")
    rid = domain.rfc_aanmaken(SYS, {
        "titel": i["summary"], "omschrijving": i["description"], "klant_id": k["id"], "contract_id": ct["id"],
        "opdracht_id": opd and opd["id"], "aanvrager_id": cp and cp["id"], "change_type": f.get("changeType") or "Normaal",
        "prioriteit": f.get("priority") or "Midden", "gewenste_datum": f.get("cf_gewenste_datum"), "status": status,
    }, bron="Jira", jira_key=i["key"], gebruiker=door)
    # contracttoets-resultaat terug naar Jira (CRM → Jira veld 'Binnen contract?' + read-only contractvelden)
    with tx() as c:
        emit(c, "rfc.sync", "rfc", rid, {"velden": ["binnen_contract"]})
    return rid


BIDI = {"summary": "titel", "description": "omschrijving", "changeType": "change_type", "priority": "prioriteit",
        "cf_contract": "contract_id", "parent": "opdracht_id", "reporter": "aanvrager_id"}


def _crm_waarde(veld, jira_waarde):
    if veld == "contract_id":
        r = q1("SELECT id FROM contracten WHERE jira_key=?", (jira_waarde,))
        return r and r["id"]
    if veld == "opdracht_id":
        r = q1("SELECT id FROM opdrachten WHERE jira_key=?", (jira_waarde,)) if jira_waarde else None
        return r and r["id"]
    if veld == "aanvrager_id":
        r = q1("SELECT id FROM contactpersonen WHERE jira_account_id=?", (jira_waarde,)) if jira_waarde else None
        return r and r["id"]
    return jira_waarde


def in_issue_updated(ev):
    i = ev["issue"]
    if i["issuetype"] == "Change":
        pas_change_toe(i, _bron_naam(ev))
    elif i["issuetype"] == "Epic":
        o = q1("SELECT * FROM opdrachten WHERE jira_key=?", (i["key"],))
        if o and i["status"] == "Done" and not o["jira_epic_done"]:
            with tx() as c:
                c.execute("UPDATE opdrachten SET jira_epic_done=1, updated_at=? WHERE id=?", (now(), o["id"]))
                audit(c, _bron_naam(ev), "Jira", "opdracht", o["id"], "Epic op Done gezet; wacht op bevestiging accountmanager")
                am = c.execute("SELECT k.accountmanager_id FROM contracten ct JOIN klanten k ON k.id=ct.klant_id WHERE ct.id=?", (o["contract_id"],)).fetchone()[0]
                signaal(c, f"epicdone:{o['id']}", "opdracht", "opdracht", o["id"], f"Epic {i['key']} is Done: bevestig afronden van {o['nummer']}", am)
        _epic_voortgang(i["key"])
    elif i.get("parent"):
        _epic_voortgang(i["parent"])


def pas_change_toe(i, door):
    r = q1("SELECT * FROM rfcs WHERE jira_key=?", (i["key"],))
    if not r:
        return rfc_uit_jira(i, door)
    f = {**i["fields"], "summary": i["summary"], "description": i["description"], "parent": i.get("parent")}
    upd, w = {}, {}
    st = config("statusmapping").get(i["status"])
    if st and st != r["status"]:
        upd["status"] = st
    vts = json.loads(r["veld_ts"] or "{}")
    terugzetten = []
    for jveld, cveld in BIDI.items():
        if jveld not in f:
            continue
        nieuw = _crm_waarde(cveld, f[jveld])
        if nieuw in (None, "") and cveld in ("contract_id", "aanvrager_id"):
            continue
        if str(nieuw or "") == str(r[cveld] or ""):
            continue
        if cveld == "contract_id":  # keuze in Jira, validatie in CRM
            ct = q1("SELECT * FROM contracten WHERE id=?", (nieuw,))
            if ct["klant_id"] != r["klant_id"] or ct["status"] not in domain.ACTIEVE_CONTRACT:
                with tx() as c:
                    c.execute("INSERT INTO conflicten(entiteit,entiteit_id,veld,crm_waarde,jira_waarde,winnaar,ts) VALUES(?,?,?,?,?,?,?)",
                              ("rfc", r["id"], cveld, r[cveld], f"{f[jveld]} (ongeldig contract)", "CRM (validatie)", now()))
                terugzetten.append(cveld)
                continue
        crm_ts, sync = vts.get(cveld), r["last_sync"]
        if crm_ts and sync and crm_ts > sync:  # beide kanten gewijzigd sinds laatste sync → laatste wijziging wint
            winnaar = "Jira" if (i["updated"] or "") >= crm_ts else "CRM"
            with tx() as c:
                c.execute("INSERT INTO conflicten(entiteit,entiteit_id,veld,crm_waarde,jira_waarde,winnaar,ts) VALUES(?,?,?,?,?,?,?)",
                          ("rfc", r["id"], cveld, r[cveld], nieuw, winnaar, now()))
            if winnaar == "CRM":
                terugzetten.append(cveld)
                continue
        upd[cveld] = nieuw
    # Jira-leidende velden
    for jv, cv in (("cf_kosten", "geschatte_kosten"), ("cf_uren", "geschatte_uren")):
        if f.get(jv) is not None and str(f[jv]) != str(r[cv]):
            upd[cv] = float(f[jv])
    if f.get("cf_risicoscore") is not None:
        upd["cab_vereist"] = 1 if int(f["cf_risicoscore"]) >= config("drempels")["cab_risicoscore"] else 0
    w = domain.diff(r, upd)
    with tx() as c:
        if w:
            for k in w:
                vts[k] = i["updated"]
            c.execute(f"UPDATE rfcs SET {','.join(f'{k}=?' for k in upd)}, veld_ts=?, last_sync=?, updated_at=? WHERE id=?",
                      list(upd.values()) + [json.dumps(vts), now(), now(), r["id"]])
            audit(c, door, "Jira", "rfc", r["id"], "bijgewerkt vanuit Jira", w)
        else:
            c.execute("UPDATE rfcs SET last_sync=? WHERE id=?", (now(), r["id"]))
        if set(w) & {"geschatte_uren", "contract_id", "gewenste_datum"}:
            domain._toets_toepassen(c, r["id"], door, "Jira")
        if terugzetten:
            emit(c, "rfc.sync", "rfc", r["id"], {"velden": terugzetten})


def in_form(ev):
    """FR-IA-01..04: impactanalyse als gestructureerd, geversioneerd record."""
    key = ev["issue"]["key"]
    r = q1("SELECT * FROM rfcs WHERE jira_key=?", (key,))
    if not r:
        raise Afhankelijk(f"RFC {key} nog niet in CRM")
    a = ev["form"]["antwoorden"]
    ct = q1("SELECT * FROM contracten WHERE id=?", (r["contract_id"],))
    score = domain.risicoscore(a["kans"], a["impact"])
    verschil = domain.contractverschil(ct, {"uren": float(a.get("uren") or 0), "kosten": float(a.get("kosten") or 0),
                                             "impact_contract": a.get("impact_contract")})
    with tx() as c:
        if c.execute("SELECT 1 FROM impactanalyses WHERE rfc_id=? AND versie=?", (r["id"], a["versie"])).fetchone():
            return
        iid = c.execute("""INSERT INTO impactanalyses(rfc_id,versie,impact_techniek,toelichting_techniek,impact_organisatie,
                     toelichting_organisatie,impact_planning,kosten,uren,kans,impact,risicoscore,impact_contract,security_privacy,
                     dpia_nodig,terugvalplan,opgesteld_door,datum,contractverschil) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (r["id"], a["versie"], a.get("impact_techniek"), a.get("toelichting_techniek"), a.get("impact_organisatie"),
                         a.get("toelichting_organisatie"), a.get("impact_planning"), float(a.get("kosten") or 0), float(a.get("uren") or 0),
                         int(a["kans"]), int(a["impact"]), score, a.get("impact_contract"), a.get("security_privacy"),
                         1 if a.get("dpia_nodig") else 0, a.get("terugvalplan"), a.get("opgesteld_door"), a.get("datum"),
                         json.dumps(verschil))).lastrowid
        c.execute("UPDATE rfcs SET geschatte_kosten=?, geschatte_uren=?, cab_vereist=?, updated_at=? WHERE id=?",
                  (float(a.get("kosten") or 0), float(a.get("uren") or 0), 1 if score >= config("drempels")["cab_risicoscore"] else 0, now(), r["id"]))
        audit(c, _bron_naam(ev), "Jira", "rfc", r["id"], f"impactanalyse v{a['versie']} ontvangen", {"risicoscore": score})
        domain._toets_toepassen(c, r["id"], _bron_naam(ev), "Jira")


def in_approval(ev):
    key = ev["issue"]["key"]
    r = q1("SELECT r.*, k.accountmanager_id FROM rfcs r JOIN klanten k ON k.id=r.klant_id WHERE r.jira_key=?", (key,))
    if not r:
        raise Afhankelijk(f"RFC {key} nog niet in CRM")
    ap = ev["approval"]
    with tx() as c:
        c.execute("UPDATE rfcs SET cab_besluit=?, cab_datum=?, updated_at=? WHERE id=?", (ap["besluit"], ap["datum"][:10], now(), r["id"]))
        audit(c, f"{ap['door']} (Jira)", "Jira", "rfc", r["id"], "CAB-besluit", {"cab_besluit": ap["besluit"]})
        if ap["besluit"] == "Goedgekeurd" and r["binnen_contract"] == "Nee":
            domain._meerwerk(c, r["id"])
            signaal(c, f"offertetaak:{r['id']}", "meerwerk", "rfc", r["id"],
                    f"CAB keurde {key} goed als meerwerk: offerte opstellen en versturen", r["accountmanager_id"])


def in_worklog(ev):
    wl = ev["worklog"]
    i = adapter().get_issue(wl["issueKey"])
    epic = i["key"] if i["issuetype"] == "Epic" else i.get("parent")
    if epic:
        _epic_voortgang(epic)


def _epic_voortgang(epic_key):
    """FR-OP-03: bestede uren en voortgang uit Jira; FR-CT-04 drempelsignalering."""
    o = q1("SELECT * FROM opdrachten WHERE jira_key=?", (epic_key,))
    if not o:
        return
    v = adapter().epic_voortgang(epic_key)
    with tx() as c:
        if v["uren"] != o["bestede_uren"] or v["pct"] != o["voortgang"]:
            c.execute("UPDATE opdrachten SET bestede_uren=?, voortgang=?, updated_at=? WHERE id=?", (v["uren"], v["pct"], now(), o["id"]))
            audit(c, SVC, "Jira", "opdracht", o["id"], "uren/voortgang bijgewerkt", {"bestede_uren": {"oud": o["bestede_uren"], "nieuw": v["uren"]},
                                                                                       "voortgang": {"oud": o["voortgang"], "nieuw": v["pct"]}})
    uren_drempels(o["contract_id"])


def uren_drempels(contract_id):
    ct = q1("SELECT ct.*, k.accountmanager_id FROM contracten ct JOIN klanten k ON k.id=ct.klant_id WHERE ct.id=?", (contract_id,))
    if not ct or not ct["urenbudget"]:
        return
    besteed = domain.contract_bestede_uren(contract_id)
    pct = 100 * besteed / ct["urenbudget"]
    with tx() as c:
        for d in config("drempels")["uren_waarschuwing"]:
            if pct >= d:
                signaal(c, f"uren:{contract_id}:{d}:v{ct['versie']}", "uren", "contract", contract_id,
                        f"Contract {ct['nummer']}: {pct:.0f}% van urenbudget verbruikt (drempel {d}%)", ct["accountmanager_id"])


IN = {"jira:issue_created": in_issue_created, "jira:issue_updated": in_issue_updated, "jira:form_submitted": in_form,
      "jira:approval_completed": in_approval, "worklog_created": in_worklog}


# ======================================================================== Contractvalidator (Forge/Automation)
def contract_validator(asset_key):
    ct = q1("SELECT * FROM contracten WHERE jira_key=?", (asset_key,))
    if not ct:
        return False, "contract onbekend in CRM"
    if ct["status"] not in domain.ACTIEVE_CONTRACT:
        return False, f"contract {ct['nummer']} heeft status {ct['status']}"
    if ct["einddatum"] and ct["einddatum"] < date.today().isoformat():
        return False, f"contract {ct['nummer']} is verlopen"
    return True, "ok"


# ======================================================================== Reconciliatie
def reconciliatie():
    """Vergelijkt alle gekoppelde records en herstelt gemiste events (NFR-PE-04)."""
    t0 = time.time()
    res = {"start": now(), "gecontroleerd": 0, "hersteld": 0, "opnieuw_aangeboden": 0, "fout": None}
    try:
        a = adapter()
        open_ = {(m["entiteit"], m["entiteit_id"]) for m in q("SELECT entiteit, entiteit_id FROM berichten WHERE richting='uit' AND status IN ('wachtend','retry')")}
        with tx() as c:
            for tabel, ent, typ, cond in (("klanten", "klant", "klant.sync", "status='Actief' AND jira_key IS NULL"),
                                          ("contracten", "contract", "contract.sync", "status IN ('Actief','Verlengd') AND jira_key IS NULL"),
                                          ("opdrachten", "opdracht", "opdracht.sync", "status IN ('Gepland','Lopend') AND jira_key IS NULL"),
                                          ("rfcs", "rfc", "rfc.aanmaken", "jira_key IS NULL")):
                for r in c.execute(f"SELECT id FROM {tabel} WHERE {cond}").fetchall():
                    if (ent, str(r[0])) not in open_:
                        emit(c, typ, ent, r[0], {"bron": "reconciliatie"})
                        res["opnieuw_aangeboden"] += 1
        for i in a.search("Change"):
            res["gecontroleerd"] += 1
            r = q1("SELECT * FROM rfcs WHERE jira_key=?", (i["key"],))
            if not r or (i["updated"] or "") > (r["last_sync"] or "") and i["updated_by"] != SVC:
                try:
                    pas_change_toe(i, "reconciliatie")
                    res["hersteld"] += 1
                except Fout as e:
                    res.setdefault("geweigerd", []).append(str(e))
        for o in q("SELECT jira_key FROM opdrachten WHERE jira_key IS NOT NULL"):
            res["gecontroleerd"] += 1
            _epic_voortgang(o["jira_key"])
    except JiraFout as e:
        res["fout"] = str(e)
    res["duur_s"] = round(time.time() - t0, 2)
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO config VALUES('laatste_reconciliatie', ?)", (json.dumps(res),))
        audit(c, SVC, "Integratie", "systeem", None, "reconciliatie", res)
    return res


# ======================================================================== Dagelijkse taken
def dagtaken():
    """Contract verlopen, opzegsignalering (FR-CT-03), urendrempels, AVG-bewaartermijn (NFR-PR-04)."""
    vandaag = date.today()
    res = {"verlopen": 0, "signalen": 0, "geanonimiseerd": 0}
    drempels = config("drempels")["opzeg_signaal_maanden"]
    for ct in q("SELECT ct.*, k.accountmanager_id FROM contracten ct JOIN klanten k ON k.id=ct.klant_id WHERE ct.status IN ('Actief','Verlengd')"):
        if ct["einddatum"] and ct["einddatum"] < vandaag.isoformat():
            with tx() as c:
                c.execute("UPDATE contracten SET status='Verlopen', updated_at=? WHERE id=?", (now(), ct["id"]))
                audit(c, "Systeem", "CRM", "contract", ct["id"], "verlopen (einddatum bereikt)", {"status": {"oud": ct["status"], "nieuw": "Verlopen"}})
                emit(c, "contract.sync", "contract", ct["id"])
                signaal(c, f"verlopen:{ct['id']}:{ct['einddatum']}", "contract", "contract", ct["id"], f"Contract {ct['nummer']} is verlopen", ct["accountmanager_id"])
            res["verlopen"] += 1
            continue
        u = domain.uiterste_opzegdatum(ct)
        if not u or vandaag > u:
            continue
        gekruist = [m for m in drempels if vandaag >= add_months(u, -m)]
        if gekruist:
            m = min(gekruist)
            with tx() as c:
                before = c.total_changes
                signaal(c, f"opzeg:{ct['id']}:{m}:{ct['einddatum']}", "opzegtermijn", "contract", ct["id"],
                        f"Contract {ct['nummer']}: uiterste opzegdatum {u.isoformat()} over {(u - vandaag).days} dagen (signaal {m} mnd, {ct['verlengingsvorm']})",
                        ct["accountmanager_id"])
                res["signalen"] += c.total_changes - before
        uren_drempels(ct["id"])
    # AVG: anonimiseer contactpersonen van klanten zonder lopend contract na bewaartermijn
    termijn = config("bewaartermijn_maanden")["contactpersoon"]
    grens = add_months(vandaag, -termijn).isoformat()
    for cp in q("""SELECT cp.id FROM contactpersonen cp WHERE cp.geanonimiseerd=0 AND NOT EXISTS
                   (SELECT 1 FROM contracten ct WHERE ct.klant_id=cp.klant_id AND (ct.einddatum IS NULL OR ct.einddatum>=?))
                   AND EXISTS (SELECT 1 FROM contracten ct WHERE ct.klant_id=cp.klant_id)""", (grens,)):
        domain.contact_anonimiseren(SYS, cp["id"])
        res["geanonimiseerd"] += 1
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO config VALUES('laatste_dagtaken', ?)", (json.dumps({**res, "ts": now()}),))
    return res


def planner():
    """Dagtaken bij start en dagelijks; reconciliatie 's nachts om 02:00."""
    laatste_dag, laatste_recon = None, None
    while not STOP.is_set():
        n = datetime.now()
        if laatste_dag != n.date():
            try:
                dagtaken()
            except Exception:
                traceback.print_exc()
            laatste_dag = n.date()
        if n.hour == 2 and laatste_recon != n.date():
            try:
                reconciliatie()
            except Exception:
                traceback.print_exc()
            laatste_recon = n.date()
        STOP.wait(30)


def start():
    threading.Thread(target=worker, daemon=True, name="integratie-worker").start()
    threading.Thread(target=planner, daemon=True, name="planner").start()


# ======================================================================== beheer
def status():
    tellingen = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM berichten GROUP BY status")}
    uur = (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds")
    lat = q("SELECT duur_ms, ts, verwerkt FROM berichten WHERE status='verwerkt' AND verwerkt>=?", (uur,))
    vertraging = sorted(max(0, (datetime.fromisoformat(x["verwerkt"]) - datetime.fromisoformat(x["ts"])).total_seconds()) for x in lat)
    p95 = vertraging[int(0.95 * (len(vertraging) - 1))] if vertraging else None
    totaal = sum(tellingen.values()) or 1
    return {
        "adapter": adapter().naam, "jira_offline": jira_sim.STATE["offline"],
        "tellingen": tellingen, "queue_diepte": tellingen.get("wachtend", 0) + tellingen.get("retry", 0),
        "foutpercentage": round(100 * (tellingen.get("dlq", 0) + tellingen.get("geweigerd", 0)) / totaal, 1),
        "p95_sync_seconden": p95, "verwerkt_laatste_uur": len(lat),
        "laatste_reconciliatie": config("laatste_reconciliatie"), "laatste_dagtaken": config("laatste_dagtaken"),
        "open_conflicten": q1("SELECT COUNT(*) n FROM conflicten WHERE opgelost=0")["n"],
        "health": "ok" if not jira_sim.STATE["offline"] and tellingen.get("dlq", 0) == 0 else "verstoord",
    }


def opnieuw(bericht_id):
    with tx() as c:
        c.execute("UPDATE berichten SET status='wachtend', pogingen=0, volgende_poging=?, fout=NULL WHERE id=? AND status IN ('dlq','geweigerd','retry')",
                  (now(), bericht_id))
