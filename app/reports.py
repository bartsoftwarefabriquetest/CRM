"""Rapportages (hoofdstuk 7), globale zoekfunctie (FR-AL-01) en import/export (FR-AL-04)."""
import csv
import io
import json
from datetime import date

from . import domain, integration
from .db import q, q1, tx, now
from .domain import Fout, audit, filter_contract


def _filter_sql(f, alias_ct="ct", alias_k="k"):
    sql, args = "", []
    if f.get("klant_id"):
        sql += f" AND {alias_k}.id=?"
        args.append(f["klant_id"])
    if f.get("soort"):
        sql += f" AND {alias_ct}.soort=?"
        args.append(f["soort"])
    return sql, args


def contractkalender(user, f):
    s, a = _filter_sql(f)
    rows = domain.contracten(user)
    ids = {r["id"] for r in q(f"SELECT ct.id FROM contracten ct JOIN klanten k ON k.id=ct.klant_id WHERE 1=1 {s}", a)}
    out = []
    for r in rows:
        if r["id"] not in ids or not r["einddatum"]:
            continue
        if f.get("van") and r["einddatum"] < f["van"] or f.get("tot") and r["einddatum"] > f["tot"]:
            continue
        out.append({k: r.get(k) for k in ("id", "nummer", "klantnaam", "soort", "status", "startdatum", "einddatum",
                                           "uiterste_opzegdatum", "dagen_tot_opzeg", "verlengingsvorm", "opzegtermijn", "waarde")})
    return sorted(out, key=lambda x: x["uiterste_opzegdatum"] or "9")


def urenverbruik(user, f):
    s, a = _filter_sql(f)
    out = []
    for r in q(f"SELECT ct.*, k.naam klantnaam FROM contracten ct JOIN klanten k ON k.id=ct.klant_id WHERE ct.urenbudget IS NOT NULL {s}", a):
        besteed = domain.contract_bestede_uren(r["id"])
        st, ei = domain.pdate(r["startdatum"]), domain.pdate(r["einddatum"])
        prognose = None
        if ei and st and date.today() > st:
            verstreken = (min(date.today(), ei) - st).days or 1
            prognose = round(besteed / verstreken * ((ei - st).days or 1), 0)
        out.append({"id": r["id"], "nummer": r["nummer"], "klantnaam": r["klantnaam"], "soort": r["soort"], "status": r["status"],
                    "begroot": r["urenbudget"], "besteed": besteed, "pct": round(100 * besteed / r["urenbudget"], 1),
                    "prognose_einde": prognose, "prognose_pct": round(100 * prognose / r["urenbudget"], 0) if prognose else None})
    return sorted(out, key=lambda x: -x["pct"])


def _periode(f, col):
    sql, args = "", []
    if f.get("van"):
        sql += f" AND {col}>=?"
        args.append(f["van"])
    if f.get("tot"):
        sql += f" AND {col}<=?"
        args.append(f["tot"] + "T99")
    return sql, args


def rfc_per_klant(user, f):
    s, a = _filter_sql(f)
    p, pa = _periode(f, "r.created_at")
    rows = q(f"""SELECT k.id, k.naam klantnaam,
        SUM(CASE WHEN r.status NOT IN ('Gesloten','Afgewezen','Goedgekeurd','Uitgevoerd') THEN 1 ELSE 0 END) open,
        SUM(CASE WHEN r.status IN ('Goedgekeurd','Uitgevoerd') OR (r.status='Gesloten' AND COALESCE(r.cab_besluit,'')!='Afgewezen') THEN 1 ELSE 0 END) goedgekeurd,
        SUM(CASE WHEN r.status='Afgewezen' OR r.cab_besluit='Afgewezen' THEN 1 ELSE 0 END) afgewezen,
        COUNT(r.id) totaal,
        AVG(CASE WHEN r.status='Gesloten' THEN julianday(r.updated_at)-julianday(r.created_at) END) doorlooptijd_dagen
        FROM rfcs r JOIN klanten k ON k.id=r.klant_id JOIN contracten ct ON ct.id=r.contract_id WHERE 1=1 {s} {p}
        GROUP BY k.id ORDER BY totaal DESC""", a + pa)
    for r in rows:
        r["doorlooptijd_dagen"] = round(r["doorlooptijd_dagen"], 1) if r["doorlooptijd_dagen"] is not None else None
    return rows


