"""Domeinlogica: CRUD, bedrijfsregels (contracttoets, signalering, risicoscore), RBAC en audit trail."""
import json
import re
import uuid
from datetime import date, timedelta

from . import db
from .db import q, q1, tx, now, add_months


class Fout(Exception):
    def __init__(self, msg, code=400, extra=None):
        super().__init__(msg)
        self.code = code
        self.extra = extra or {}


# ---------------------------------------------------------------- keuzelijsten
KEUZES = {
    "klant_status": ["Prospect", "Actief", "Inactief", "Beëindigd"],
    "branche": ["Overheid", "Zorg", "Onderwijs", "Logistiek", "Financiële dienstverlening", "Industrie", "Retail", "Creatief", "Overig"],
    "rollen": ["Beslisser", "CAB-lid", "Technisch contact", "Financieel contact", "Escalatie"],
    "communicatievoorkeur": ["E-mail", "Telefoon", "Portaal"],
    "avg_grondslag": ["Overeenkomst", "Gerechtvaardigd belang", "Toestemming"],
    "contract_soort": ["Raamovereenkomst", "Managed service / SLA", "Detachering", "Fixed price project", "Time & material", "Licentie / support"],
    "verlengingsvorm": ["Geen", "Stilzwijgend", "Optie tot verlenging"],
    "sla_niveau": ["Bronze", "Silver", "Gold", "Platinum"],
    "change_regime": ["Binnen contract", "Meerwerk via offerte", "Strippenkaart"],
    "contract_status": ["Concept", "Actief", "Verlengd", "Opgezegd", "Verlopen"],
    "opdracht_type": ["Project", "Detachering", "Managed service", "Advies"],
    "opdracht_status": ["Gepland", "Lopend", "Afgerond", "Gestopt"],
    "change_type": ["Standaard", "Normaal", "Spoed"],
    "prioriteit": ["Laag", "Midden", "Hoog", "Kritiek"],
    "rfc_status": ["Nieuw", "Beoordeling", "CAB", "Goedgekeurd", "Afgewezen", "Uitgevoerd", "Gesloten"],
    "impact": ["Laag", "Midden", "Hoog"],
    "activiteit_type": ["Notitie", "E-mail", "Afspraak", "Telefoon"],
    "taal": ["Nederlands", "Engels", "Duits", "Frans"],
    "besluitstijl": ["Analytisch (cijfers en onderbouwing)", "Pragmatisch (snel en concreet)", "Relatiegericht (vertrouwen en draagvlak)", "Visionair (strategie en grote lijnen)"],
    "houding": ["Ambassadeur", "Positief", "Neutraal", "Kritisch"],
}
ACTIEVE_CONTRACT = ("Actief", "Verlengd")
JSM_ROLLEN = {"Technisch contact", "CAB-lid"}

# ---------------------------------------------------------------- RBAC (hoofdstuk 6)
ROLLEN = {
    "accountmanager": "Accountmanager", "servicemanager": "Servicemanager / change manager",
    "consultant": "Consultant / engineer", "directie": "Directie", "finance": "Finance",
    "beheerder": "Functioneel beheerder",
}
WAARDE_ZICHTBAAR = {"accountmanager", "directie", "finance", "beheerder"}
FIN_VELDEN = {"waarde", "urenbudget"}


def mag(user, actie, entiteit, record=None):
    rol = user["rol"]
    if rol == "beheerder":
        return True
    if actie == "lezen":
        return True
    eigen = record is None or record.get("accountmanager_id") in (None, user["id"])
    if entiteit in ("klant", "contactpersoon", "activiteit"):
        return rol == "accountmanager" and eigen
    if entiteit == "contract":
        return (rol == "accountmanager" and eigen) or rol == "finance"
    if entiteit in ("opdracht", "offerte"):
        return rol == "accountmanager" and eigen
    if entiteit == "rfc":
        if actie == "aanmaken":
            return rol in ("accountmanager", "servicemanager")
        return rol == "servicemanager"
    if entiteit in ("beheer",):
        return False
    return False


def eis(user, actie, entiteit, record=None):
    if not mag(user, actie, entiteit, record):
        raise Fout(f"Geen rechten: rol '{ROLLEN.get(user['rol'], user['rol'])}' mag '{actie}' niet op {entiteit}", 403)


def filter_contract(user, c):
    if c and user["rol"] not in WAARDE_ZICHTBAAR:
        c = dict(c)
        c["waarde"] = None
        c["waarde_verborgen"] = True
    return c


CP_PERSOONLIJK = ["verjaardag", "persoonlijk_gezin", "persoonlijk_interesses", "persoonlijk_notities"]
CP_ZAKELIJK = ["voorkeur_taal", "voorkeur_contactmoment", "voorkeur_kanaal_detail", "besluitstijl", "houding", "invloed",
               "linkedin", "assistent", "zakelijke_interesses", "zakelijke_notities"]
PERSOONLIJK_ZICHTBAAR = {"accountmanager", "directie", "beheerder"}  # need-to-know: relatiebeheer


def filter_contact(user, c):
    if not c:
        return c
    c = dict(c)
    if user["rol"] == "consultant":  # lezen (beperkt)
        c["telefoon"] = None
        c["avg_grondslag"] = "(verborgen)"
        for f in CP_ZAKELIJK:
            c[f] = None
        c["zakelijk_verborgen"] = True
    if user["rol"] not in PERSOONLIJK_ZICHTBAAR:
        for f in CP_PERSOONLIJK:
            c[f] = None
        c["persoonlijk_verborgen"] = True
    return c


# ---------------------------------------------------------------- audit & events
def audit(c, gebruiker, bron, entiteit, eid, actie, wijzigingen=None):
    c.execute("INSERT INTO audit(ts,gebruiker,bron,entiteit,entiteit_id,actie,wijzigingen) VALUES(?,?,?,?,?,?,?)",
              (now(), gebruiker, bron, entiteit, eid, actie, json.dumps(wijzigingen or {}, ensure_ascii=False, default=str)))


def emit(c, type_, entiteit, eid, payload=None, richting="uit", event_id=None):
    """CRM domain event → berichtenqueue van de integratielaag (outbox-patroon, zelfde transactie)."""
    c.execute("""INSERT OR IGNORE INTO berichten(event_id,richting,type,entiteit,entiteit_id,payload,status,volgende_poging,ts)
                 VALUES(?,?,?,?,?,?, 'wachtend', ?, ?)""",
              (event_id or str(uuid.uuid4()), richting, type_, entiteit, str(eid),
               json.dumps(payload or {}, ensure_ascii=False, default=str), now(), now()))


def diff(oud, nieuw):
    return {k: {"oud": oud.get(k), "nieuw": v} for k, v in nieuw.items() if str(oud.get(k)) != str(v)}


def config(sleutel):
    r = q1("SELECT waarde FROM config WHERE sleutel=?", (sleutel,))
    return json.loads(r["waarde"]) if r else None


def signaal(c, sleutel, type_, entiteit, eid, bericht, ontvanger_id):
    c.execute("INSERT OR IGNORE INTO signalen(sleutel,type,entiteit,entiteit_id,bericht,ontvanger_id,ts) VALUES(?,?,?,?,?,?,?)",
              (sleutel, type_, entiteit, eid, bericht, ontvanger_id, now()))