def meerwerk(user, f):
    s, a = _filter_sql(f)
    p, pa = _periode(f, "r.created_at")
    rows = q(f"""SELECT r.id, r.jira_key, r.titel, r.status, r.geschatte_kosten, r.geschatte_uren, r.cab_besluit,
                 k.naam klantnaam, ct.nummer contractnummer, ct.change_regime, o.status offerte_status, o.id offerte_id
                 FROM rfcs r JOIN klanten k ON k.id=r.klant_id JOIN contracten ct ON ct.id=r.contract_id
                 LEFT JOIN offertes o ON o.rfc_id=r.id WHERE r.binnen_contract='Nee' {s} {p} ORDER BY r.created_at DESC""", a + pa)
    if user["rol"] not in domain.WAARDE_ZICHTBAAR:
        for r in rows:
            r["geschatte_kosten"] = None
    return rows


def change_kpis(user, f):
    s, a = _filter_sql(f)
    p, pa = _periode(f, "r.created_at")
    base = f"FROM rfcs r JOIN klanten k ON k.id=r.klant_id JOIN contracten ct ON ct.id=r.contract_id WHERE 1=1 {s} {p}"
    per_type = q(f"SELECT r.change_type, COUNT(*) n {base} GROUP BY r.change_type", a + pa)
    per_status = q(f"SELECT r.status, COUNT(*) n {base} GROUP BY r.status", a + pa)
    afgerond = q1(f"""SELECT SUM(CASE WHEN r.status IN ('Uitgevoerd','Gesloten') AND COALESCE(r.cab_besluit,'')!='Afgewezen' THEN 1 ELSE 0 END) ok,
                      SUM(CASE WHEN r.status IN ('Uitgevoerd','Gesloten','Afgewezen') THEN 1 ELSE 0 END) n {base}""", a + pa)
    risico = q1(f"""SELECT AVG(ia.risicoscore) gem FROM impactanalyses ia JOIN rfcs r ON r.id=ia.rfc_id
                    JOIN klanten k ON k.id=r.klant_id JOIN contracten ct ON ct.id=r.contract_id
                    WHERE ia.versie=(SELECT MAX(versie) FROM impactanalyses x WHERE x.rfc_id=ia.rfc_id) {s} {p}""", a + pa)
    totaal = sum(x["n"] for x in per_type)
    return {"totaal": totaal, "per_type": per_type, "per_status": per_status,
            "spoed": next((x["n"] for x in per_type if x["change_type"] == "Spoed"), 0),
            "succesratio": round(100 * (afgerond["ok"] or 0) / afgerond["n"], 1) if afgerond["n"] else None,
            "gem_risicoscore": round(risico["gem"], 1) if risico["gem"] else None}


def integratiestatus(user, f):
    st = integration.status()
    st["recente_fouten"] = q("SELECT id,richting,type,entiteit,entiteit_id,status,pogingen,fout,ts FROM berichten WHERE status IN ('dlq','geweigerd','retry') ORDER BY id DESC LIMIT 20")
    st["per_type"] = q("SELECT richting, type, COUNT(*) n, SUM(CASE WHEN status='verwerkt' THEN 1 ELSE 0 END) ok FROM berichten GROUP BY richting, type ORDER BY richting, type")
    return st


RAPPORTEN = {"contractkalender": contractkalender, "urenverbruik": urenverbruik, "rfc-per-klant": rfc_per_klant,
             "meerwerk": meerwerk, "change-kpis": change_kpis, "integratiestatus": integratiestatus}


# ------------------------------------------------------------------ zoeken
def zoeken(user, term):
    t = f"%{term}%"
    res = []
    for r in q("SELECT id, naam, nummer, plaats FROM klanten WHERE naam LIKE ? OR kvk LIKE ? OR nummer LIKE ? OR plaats LIKE ? OR jira_key LIKE ? LIMIT 10", (t,) * 5):
        res.append({"type": "Klant", "id": r["id"], "titel": r["naam"], "sub": f"{r['nummer']} · {r['plaats'] or ''}", "link": f"#/klanten/{r['id']}"})
    for r in q("""SELECT cp.id, cp.klant_id, cp.voornaam, cp.achternaam, cp.email, k.naam FROM contactpersonen cp JOIN klanten k ON k.id=cp.klant_id
                  WHERE cp.achternaam LIKE ? OR cp.voornaam LIKE ? OR cp.email LIKE ? LIMIT 10""", (t,) * 3):
        res.append({"type": "Contactpersoon", "id": r["id"], "titel": f"{r['voornaam'] or ''} {r['achternaam']}", "sub": f"{r['email']} · {r['naam']}", "link": f"#/klanten/{r['klant_id']}"})
    for r in q("SELECT ct.id, ct.nummer, ct.soort, k.naam FROM contracten ct JOIN klanten k ON k.id=ct.klant_id WHERE ct.nummer LIKE ? OR ct.jira_key LIKE ? OR k.naam LIKE ? LIMIT 10", (t,) * 3):
        res.append({"type": "Contract", "id": r["id"], "titel": r["nummer"], "sub": f"{r['soort']} · {r['naam']}", "link": f"#/contracten/{r['id']}"})
    for r in q("SELECT id, nummer, omschrijving, jira_key FROM opdrachten WHERE nummer LIKE ? OR omschrijving LIKE ? OR jira_key LIKE ? LIMIT 10", (t,) * 3):
        res.append({"type": "Opdracht", "id": r["id"], "titel": f"{r['nummer']} {r['omschrijving']}", "sub": r["jira_key"] or "", "link": f"#/opdrachten/{r['id']}"})
    for r in q("SELECT id, jira_key, titel, status FROM rfcs WHERE titel LIKE ? OR jira_key LIKE ? OR omschrijving LIKE ? LIMIT 10", (t,) * 3):
        res.append({"type": "RFC", "id": r["id"], "titel": f"{r['jira_key'] or 'RFC #' + str(r['id'])} {r['titel']}", "sub": r["status"], "link": f"#/rfcs/{r['id']}"})
    return res


# ------------------------------------------------------------------ export / import
EXPORTS = {
    "klanten": lambda u: domain.klanten(u),
    "contactpersonen": lambda u: [domain.filter_contact(u, domain._cp_out(r)) for r in q("SELECT * FROM contactpersonen")],
    "contracten": lambda u: domain.contracten(u),
    "opdrachten": lambda u: domain.opdrachten(u),
    "rfcs": lambda u: domain.rfcs(u),
    "impactanalyses": lambda u: q("SELECT ia.*, r.jira_key FROM impactanalyses ia JOIN rfcs r ON r.id=ia.rfc_id"),
    "audit": lambda u: q("SELECT * FROM audit ORDER BY id DESC LIMIT 5000"),
}


def naar_csv(rows):
    buf = io.StringIO()
    buf.write("﻿")  # Excel herkent UTF-8
    if not rows:
        return buf.getvalue()
    cols = [k for k in rows[0] if not isinstance(rows[0][k], (list, dict))]
    w = csv.DictWriter(buf, fieldnames=cols, delimiter=";", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k) for k in cols})
    return buf.getvalue()


def exporteer(user, wat, rows=None):
    if wat == "audit" and user["rol"] not in ("beheerder", "directie"):
        raise Fout("Alleen beheer en directie mogen de audit trail exporteren", 403)
    rows = rows if rows is not None else EXPORTS[wat](user)
    with tx() as c:  # NFR-SE-06/08: exports worden gelogd
        audit(c, user["naam"], "CRM", "export", None, f"export {wat}", {"records": len(rows)})
    return naar_csv(rows)


def importeer(user, wat, tekst, forceer=False):
    tekst = tekst.lstrip("﻿")
    dialect = ";" if tekst.split("\n", 1)[0].count(";") >= tekst.split("\n", 1)[0].count(",") else ","
    rows = list(csv.DictReader(io.StringIO(tekst), delimiter=dialect))
    res = {"ingelezen": len(rows), "aangemaakt": 0, "fouten": []}
    for n, r in enumerate(rows, start=2):
        r = {k.strip(): (v or "").strip() or None for k, v in r.items() if k}
        try:
            if wat == "klanten":
                if not r.get("accountmanager_id") and r.get("accountmanager"):
                    u = q1("SELECT id FROM users WHERE naam=? OR email=?", (r["accountmanager"], r["accountmanager"]))
                    r["accountmanager_id"] = u and u["id"]
                r["accountmanager_id"] = int(r["accountmanager_id"]) if r.get("accountmanager_id") else None
                domain.klant_aanmaken(user, r, forceer=forceer)
            elif wat == "contactpersonen":
                if not r.get("klant_id") and r.get("kvk"):
                    k = q1("SELECT id FROM klanten WHERE kvk=?", (r["kvk"],))
                    r["klant_id"] = k and k["id"]
                domain.contact_opslaan(user, r)
            else:
                raise Fout(f"Import van {wat} wordt niet ondersteund")
            res["aangemaakt"] += 1
        except Fout as e:
            res["fouten"].append({"regel": n, "fout": str(e), "dubbelen": [d["naam"] for d in e.extra.get("dubbelen", [])]})
    return res