# ---------------------------------------------------------------- hulpfuncties
def pdate(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        raise Fout(f"Ongeldige datum: {s}")


def verplicht(data, velden):
    mis = [v for v in velden if data.get(v) in (None, "", [])]
    if mis:
        raise Fout("Verplichte velden ontbreken: " + ", ".join(mis), extra={"velden": mis})


def keuze(data, veld, lijst):
    v = data.get(veld)
    if v not in (None, "") and v not in KEUZES[lijst]:
        raise Fout(f"Ongeldige waarde voor {veld}: {v}")


def num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise Fout(f"Geen geldig getal: {v}")


def volgnummer(c, tabel, prefix, jaar=True):
    pre = f"{prefix}-{date.today().year}-" if jaar else f"{prefix}-"
    r = c.execute(f"SELECT nummer FROM {tabel} WHERE nummer LIKE ? ORDER BY nummer DESC LIMIT 1", (pre + "%",)).fetchone()
    n = int(r[0].split("-")[-1]) + 1 if r else 1
    return f"{pre}{n:04d}"


# ================================================================ KLANTEN
KLANT_VELDEN = ["naam", "kvk", "btw", "bezoekadres", "postadres", "factuuradres", "plaats", "email_domein", "branche",
                "accountmanager_id", "status"]


def dubbele_klanten(data, negeer_id=None):
    """FR-KL-02: controle op KvK, naam en e-maildomein."""
    hits = []
    for k in q("SELECT id,nummer,naam,kvk,email_domein,plaats FROM klanten WHERE gearchiveerd=0"):
        if k["id"] == negeer_id:
            continue
        reden = []
        if data.get("kvk") and k["kvk"] == data["kvk"]:
            reden.append("zelfde KvK-nummer")
        norm = lambda s: re.sub(r"\b(b\.?v\.?|n\.?v\.?|vof|holding)\b|[^a-z0-9]", "", (s or "").lower())
        if data.get("naam") and norm(k["naam"]) == norm(data["naam"]):
            reden.append("zelfde naam")
        if data.get("email_domein") and k["email_domein"] and k["email_domein"].lower() == data["email_domein"].lower():
            reden.append("zelfde e-maildomein")
        if reden:
            hits.append({**k, "reden": reden})
    return hits


def valideer_klant(data):
    verplicht(data, ["naam", "kvk", "bezoekadres", "accountmanager_id", "status"])
    if not re.fullmatch(r"\d{8}", str(data["kvk"])):
        raise Fout("KvK-nummer moet uit precies 8 cijfers bestaan")
    if data.get("btw") and not re.fullmatch(r"NL\d{9}B\d{2}", data["btw"].replace(" ", "").upper()):
        raise Fout("BTW-nummer heeft geen geldig NL-formaat (NL123456789B01)")
    keuze(data, "status", "klant_status")
    keuze(data, "branche", "branche")


def klant_aanmaken(user, data, forceer=False):
    eis(user, "schrijven", "klant", {"accountmanager_id": data.get("accountmanager_id") and int(data["accountmanager_id"])})
    data = {k: data.get(k) for k in KLANT_VELDEN}
    data.setdefault("status", "Prospect")
    data["status"] = data["status"] or "Prospect"
    valideer_klant(data)
    dub = dubbele_klanten(data)
    if dub and not forceer:
        raise Fout("Mogelijk dubbele klant gevonden", 409, {"dubbelen": dub})
    with tx() as c:
        data["nummer"] = volgnummer(c, "klanten", "KL", jaar=False)
        cols = list(data)
        cur = c.execute(f"INSERT INTO klanten({','.join(cols)},created_at,updated_at) VALUES({','.join('?' * len(cols))},?,?)",
                        [data[k] for k in cols] + [now(), now()])
        kid = cur.lastrowid
        audit(c, user["naam"], "CRM", "klant", kid, "aangemaakt", data)
        if data["status"] == "Actief":
            _klant_actief(c, kid)
    return klant(user, kid)


def _klant_actief(c, kid):
    c.execute("UPDATE klanten SET jira_sync_status='wachtend' WHERE id=?", (kid,))
    emit(c, "klant.sync", "klant", kid)
    for cp in c.execute("SELECT id FROM contactpersonen WHERE klant_id=?", (kid,)).fetchall():
        emit(c, "contact.sync", "contactpersoon", cp[0])


def klant_wijzigen(user, kid, data, bron="CRM"):
    oud = q1("SELECT * FROM klanten WHERE id=?", (kid,))
    if not oud:
        raise Fout("Klant niet gevonden", 404)
    eis(user, "schrijven", "klant", oud)
    nieuw = {k: data[k] for k in KLANT_VELDEN if k in data}
    valideer_klant({**oud, **nieuw})
    wijz = diff(oud, nieuw)
    if not wijz:
        return klant(user, kid)
    with tx() as c:
        sets = ",".join(f"{k}=?" for k in nieuw)
        c.execute(f"UPDATE klanten SET {sets}, updated_at=? WHERE id=?", list(nieuw.values()) + [now(), kid])
        audit(c, user["naam"], bron, "klant", kid, "gewijzigd", wijz)
        if "status" in wijz and nieuw["status"] == "Actief":
            _klant_actief(c, kid)  # FR-KL-04
        elif oud["jira_org_id"]:
            emit(c, "klant.sync", "klant", kid)
    return klant(user, kid)


def klant_archiveren(user, kid):
    oud = q1("SELECT * FROM klanten WHERE id=?", (kid,))
    eis(user, "schrijven", "klant", oud)
    with tx() as c:
        c.execute("UPDATE klanten SET gearchiveerd=1, status='Inactief', updated_at=? WHERE id=?", (now(), kid))
        audit(c, user["naam"], "CRM", "klant", kid, "gearchiveerd")
        if oud["jira_org_id"]:
            emit(c, "klant.sync", "klant", kid)
    return {"ok": True}


def klanten(user, zoek=None, status=None, archief=False):
    sql = """SELECT k.*, u.naam accountmanager,
               (SELECT COUNT(*) FROM contracten ct WHERE ct.klant_id=k.id AND ct.status IN ('Actief','Verlengd')) actieve_contracten,
               (SELECT COUNT(*) FROM rfcs r WHERE r.klant_id=k.id AND r.status NOT IN ('Gesloten','Afgewezen')) open_rfcs
             FROM klanten k LEFT JOIN users u ON u.id=k.accountmanager_id WHERE k.gearchiveerd=?"""
    args = [1 if archief else 0]
    if zoek:
        sql += " AND (k.naam LIKE ? OR k.kvk LIKE ? OR k.plaats LIKE ? OR k.nummer LIKE ?)"
        args += [f"%{zoek}%"] * 4
    if status:
        sql += " AND k.status=?"
        args.append(status)
    rows = q(sql + " ORDER BY k.naam", args)
    stats = offerte_stats(user)
    for r in rows:
        r["offertes"] = stats.get(r["id"], offerte_stats_leeg())
    return rows


def offerte_stats_leeg():
    return {"totaal": 0, "concept": 0, "uitgebracht": 0, "lopend": 0, "gewonnen": 0, "afgewezen": 0,
            "scoringspct": None, "waarde_gewonnen": None, "waarde_uitgebracht": None}


def offerte_stats(user, klant_id=None):
    """Per klant: uitgebracht (Verstuurd/Geaccepteerd/Afgewezen), gewonnen, afgewezen, lopend, concept en scoringspercentage."""
    sql = """SELECT klant_id, COUNT(*) totaal,
               SUM(status='Concept') concept, SUM(status='Verstuurd') lopend,
               SUM(status='Geaccepteerd') gewonnen, SUM(status='Afgewezen') afgewezen,
               SUM(CASE WHEN status='Geaccepteerd' THEN COALESCE(waarde,0) END) waarde_gewonnen,
               SUM(CASE WHEN status!='Concept' THEN COALESCE(waarde,0) END) waarde_uitgebracht
             FROM offertes""" + (" WHERE klant_id=?" if klant_id else "") + " GROUP BY klant_id"
    out = {}
    for r in q(sql, (klant_id,) if klant_id else ()):
        beslist = r["gewonnen"] + r["afgewezen"]
        s = {**r, "uitgebracht": r["totaal"] - r["concept"],
             "scoringspct": round(100 * r["gewonnen"] / beslist) if beslist else None}
        if user["rol"] not in WAARDE_ZICHTBAAR:
            s["waarde_gewonnen"] = s["waarde_uitgebracht"] = None
        out[r.pop("klant_id")] = s
    return out


def klant(user, kid):
    """FR-KL-03: klantkaart in één scherm."""
    k = q1("SELECT k.*, u.naam accountmanager FROM klanten k LEFT JOIN users u ON u.id=k.accountmanager_id WHERE k.id=?", (kid,))
    if not k:
        raise Fout("Klant niet gevonden", 404)
    k["contactpersonen"] = [filter_contact(user, _cp_out(x)) for x in q("SELECT * FROM contactpersonen WHERE klant_id=? ORDER BY actief DESC, achternaam", (kid,))]
    k["contracten"] = [filter_contract(user, _contract_extra(x)) for x in q("SELECT * FROM contracten WHERE klant_id=? ORDER BY startdatum DESC", (kid,))]
    k["opdrachten"] = q("""SELECT o.*, ct.nummer contractnummer FROM opdrachten o JOIN contracten ct ON ct.id=o.contract_id
                           WHERE ct.klant_id=? ORDER BY o.startdatum DESC""", (kid,))
    k["rfcs"] = q("""SELECT r.*, ct.nummer contractnummer FROM rfcs r JOIN contracten ct ON ct.id=r.contract_id
                     WHERE r.klant_id=? ORDER BY r.created_at DESC""", (kid,))
    n = opm_tellingen("contactpersoon", [c["id"] for c in k["contactpersonen"]])
    for c in k["contactpersonen"]:
        c["opmerkingen_aantal"] = n.get(c["id"], 0)
    k["tijdlijn"] = tijdlijn(kid)
    k["mag_schrijven"] = mag(user, "schrijven", "klant", k)
    k["opmerkingen_aantal"] = opm_tellingen("klant", [kid]).get(kid, 0)
    k["offerte_stats"] = offerte_stats(user, kid).get(kid, offerte_stats_leeg())
    k["offertes"] = [_offerte_out(o) for o in q("""SELECT o.*, r.jira_key, r.titel FROM offertes o JOIN rfcs r ON r.id=o.rfc_id
                                                    WHERE o.klant_id=? ORDER BY o.id DESC""", (kid,))]
    if user["rol"] not in WAARDE_ZICHTBAAR:
        for o in k["offertes"]:
            o["waarde"] = None
    return k


def tijdlijn(kid):
    """FR-KL-06: activiteiten + wijzigingen + Jira-activiteit."""
    items = [{"ts": a["ts"], "type": a["type"], "tekst": a["tekst"], "door": a["gebruiker"], "bron": "CRM"}
             for a in q("SELECT * FROM activiteiten WHERE klant_id=?", (kid,))]
    ids = {
        "klant": [kid],
        "contactpersoon": [r["id"] for r in q("SELECT id FROM contactpersonen WHERE klant_id=?", (kid,))],
        "contract": [r["id"] for r in q("SELECT id FROM contracten WHERE klant_id=?", (kid,))],
        "opdracht": [r["id"] for r in q("SELECT o.id FROM opdrachten o JOIN contracten c ON c.id=o.contract_id WHERE c.klant_id=?", (kid,))],
        "rfc": [r["id"] for r in q("SELECT id FROM rfcs WHERE klant_id=?", (kid,))],
    }
    for ent, lst in ids.items():
        if not lst:
            continue
        for a in q(f"SELECT * FROM audit WHERE entiteit=? AND entiteit_id IN ({','.join('?' * len(lst))}) ORDER BY id DESC LIMIT 60", [ent] + lst):
            w = json.loads(a["wijzigingen"] or "{}")
            samenvatting = ", ".join(f"{k}: {v.get('nieuw') if isinstance(v, dict) else v}" for k, v in list(w.items())[:4]) if a["actie"] != "aangemaakt" else ""
            items.append({"ts": a["ts"], "type": "Jira" if a["bron"] != "CRM" else "Wijziging",
                          "tekst": f"{ent} #{a['entiteit_id']} {a['actie']}" + (f" — {samenvatting}" if samenvatting else ""),
                          "door": a["gebruiker"], "bron": a["bron"]})
        for o in q(f"SELECT * FROM opmerkingen WHERE verwijderd=0 AND entiteit=? AND entiteit_id IN ({','.join('?' * len(lst))})", [ent] + lst):
            items.append({"ts": o["ts"], "type": "Opmerking", "tekst": f"{opm_label(ent, o['entiteit_id'])}: {o['tekst'][:300]}",
                          "door": o["gebruiker"], "bron": "CRM"})
    return sorted(items, key=lambda x: x["ts"], reverse=True)[:80]


# ================================================================ OPMERKINGEN
OPM_TABELLEN = {"klant": "klanten", "contactpersoon": "contactpersonen", "contract": "contracten", "opdracht": "opdrachten", "rfc": "rfcs"}
OPM_MAX = 5000


def opm_label(ent, eid):
    r = q1(f"SELECT * FROM {OPM_TABELLEN[ent]} WHERE id=?", (eid,)) or {}
    if ent == "klant":
        return f"Klant {r.get('naam', eid)}"
    if ent == "contactpersoon":
        return f"Contactpersoon {(r.get('voornaam') or '') + ' ' + (r.get('achternaam') or '')}".strip()
    if ent == "rfc":
        return f"RFC {r.get('jira_key') or '#' + str(eid)}"
    return f"{ent.capitalize()} {r.get('nummer', eid)}"


def opm_tellingen(ent, ids):
    if not ids:
        return {}
    return {r["entiteit_id"]: r["n"] for r in q(
        f"SELECT entiteit_id, COUNT(*) n FROM opmerkingen WHERE verwijderd=0 AND entiteit=? AND entiteit_id IN ({','.join('?' * len(ids))}) GROUP BY entiteit_id",
        [ent] + list(ids))}


def _opm_record(ent, eid):
    if ent not in OPM_TABELLEN:
        raise Fout(f"Opmerkingen niet mogelijk op '{ent}'")
    if not q1(f"SELECT id FROM {OPM_TABELLEN[ent]} WHERE id=?", (eid,)):
        raise Fout("Record niet gevonden", 404)


def opmerkingen(user, ent, eid):
    _opm_record(ent, eid)
    rows = q("SELECT * FROM opmerkingen WHERE entiteit=? AND entiteit_id=? AND verwijderd=0 ORDER BY id DESC", (ent, eid))
    for r in rows:
        r["eigen"] = r["gebruiker_id"] == user["id"]
        r["mag_beheren"] = r["eigen"] or user["rol"] == "beheerder"
    return rows


def _opm_tekst(tekst):
    tekst = (tekst or "").strip()
    if not tekst:
        raise Fout("Opmerking is leeg")
    if len(tekst) > OPM_MAX:
        raise Fout(f"Opmerking is te lang (max. {OPM_MAX} tekens)")
    return tekst


def opmerking_plaatsen(user, ent, eid, tekst):
    """Iedere ingelogde gebruiker die het record mag lezen, mag een opmerking plaatsen."""
    _opm_record(ent, eid)
    tekst = _opm_tekst(tekst)
    with tx() as c:
        oid = c.execute("INSERT INTO opmerkingen(entiteit,entiteit_id,tekst,gebruiker_id,gebruiker,ts) VALUES(?,?,?,?,?,?)",
                        (ent, eid, tekst, user["id"], user["naam"], now())).lastrowid
        audit(c, user["naam"], "CRM", ent, eid, "opmerking geplaatst", {"opmerking": tekst[:200]})
    return q1("SELECT * FROM opmerkingen WHERE id=?", (oid,))


def _opm_eigen(user, oid):
    o = q1("SELECT * FROM opmerkingen WHERE id=? AND verwijderd=0", (oid,))
    if not o:
        raise Fout("Opmerking niet gevonden", 404)
    if o["gebruiker_id"] != user["id"] and user["rol"] != "beheerder":
        raise Fout("Je kunt alleen je eigen opmerkingen wijzigen of verwijderen", 403)
    return o


def opmerking_wijzigen(user, oid, tekst):
    o = _opm_eigen(user, oid)
    tekst = _opm_tekst(tekst)
    with tx() as c:
        c.execute("UPDATE opmerkingen SET tekst=?, bewerkt=? WHERE id=?", (tekst, now(), oid))
        audit(c, user["naam"], "CRM", o["entiteit"], o["entiteit_id"], "opmerking gewijzigd",
              {"opmerking": {"oud": o["tekst"][:200], "nieuw": tekst[:200]}})
    return {"ok": True}


def opmerking_verwijderen(user, oid):
    """Zacht verwijderen: de tekst blijft voor de audit trail bewaard."""
    o = _opm_eigen(user, oid)
    with tx() as c:
        c.execute("UPDATE opmerkingen SET verwijderd=1, bewerkt=? WHERE id=?", (now(), oid))
        audit(c, user["naam"], "CRM", o["entiteit"], o["entiteit_id"], "opmerking verwijderd", {"opmerking": o["tekst"][:200]})
    return {"ok": True}


def activiteit_toevoegen(user, kid, data):
    k = q1("SELECT * FROM klanten WHERE id=?", (kid,))
    eis(user, "schrijven", "activiteit", k)
    verplicht(data, ["type", "tekst"])
    keuze(data, "type", "activiteit_type")
    with tx() as c:
        c.execute("INSERT INTO activiteiten(klant_id,type,tekst,gebruiker,ts) VALUES(?,?,?,?,?)",
                  (kid, data["type"], data["tekst"], user["naam"], now()))
    return {"ok": True}


# ================================================================ CONTACTPERSONEN
CP_VELDEN = ["voornaam", "achternaam", "email", "telefoon", "functie", "rollen", "communicatievoorkeur", "avg_grondslag"]


def _cp_out(r):
    r = dict(r)
    r["rollen"] = json.loads(r.get("rollen") or "[]")
    r["naam"] = f"{r.get('voornaam') or ''} {r['achternaam']}".strip()
    return r


def contact(user, cid, details=False):
    r = q1("""SELECT cp.*, k.naam klantnaam, k.accountmanager_id, k.status klantstatus FROM contactpersonen cp
              JOIN klanten k ON k.id=cp.klant_id WHERE cp.id=?""", (cid,))
    if not r:
        raise Fout("Contactpersoon niet gevonden", 404)
    r = filter_contact(user, _cp_out(r))
    if details:
        r["rfcs"] = q("""SELECT r.id, r.jira_key, r.titel, r.status, r.change_type, rc.rol FROM rfc_contacten rc
                         JOIN rfcs r ON r.id=rc.rfc_id WHERE rc.contact_id=? ORDER BY r.created_at DESC""", (cid,))
        r["opdrachten"] = q("""SELECT o.*, ct.nummer contractnummer FROM opdrachten o JOIN contracten ct ON ct.id=o.contract_id
                               WHERE o.opdrachtgever_id=? ORDER BY o.startdatum DESC""", (cid,))
        r["opmerkingen_aantal"] = opm_tellingen("contactpersoon", [cid]).get(cid, 0)
        r["mag_schrijven"] = mag(user, "schrijven", "contactpersoon", r) and not r["geanonimiseerd"]
    return r


PROFIEL_KEUZES = {"voorkeur_taal": "taal", "besluitstijl": "besluitstijl", "houding": "houding", "invloed": "impact"}


def contact_profiel_opslaan(user, cid, data):
    """Persoonlijke informatie en zakelijke voorkeuren. Waarden komen niet in de audit trail (alleen veldnamen)
    en worden nooit naar Jira gesynchroniseerd (NFR-PR-03)."""
    oud = q1("SELECT cp.*, k.accountmanager_id FROM contactpersonen cp JOIN klanten k ON k.id=cp.klant_id WHERE cp.id=?", (cid,))
    if not oud:
        raise Fout("Contactpersoon niet gevonden", 404)
    eis(user, "schrijven", "contactpersoon", oud)
    if oud["geanonimiseerd"]:
        raise Fout("Geanonimiseerde contactpersoon kan niet worden gewijzigd")
    toegestaan = CP_ZAKELIJK + (CP_PERSOONLIJK if user["rol"] in PERSOONLIJK_ZICHTBAAR else [])
    nieuw = {f: (str(data[f]).strip() or None) if data[f] is not None else None for f in toegestaan if f in data}
    for veld, lijst in PROFIEL_KEUZES.items():
        keuze(nieuw, veld, lijst)
    if nieuw.get("verjaardag"):
        pdate(nieuw["verjaardag"])
    if nieuw.get("linkedin") and not re.match(r"^https://([a-z]{2,3}\.)?linkedin\.com/", nieuw["linkedin"], re.I):
        raise Fout("LinkedIn moet een https://linkedin.com/…-adres zijn")
    for f, v in nieuw.items():
        if v and len(v) > 4000:
            raise Fout(f"{f} is te lang (max. 4000 tekens)")
    gewijzigd = [k for k in nieuw if (oud[k] or None) != nieuw[k]]
    if not gewijzigd:
        return contact(user, cid, details=True)
    with tx() as c:
        c.execute(f"UPDATE contactpersonen SET {','.join(f'{k}=?' for k in gewijzigd)}, updated_at=? WHERE id=?",
                  [nieuw[k] for k in gewijzigd] + [now(), cid])
        audit(c, user["naam"], "CRM", "contactpersoon", cid, "profiel bijgewerkt", {"velden": ", ".join(gewijzigd)})
    return contact(user, cid, details=True)


def contact_opslaan(user, data, cid=None):
    oud = q1("SELECT * FROM contactpersonen WHERE id=?", (cid,)) if cid else None
    kid = int(oud["klant_id"] if oud else data.get("klant_id") or 0)
    k = q1("SELECT * FROM klanten WHERE id=?", (kid,))
    if not k:
        raise Fout("Klant is verplicht")
    eis(user, "schrijven", "contactpersoon", k)
    if oud and oud["geanonimiseerd"]:
        raise Fout("Geanonimiseerde contactpersoon kan niet worden gewijzigd")
    nieuw = {f: data[f] for f in CP_VELDEN if f in data}
    if "rollen" in nieuw:
        rollen = nieuw["rollen"] if isinstance(nieuw["rollen"], list) else [x.strip() for x in str(nieuw["rollen"]).split(",") if x.strip()]
        for r in rollen:
            if r not in KEUZES["rollen"]:
                raise Fout(f"Onbekende rol: {r}")
        nieuw["rollen"] = json.dumps(rollen)
    merged = {**(oud or {}), **nieuw}
    verplicht(merged, ["achternaam", "email", "avg_grondslag"])
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", merged["email"], re.I):
        raise Fout("Ongeldig e-mailadres")
    keuze(merged, "avg_grondslag", "avg_grondslag")
    keuze(merged, "communicatievoorkeur", "communicatievoorkeur")
    dup = q1("SELECT id FROM contactpersonen WHERE lower(email)=lower(?) AND id IS NOT ?", (merged["email"], cid))
    if dup:
        raise Fout("Er bestaat al een contactpersoon met dit e-mailadres (sleutel voor JSM Customer)")
    with tx() as c:
        if oud:
            w = diff(oud, nieuw)
            if not w:
                return contact(user, cid)
            c.execute(f"UPDATE contactpersonen SET {','.join(f'{k}=?' for k in nieuw)}, updated_at=? WHERE id=?",
                      list(nieuw.values()) + [now(), cid])
            audit(c, user["naam"], "CRM", "contactpersoon", cid, "gewijzigd", w)
        else:
            nieuw.setdefault("rollen", "[]")
            cols = list(nieuw)
            cid = c.execute(f"INSERT INTO contactpersonen(klant_id,{','.join(cols)},created_at,updated_at) VALUES(?,{','.join('?' * len(cols))},?,?)",
                            [kid] + list(nieuw.values()) + [now(), now()]).lastrowid
            audit(c, user["naam"], "CRM", "contactpersoon", cid, "aangemaakt", nieuw)
        if k["status"] == "Actief":
            emit(c, "contact.sync", "contactpersoon", cid)  # FR-CP-03
    return contact(user, cid)


def contact_actief(user, cid, actief):
    oud = q1("SELECT cp.*, k.accountmanager_id, k.status klantstatus FROM contactpersonen cp JOIN klanten k ON k.id=cp.klant_id WHERE cp.id=?", (cid,))
    eis(user, "schrijven", "contactpersoon", oud)
    with tx() as c:
        c.execute("UPDATE contactpersonen SET actief=?, updated_at=? WHERE id=?", (1 if actief else 0, now(), cid))
        audit(c, user["naam"], "CRM", "contactpersoon", cid, "geactiveerd" if actief else "gedeactiveerd")
        if oud["klantstatus"] == "Actief" or oud["jira_account_id"]:
            emit(c, "contact.sync", "contactpersoon", cid)  # FR-CP-04
    return contact(user, cid)


def contact_anonimiseren(user, cid):
    """FR-CP-05 / NFR-PR-05: verwijderverzoek → anonimisering in CRM en verzoek in Jira."""
    oud = q1("SELECT cp.*, k.accountmanager_id FROM contactpersonen cp JOIN klanten k ON k.id=cp.klant_id WHERE cp.id=?", (cid,))
    eis(user, "schrijven", "contactpersoon", oud)
    with tx() as c:
        profiel = ", ".join(f"{f}=NULL" for f in CP_PERSOONLIJK + CP_ZAKELIJK)
        c.execute(f"""UPDATE contactpersonen SET voornaam=NULL, achternaam=?, email=?, telefoon=NULL, functie=NULL,
                     rollen='[]', actief=0, geanonimiseerd=1, {profiel}, updated_at=? WHERE id=?""",
                  (f"Geanonimiseerd #{cid}", f"geanonimiseerd-{cid}@invalid.local", now(), cid))
        audit(c, user["naam"], "CRM", "contactpersoon", cid, "geanonimiseerd (AVG-verwijderverzoek)")
        if oud["jira_account_id"]:
            emit(c, "contact.anonimiseer", "contactpersoon", cid, {"account_id": oud["jira_account_id"], "email": oud["email"]})
    return {"ok": True}


# ================================================================ CONTRACTEN
CT_VELDEN = ["klant_id", "soort", "startdatum", "einddatum", "onbepaalde_tijd", "verlengingsvorm", "opzegtermijn", "waarde",
             "urenbudget", "sla_niveau", "change_regime", "status"]


def uiterste_opzegdatum(ct):
    e = pdate(ct.get("einddatum"))
    return add_months(e, -int(ct.get("opzegtermijn") or 0)) if e else None


def contract_bestede_uren(cid):
    r = q1("SELECT COALESCE(SUM(bestede_uren),0) u FROM opdrachten WHERE contract_id=?", (cid,))
    return r["u"]


def _contract_extra(ct):
    ct = dict(ct)
    s, e = pdate(ct["startdatum"]), pdate(ct.get("einddatum"))
    ct["duur_maanden"] = None if ct["onbepaalde_tijd"] else _duur(s, e)  # FR-CT-02
    u = uiterste_opzegdatum(ct)
    ct["uiterste_opzegdatum"] = u.isoformat() if u else None
    ct["dagen_tot_opzeg"] = (u - date.today()).days if u else None
    ct["bestede_uren"] = contract_bestede_uren(ct["id"])
    ct["uren_pct"] = round(100 * ct["bestede_uren"] / ct["urenbudget"], 1) if ct.get("urenbudget") else None
    ct["documenten"] = json.loads(ct.get("documenten") or "[]")
    return ct


def _duur(s, e):
    """Contractduur in hele maanden (afgerond), afgeleid van start en eind."""
    if not s or not e:
        return None
    return max(round(((e - s).days + 1) / 30.4375), 0)


def contracten(user, zoek=None, status=None, soort=None, klant_id=None):
    sql = "SELECT ct.*, k.naam klantnaam, k.accountmanager_id FROM contracten ct JOIN klanten k ON k.id=ct.klant_id WHERE 1=1"
    args = []
    if zoek:
        sql += " AND (ct.nummer LIKE ? OR k.naam LIKE ?)"
        args += [f"%{zoek}%"] * 2
    for col, v in (("ct.status", status), ("ct.soort", soort), ("ct.klant_id", klant_id)):
        if v:
            sql += f" AND {col}=?"
            args.append(v)
    return [filter_contract(user, _contract_extra(r)) for r in q(sql + " ORDER BY ct.nummer DESC", args)]


def contract(user, cid):
    r = q1("SELECT ct.*, k.naam klantnaam, k.accountmanager_id FROM contracten ct JOIN klanten k ON k.id=ct.klant_id WHERE ct.id=?", (cid,))
    if not r:
        raise Fout("Contract niet gevonden", 404)
    r = _contract_extra(r)
    r["versies"] = q("SELECT * FROM contract_versies WHERE contract_id=? ORDER BY versie DESC, id DESC", (cid,))
    r["opdrachten"] = q("SELECT * FROM opdrachten WHERE contract_id=? ORDER BY nummer", (cid,))
    r["rfcs"] = q("SELECT * FROM rfcs WHERE contract_id=? ORDER BY created_at DESC", (cid,))
    r["mag_schrijven"] = mag(user, "schrijven", "contract", r)
    r["opmerkingen_aantal"] = opm_tellingen("contract", [cid]).get(cid, 0)
    r = filter_contract(user, r)
    if r.get("waarde_verborgen"):
        for v in r["versies"]:
            v["snapshot"] = "{}"
    return r


def valideer_contract(d):
    verplicht(d, ["klant_id", "soort", "startdatum", "verlengingsvorm", "change_regime", "status"])
    if d.get("opzegtermijn") in (None, ""):
        raise Fout("Verplichte velden ontbreken: opzegtermijn")
    for v, l in (("soort", "contract_soort"), ("verlengingsvorm", "verlengingsvorm"), ("change_regime", "change_regime"),
                 ("status", "contract_status"), ("sla_niveau", "sla_niveau")):
        keuze(d, v, l)
    if not int(d.get("onbepaalde_tijd") or 0):
        if not d.get("einddatum"):
            raise Fout("Einddatum is verplicht (behalve bij onbepaalde tijd)")
        if pdate(d["einddatum"]) < pdate(d["startdatum"]):
            raise Fout("Einddatum ligt vóór startdatum")
    else:
        d["einddatum"] = None


def _normaliseer_contract(data):
    d = {k: data.get(k) for k in CT_VELDEN if k in data}
    for k in ("waarde", "urenbudget"):
        if k in d:
            d[k] = num(d[k])
    if "opzegtermijn" in d and d["opzegtermijn"] not in (None, ""):
        d["opzegtermijn"] = int(num(d["opzegtermijn"]))
    if "onbepaalde_tijd" in d:
        d["onbepaalde_tijd"] = 1 if d["onbepaalde_tijd"] in (1, True, "1", "true", "on") else 0
    return d


def contract_aanmaken(user, data):
    k = q1("SELECT * FROM klanten WHERE id=?", (data.get("klant_id"),))
    if not k:
        raise Fout("Klant is verplicht")
    eis(user, "schrijven", "contract", k)
    d = _normaliseer_contract(data)
    d.setdefault("status", "Concept")
    d["status"] = d["status"] or "Concept"
    d.setdefault("onbepaalde_tijd", 0)
    valideer_contract(d)
    with tx() as c:
        d["nummer"] = data.get("nummer") or volgnummer(c, "contracten", "CT")
        cols = list(d)
        cid = c.execute(f"INSERT INTO contracten({','.join(cols)},created_at,updated_at) VALUES({','.join('?' * len(cols))},?,?)",
                        list(d.values()) + [now(), now()]).lastrowid
        c.execute("INSERT INTO contract_versies(contract_id,versie,soort_wijziging,toelichting,snapshot,gebruiker,ts) VALUES(?,?,?,?,?,?,?)",
                  (cid, 1, "Initieel", "Aangemaakt", json.dumps(d, default=str), user["naam"], now()))
        audit(c, user["naam"], "CRM", "contract", cid, "aangemaakt", d)
        if d["status"] in ACTIEVE_CONTRACT:
            c.execute("UPDATE contracten SET jira_sync_status='wachtend' WHERE id=?", (cid,))
            emit(c, "contract.sync", "contract", cid)
    return contract(user, cid)


def contract_wijzigen(user, cid, data, bron="CRM", soort_wijziging=None, toelichting=None):
    oud = q1("SELECT ct.*, k.accountmanager_id FROM contracten ct JOIN klanten k ON k.id=ct.klant_id WHERE ct.id=?", (cid,))
    if not oud:
        raise Fout("Contract niet gevonden", 404)
    eis(user, "schrijven", "contract", oud)
    d = _normaliseer_contract(data)
    d.pop("klant_id", None)
    if user["rol"] == "finance":
        niet = set(d) - FIN_VELDEN
        if niet:
            raise Fout("Finance mag alleen financiële velden wijzigen (waarde, urenbudget)", 403)
    merged = {**oud, **d}
    valideer_contract(merged)
    if "einddatum" in d or "onbepaalde_tijd" in d:
        d["einddatum"] = merged["einddatum"]
    w = diff(oud, d)
    if not w:
        return contract(user, cid)
    with tx() as c:
        versie = oud["versie"] + 1 if soort_wijziging else oud["versie"]
        c.execute(f"UPDATE contracten SET {','.join(f'{k}=?' for k in d)}, versie=?, updated_at=? WHERE id=?",
                  list(d.values()) + [versie, now(), cid])
        if soort_wijziging:  # FR-CT-05
            c.execute("INSERT INTO contract_versies(contract_id,versie,soort_wijziging,toelichting,snapshot,gebruiker,ts) VALUES(?,?,?,?,?,?,?)",
                      (cid, versie, soort_wijziging, toelichting, json.dumps({k: v["nieuw"] for k, v in w.items()}, default=str), user["naam"], now()))
        audit(c, user["naam"], bron, "contract", cid, soort_wijziging.lower() if soort_wijziging else "gewijzigd", w)
        if merged["status"] in ACTIEVE_CONTRACT or oud["jira_key"]:
            emit(c, "contract.sync", "contract", cid)
    return contract(user, cid)


def contract_verlengen(user, cid, data):
    ct = q1("SELECT * FROM contracten WHERE id=?", (cid,))
    verplicht(data, ["einddatum"])
    if ct["einddatum"] and pdate(data["einddatum"]) <= pdate(ct["einddatum"]):
        raise Fout("Nieuwe einddatum moet na de huidige einddatum liggen")
    upd = {"einddatum": data["einddatum"], "status": "Verlengd"}
    if data.get("urenbudget") not in (None, ""):
        upd["urenbudget"] = data["urenbudget"]
    return contract_wijzigen(user, cid, upd, soort_wijziging="Verlenging", toelichting=data.get("toelichting"))


def contract_addendum(user, cid, data):
    upd = {k: v for k, v in data.items() if k in CT_VELDEN and k != "klant_id"}
    if not upd:
        raise Fout("Addendum bevat geen wijzigingen")
    return contract_wijzigen(user, cid, upd, soort_wijziging="Addendum", toelichting=data.get("toelichting"))


def contracttoets(ct, gewenste_datum=None, geschatte_uren=None):
    """FR-RFC-03: is het contract actief, valt de datum binnen de looptijd, is er budget, wat is het regime."""
    ct = _contract_extra(ct) if "bestede_uren" not in ct else ct
    checks = []
    actief = ct["status"] in ACTIEVE_CONTRACT
    checks.append({"check": "Contract actief", "ok": actief, "detail": ct["status"]})
    gd = pdate(gewenste_datum)
    if gd:
        binnen = gd >= pdate(ct["startdatum"]) and (ct["onbepaalde_tijd"] or gd <= pdate(ct["einddatum"]))
        checks.append({"check": "Gewenste datum binnen looptijd", "ok": binnen,
                       "detail": f"{gd} t.o.v. {ct['startdatum']} – {ct['einddatum'] or 'onbepaald'}"})
    else:
        binnen = True
        checks.append({"check": "Gewenste datum binnen looptijd", "ok": None, "detail": "Geen gewenste datum"})
    budget_ok = None
    if ct.get("urenbudget"):
        rest = ct["urenbudget"] - ct["bestede_uren"]
        if geschatte_uren is not None:
            budget_ok = geschatte_uren <= rest
            detail = f"Rest {rest:.0f} u, nodig {geschatte_uren:.0f} u"
        else:
            budget_ok = rest > 0 if rest <= 0 else None
            detail = f"Rest {rest:.0f} u; geschatte uren nog onbekend"
        checks.append({"check": "Urenbudget beschikbaar", "ok": budget_ok, "detail": detail})
    else:
        checks.append({"check": "Urenbudget beschikbaar", "ok": None, "detail": "Geen urenplafond in contract"})
    regime = ct["change_regime"]
    checks.append({"check": "Change-regime", "ok": regime != "Meerwerk via offerte", "detail": regime})

    if not actief or not binnen:
        uitkomst = "Nee"
    elif regime == "Meerwerk via offerte":
        uitkomst = "Nee"
    elif budget_ok is False:
        uitkomst = "Nee"
    elif budget_ok is None and ct.get("urenbudget"):
        uitkomst = "Te bepalen"
    else:
        uitkomst = "Ja"
    meerwerk = uitkomst == "Nee" and actief and binnen
    return {"uitkomst": uitkomst, "meerwerk": meerwerk, "checks": checks, "ts": now()}


def contract_bruikbaar(ct):
    """FR-CT-07"""
    if ct["status"] not in ACTIEVE_CONTRACT:
        raise Fout(f"Contract {ct['nummer']} heeft status '{ct['status']}': koppelen van nieuwe RFC's of opdrachten is niet toegestaan", 422)


# ================================================================ OPDRACHTEN
OP_VELDEN = ["contract_id", "omschrijving", "type", "startdatum", "einddatum", "begrote_uren", "budget", "opdrachtgever_id", "status"]


def opdrachten(user, contract_id=None, status=None):
    sql = """SELECT o.*, ct.nummer contractnummer, k.naam klantnaam, k.id klant_id,
             (cp.voornaam || ' ' || cp.achternaam) opdrachtgever FROM opdrachten o
             JOIN contracten ct ON ct.id=o.contract_id JOIN klanten k ON k.id=ct.klant_id
             LEFT JOIN contactpersonen cp ON cp.id=o.opdrachtgever_id WHERE 1=1"""
    args = []
    if contract_id:
        sql += " AND o.contract_id=?"
        args.append(contract_id)
    if status:
        sql += " AND o.status=?"
        args.append(status)
    rows = q(sql + " ORDER BY o.nummer DESC", args)
    if user["rol"] not in WAARDE_ZICHTBAAR:
        for r in rows:
            r["budget"] = None
    return rows


def opdracht(user, oid):
    r = q1("""SELECT o.*, ct.nummer contractnummer, ct.klant_id, k.naam klantnaam, k.accountmanager_id,
              (cp.voornaam || ' ' || cp.achternaam) opdrachtgever FROM opdrachten o
              JOIN contracten ct ON ct.id=o.contract_id JOIN klanten k ON k.id=ct.klant_id
              LEFT JOIN contactpersonen cp ON cp.id=o.opdrachtgever_id WHERE o.id=?""", (oid,))
    if not r:
        raise Fout("Opdracht niet gevonden", 404)
    if user["rol"] not in WAARDE_ZICHTBAAR:
        r["budget"] = None
    r["rfcs"] = q("SELECT * FROM rfcs WHERE opdracht_id=?", (oid,))
    r["mag_schrijven"] = mag(user, "schrijven", "opdracht", r)
    r["opmerkingen_aantal"] = opm_tellingen("opdracht", [oid]).get(oid, 0)
    return r


def opdracht_opslaan(user, data, oid=None):
    oud = q1("SELECT * FROM opdrachten WHERE id=?", (oid,)) if oid else None
    d = {k: data.get(k) for k in OP_VELDEN if k in data}
    for k in ("begrote_uren", "budget"):
        if k in d:
            d[k] = num(d[k])
    merged = {**(oud or {}), **d}
    verplicht(merged, ["contract_id", "omschrijving", "type", "startdatum", "einddatum", "opdrachtgever_id"])
    keuze(merged, "type", "opdracht_type")
    keuze(merged, "status", "opdracht_status")
    ct = q1("SELECT ct.*, k.accountmanager_id FROM contracten ct JOIN klanten k ON k.id=ct.klant_id WHERE ct.id=?", (merged["contract_id"],))
    if not ct:
        raise Fout("Contract niet gevonden")
    eis(user, "schrijven", "opdracht", ct)
    if not oud or "contract_id" in d and int(d["contract_id"]) != oud["contract_id"]:
        contract_bruikbaar(ct)  # FR-OP-01 / FR-CT-07
    s, e = pdate(merged["startdatum"]), pdate(merged["einddatum"])
    if e < s:
        raise Fout("Einddatum ligt vóór startdatum")
    if s < pdate(ct["startdatum"]) or (not ct["onbepaalde_tijd"] and e > pdate(ct["einddatum"])):
        raise Fout(f"Opdrachtdatums moeten binnen de contractlooptijd vallen ({ct['startdatum']} – {ct['einddatum'] or 'onbepaald'})", 422)
    og = q1("SELECT klant_id FROM contactpersonen WHERE id=?", (merged["opdrachtgever_id"],))
    if not og or og["klant_id"] != ct["klant_id"]:
        raise Fout("Opdrachtgever moet een contactpersoon van dezelfde klant zijn")
    with tx() as c:
        if oud:
            w = diff(oud, d)
            if not w:
                return opdracht(user, oid)
            c.execute(f"UPDATE opdrachten SET {','.join(f'{k}=?' for k in d)}, updated_at=? WHERE id=?", list(d.values()) + [now(), oid])
            audit(c, user["naam"], "CRM", "opdracht", oid, "gewijzigd", w)
        else:
            d.setdefault("status", "Gepland")
            d["status"] = d["status"] or "Gepland"
            d["nummer"] = volgnummer(c, "opdrachten", "OP")
            cols = list(d)
            oid = c.execute(f"INSERT INTO opdrachten({','.join(cols)},created_at,updated_at) VALUES({','.join('?' * len(cols))},?,?)",
                            list(d.values()) + [now(), now()]).lastrowid
            audit(c, user["naam"], "CRM", "opdracht", oid, "aangemaakt", d)
        r = c.execute("SELECT status, jira_key FROM opdrachten WHERE id=?", (oid,)).fetchone()
        if r[0] in ("Gepland", "Lopend") or r[1]:  # FR-OP-02
            if not r[1]:
                c.execute("UPDATE opdrachten SET jira_sync_status='wachtend' WHERE id=?", (oid,))
            emit(c, "opdracht.sync", "opdracht", oid)
    return opdracht(user, oid)


def opdracht_afronden_bevestigen(user, oid):
    """FR-OP-04: Epic Done in Jira → bevestiging door accountmanager."""
    o = opdracht(user, oid)
    eis(user, "schrijven", "opdracht", o)
    if not o["jira_epic_done"]:
        raise Fout("Epic is in Jira nog niet op Done gezet")
    with tx() as c:
        c.execute("UPDATE opdrachten SET status='Afgerond', updated_at=? WHERE id=?", (now(), oid))
        audit(c, user["naam"], "CRM", "opdracht", oid, "afgerond (bevestigd na Epic Done)")
    return opdracht(user, oid)


# ================================================================ RFC's
RFC_BIDI = ["titel", "omschrijving", "contract_id", "opdracht_id", "aanvrager_id", "change_type", "prioriteit"]
RFC_VELDEN = ["titel", "omschrijving", "klant_id", "contract_id", "opdracht_id", "aanvrager_id", "change_type", "prioriteit", "gewenste_datum"]


def rfcs(user, klant_id=None, contract_id=None, opdracht_id=None, status=None, change_type=None, zoek=None):
    sql = """SELECT r.*, k.naam klantnaam, ct.nummer contractnummer, o.nummer opdrachtnummer,
             (cp.voornaam || ' ' || cp.achternaam) aanvrager,
             (SELECT MAX(risicoscore) FROM impactanalyses ia WHERE ia.rfc_id=r.id AND ia.versie=(SELECT MAX(versie) FROM impactanalyses WHERE rfc_id=r.id)) risicoscore
             FROM rfcs r JOIN klanten k ON k.id=r.klant_id JOIN contracten ct ON ct.id=r.contract_id
             LEFT JOIN opdrachten o ON o.id=r.opdracht_id LEFT JOIN contactpersonen cp ON cp.id=r.aanvrager_id WHERE 1=1"""
    args = []
    if status == "open":  # alles wat nog niet is afgerond
        sql += " AND r.status NOT IN ('Gesloten','Afgewezen')"
        status = None
    for col, v in (("r.klant_id", klant_id), ("r.contract_id", contract_id), ("r.opdracht_id", opdracht_id),
                   ("r.status", status), ("r.change_type", change_type)):
        if v:
            sql += f" AND {col}=?"
            args.append(v)
    if zoek:
        sql += " AND (r.titel LIKE ? OR r.jira_key LIKE ?)"
        args += [f"%{zoek}%"] * 2
    return q(sql + " ORDER BY r.created_at DESC", args)


def rfc(user, rid):
    r = q1("""SELECT r.*, k.naam klantnaam, k.accountmanager_id, ct.nummer contractnummer, ct.soort contractsoort,
              ct.einddatum contract_einddatum, ct.change_regime, ct.sla_niveau, o.nummer opdrachtnummer,
              (cp.voornaam || ' ' || cp.achternaam) aanvrager
              FROM rfcs r JOIN klanten k ON k.id=r.klant_id JOIN contracten ct ON ct.id=r.contract_id
              LEFT JOIN opdrachten o ON o.id=r.opdracht_id LEFT JOIN contactpersonen cp ON cp.id=r.aanvrager_id WHERE r.id=?""", (rid,))
    if not r:
        raise Fout("RFC niet gevonden", 404)
    r["contracttoets"] = json.loads(r["contracttoets"] or "null")
    r["impactanalyses"] = q("SELECT * FROM impactanalyses WHERE rfc_id=? ORDER BY versie DESC", (rid,))
    for ia in r["impactanalyses"]:
        ia["contractverschil"] = json.loads(ia["contractverschil"] or "null")
    r["betrokkenen"] = q("""SELECT rc.rol, cp.id, (cp.voornaam || ' ' || cp.achternaam) naam, cp.email FROM rfc_contacten rc
                           JOIN contactpersonen cp ON cp.id=rc.contact_id WHERE rc.rfc_id=?""", (rid,))
    r["offertes"] = [_offerte_out(o) for o in q("SELECT * FROM offertes WHERE rfc_id=? ORDER BY id", (rid,))]
    r["mag_offerte"] = mag(user, "schrijven", "offerte", r)
    if user["rol"] not in WAARDE_ZICHTBAAR:
        for o in r["offertes"]:
            o["waarde"] = None
    r["mag_schrijven"] = mag(user, "schrijven", "rfc", r)
    r["opmerkingen_aantal"] = opm_tellingen("rfc", [rid]).get(rid, 0)
    return r


def _valideer_rfc(c, d):
    verplicht(d, ["titel", "klant_id", "contract_id", "aanvrager_id", "change_type", "prioriteit"])
    keuze(d, "change_type", "change_type")
    keuze(d, "prioriteit", "prioriteit")
    ct = dict(c.execute("SELECT * FROM contracten WHERE id=?", (d["contract_id"],)).fetchone() or {})
    if not ct:
        raise Fout("Contract niet gevonden")
    if ct["klant_id"] != int(d["klant_id"]):
        raise Fout("Contract hoort niet bij deze klant")
    if d.get("opdracht_id"):
        o = c.execute("SELECT contract_id FROM opdrachten WHERE id=?", (d["opdracht_id"],)).fetchone()
        if not o or o[0] != int(d["contract_id"]):
            raise Fout("Opdracht valt niet onder het gekozen contract")
    a = c.execute("SELECT klant_id FROM contactpersonen WHERE id=?", (d["aanvrager_id"],)).fetchone()
    if not a or a[0] != int(d["klant_id"]):
        raise Fout("Aanvrager moet een contactpersoon van de klant zijn")
    return ct


def _toets_toepassen(c, rid, gebruiker="Systeem", bron="CRM"):
    r = dict(c.execute("SELECT * FROM rfcs WHERE id=?", (rid,)).fetchone())
    ct = dict(c.execute("SELECT * FROM contracten WHERE id=?", (r["contract_id"],)).fetchone())
    t = contracttoets(ct, r["gewenste_datum"], r["geschatte_uren"])
    if t["uitkomst"] != r["binnen_contract"] or not r["contracttoets"]:
        c.execute("UPDATE rfcs SET binnen_contract=?, contracttoets=?, updated_at=? WHERE id=?",
                  (t["uitkomst"], json.dumps(t), now(), rid))
        audit(c, gebruiker, bron, "rfc", rid, "contracttoets", {"binnen_contract": {"oud": r["binnen_contract"], "nieuw": t["uitkomst"]}})
        if r["jira_key"]:
            emit(c, "rfc.sync", "rfc", rid, {"velden": ["binnen_contract"]})
    if t["meerwerk"]:
        _meerwerk(c, rid)
    return t


def _meerwerk(c, rid):
    """FR-RFC-04: conceptofferte voor accountmanager."""
    if c.execute("SELECT 1 FROM offertes WHERE rfc_id=?", (rid,)).fetchone():
        return
    r = dict(c.execute("SELECT r.*, k.accountmanager_id FROM rfcs r JOIN klanten k ON k.id=r.klant_id WHERE r.id=?", (rid,)).fetchone())
    c.execute("INSERT INTO offertes(rfc_id,klant_id,accountmanager_id,omschrijving,waarde,status,ts) VALUES(?,?,?,?,?,?,?)",
              (rid, r["klant_id"], r["accountmanager_id"], f"Meerwerk: {r['titel']}", r["geschatte_kosten"], "Concept", now()))
    signaal(c, f"meerwerk:{rid}", "meerwerk", "rfc", rid,
            f"RFC '{r['titel']}' valt buiten contract ({r['jira_key'] or 'nog geen Jira-key'}): conceptofferte aangemaakt", r["accountmanager_id"])


OFFERTE_STATUS = ["Concept", "Verstuurd", "Geaccepteerd", "Afgewezen"]


def linkbron(url):
    u = (url or "").lower()
    if "sharepoint.com" in u or "onedrive" in u:
        return "SharePoint"
    if "atlassian.net/wiki" in u or "confluence" in u:
        return "Confluence"
    return "Document" if u else None


def _offerte_data(data):
    d = {}
    if "status" in data:
        if data["status"] not in OFFERTE_STATUS:
            raise Fout("Ongeldige offertestatus")
        d["status"] = data["status"]
    if "waarde" in data:
        d["waarde"] = num(data["waarde"])
    if "omschrijving" in data:
        d["omschrijving"] = (data["omschrijving"] or "").strip()
        if not d["omschrijving"]:
            raise Fout("Omschrijving is verplicht")
    if "link" in data:
        link = (data["link"] or "").strip() or None
        if link:
            if not re.match(r"^https://[^\s/]+\.[^\s/]+/\S*$", link, re.I):
                raise Fout("De link moet een volledige https://-link zijn (bijv. naar SharePoint of Confluence)")
            if len(link) > 2000:
                raise Fout("De link is te lang")
        d["link"] = link
    return d


def _offerte_out(o):
    o["link_bron"] = linkbron(o.get("link"))
    return o


def offerte_toevoegen(user, rid, data):
    r = q1("SELECT r.*, k.accountmanager_id FROM rfcs r JOIN klanten k ON k.id=r.klant_id WHERE r.id=?", (rid,))
    if not r:
        raise Fout("RFC niet gevonden", 404)
    eis(user, "schrijven", "offerte", r)
    d = {"status": "Concept", "omschrijving": f"Meerwerk: {r['titel']}", **_offerte_data(data)}
    with tx() as c:
        oid = c.execute("""INSERT INTO offertes(rfc_id,klant_id,accountmanager_id,omschrijving,waarde,status,link,ts,bijgewerkt,bijgewerkt_door)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (rid, r["klant_id"], r["accountmanager_id"], d["omschrijving"], d.get("waarde"), d["status"], d.get("link"),
                         now(), now(), user["naam"])).lastrowid
        audit(c, user["naam"], "CRM", "rfc", rid, "offerte toegevoegd", {"offerte": d["omschrijving"], "link": d.get("link")})
    return _offerte_out(q1("SELECT * FROM offertes WHERE id=?", (oid,)))


def offerte_wijzigen(user, oid, data):
    o = q1("SELECT o.*, k.accountmanager_id FROM offertes o JOIN klanten k ON k.id=o.klant_id WHERE o.id=?", (oid,))
    if not o:
        raise Fout("Offerte niet gevonden", 404)
    eis(user, "schrijven", "offerte", o)
    d = _offerte_data(data)
    w = diff(o, d)
    if not w:
        return _offerte_out(o)
    with tx() as c:
        c.execute(f"UPDATE offertes SET {','.join(f'{k}=?' for k in d)}, bijgewerkt=?, bijgewerkt_door=? WHERE id=?",
                  list(d.values()) + [now(), user["naam"], oid])
        audit(c, user["naam"], "CRM", "rfc", o["rfc_id"], "offerte gewijzigd", w)
    return _offerte_out(q1("SELECT * FROM offertes WHERE id=?", (oid,)))


def rfc_aanmaken(user, data, bron="CRM", jira_key=None, gebruiker=None):
    if bron == "CRM":
        eis(user, "aanmaken", "rfc")
    d = {k: data.get(k) for k in RFC_VELDEN}
    for k in ("opdracht_id",):
        d[k] = d[k] or None
    with tx() as c:
        ct = _valideer_rfc(c, d)
        contract_bruikbaar(ct)  # FR-CT-07
        ts = now()
        cols = list(d)
        rid = c.execute(f"""INSERT INTO rfcs({','.join(cols)},status,bron,jira_key,veld_ts,jira_sync_status,created_at,updated_at,last_sync)
                            VALUES({','.join('?' * len(cols))},?,?,?,?,?,?,?,?)""",
                        list(d.values()) + [data.get("status") or "Nieuw", bron, jira_key, json.dumps({k: ts for k in RFC_BIDI}),
                                            "gesynchroniseerd" if jira_key else "wachtend", ts, ts, ts if jira_key else None]).lastrowid
        c.execute("INSERT OR IGNORE INTO rfc_contacten VALUES(?,?,?)", (rid, d["aanvrager_id"], "Aanvrager"))
        for cab in c.execute("SELECT id, rollen FROM contactpersonen WHERE klant_id=? AND actief=1", (d["klant_id"],)).fetchall():
            if "CAB-lid" in json.loads(cab[1]):
                c.execute("INSERT OR IGNORE INTO rfc_contacten VALUES(?,?,?)", (rid, cab[0], "Goedkeurder"))
        audit(c, gebruiker or user["naam"], bron, "rfc", rid, "aangemaakt", d)
        if not jira_key:
            emit(c, "rfc.aanmaken", "rfc", rid)
        _toets_toepassen(c, rid, gebruiker or user["naam"], bron)
    return rid


def rfc_wijzigen(user, rid, data):
    oud = q1("SELECT r.*, k.accountmanager_id FROM rfcs r JOIN klanten k ON k.id=r.klant_id WHERE r.id=?", (rid,))
    if not oud:
        raise Fout("RFC niet gevonden", 404)
    eis(user, "schrijven", "rfc", oud)
    d = {k: (data[k] or None) if k in ("opdracht_id",) else data[k] for k in RFC_VELDEN if k in data and k != "klant_id"}
    w = diff(oud, d)
    if not w:
        return rfc(user, rid)
    with tx() as c:
        ct = _valideer_rfc(c, {**oud, **d})
        if "contract_id" in w:
            contract_bruikbaar(ct)
        vts = json.loads(oud["veld_ts"] or "{}")
        for k in w:
            vts[k] = now()
        c.execute(f"UPDATE rfcs SET {','.join(f'{k}=?' for k in d)}, veld_ts=?, updated_at=? WHERE id=?",
                  list(d.values()) + [json.dumps(vts), now(), rid])
        audit(c, user["naam"], "CRM", "rfc", rid, "gewijzigd", w)
        emit(c, "rfc.sync", "rfc", rid, {"velden": list(w)})
        _toets_toepassen(c, rid, user["naam"])
    return rfc(user, rid)


def rfc_toets_opnieuw(user, rid):
    with tx() as c:
        _toets_toepassen(c, rid, user["naam"])
    return rfc(user, rid)


def risicoscore(kans, impact):
    return int(kans) * int(impact)


def contractverschil(ct, ia):
    """FR-IA-04: impact vs contractgegevens."""
    ct = _contract_extra(ct)
    res = {}
    if ct.get("urenbudget"):
        rest = ct["urenbudget"] - ct["bestede_uren"]
        res["uren"] = {"rest_budget": rest, "impact": ia.get("uren"), "na_change": rest - (ia.get("uren") or 0),
                       "past": (ia.get("uren") or 0) <= rest}
    if ct.get("waarde"):
        res["kosten"] = {"contractwaarde": ct["waarde"], "impact": ia.get("kosten"),
                         "pct_van_contract": round(100 * (ia.get("kosten") or 0) / ct["waarde"], 1)}
    res["sla"] = {"niveau": ct.get("sla_niveau"), "impact": ia.get("impact_contract")}
    res["regime"] = ct["change_regime"]
    return res
