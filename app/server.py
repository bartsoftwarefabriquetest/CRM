"""HTTP-server: JSON-API, webhook-endpoint, statische frontend. Alleen Python-standaardbibliotheek."""
import html
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db, domain, integration, jira_sim, reports
from .db import q, q1, tx, now
from .domain import Fout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


def webhook_secret():
    # In productie uit een key vault (NFR-SE-04); hier lokaal gegenereerd en bewaard.
    p = os.path.join(os.path.dirname(db.DB_PATH), "webhook.secret")
    if not os.path.exists(p):
        with open(p, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(p, 0o600)
    return open(p).read().strip().encode()


# ============================================================ sessie (gesimuleerde SSO)
@route("GET", "/api/users")
def users(req, user):
    return {"users": q("SELECT * FROM users ORDER BY id"), "rollen": domain.ROLLEN}


@route("POST", "/api/login")
def login(req, user):
    u = q1("SELECT * FROM users WHERE id=?", (req.body.get("id"),))
    if not u:
        raise Fout("Onbekende gebruiker", 401)
    with tx() as c:
        domain.audit(c, u["naam"], "CRM", "sessie", u["id"], "ingelogd (SSO-simulatie)")
    return u


@route("GET", "/api/me")
def me(req, user):
    n = q1("SELECT COUNT(*) n FROM signalen WHERE gelezen=0 AND (ontvanger_id=? OR ?)", (user["id"], user["rol"] in ("beheerder", "directie")))["n"]
    return {**user, "rolnaam": domain.ROLLEN[user["rol"]], "ongelezen": n, "waarde_zichtbaar": user["rol"] in domain.WAARDE_ZICHTBAAR}


@route("GET", "/api/keuzes")
def keuzes(req, user):
    return {**domain.KEUZES, "accountmanagers": q("SELECT id, naam FROM users WHERE rol='accountmanager'")}


# ============================================================ klanten & contactpersonen
@route("GET", "/api/klanten")
def klanten(req, user):
    return domain.klanten(user, req.qs.get("zoek"), req.qs.get("status"), req.qs.get("archief") == "1")


@route("POST", "/api/klanten")
def klant_nieuw(req, user):
    return domain.klant_aanmaken(user, req.body, forceer=req.body.get("forceer"))


@route("POST", "/api/klanten/dubbelcheck")
def klant_dubbel(req, user):
    return domain.dubbele_klanten(req.body, req.body.get("id"))


@route("GET", r"/api/klanten/(\d+)")
def klant(req, user, kid):
    return domain.klant(user, int(kid))


@route("PUT", r"/api/klanten/(\d+)")
def klant_upd(req, user, kid):
    return domain.klant_wijzigen(user, int(kid), req.body)


@route("POST", r"/api/klanten/(\d+)/archiveren")
def klant_arch(req, user, kid):
    return domain.klant_archiveren(user, int(kid))


@route("POST", r"/api/klanten/(\d+)/activiteiten")
def klant_act(req, user, kid):
    return domain.activiteit_toevoegen(user, int(kid), req.body)


@route("POST", "/api/contactpersonen")
def cp_nieuw(req, user):
    return domain.contact_opslaan(user, req.body)


@route("PUT", r"/api/contactpersonen/(\d+)")
def cp_upd(req, user, cid):
    return domain.contact_opslaan(user, req.body, int(cid))


@route("POST", r"/api/contactpersonen/(\d+)/(activeren|deactiveren)")
def cp_act(req, user, cid, actie):
    return domain.contact_actief(user, int(cid), actie == "activeren")


@route("POST", r"/api/contactpersonen/(\d+)/anonimiseren")
def cp_anon(req, user, cid):
    return domain.contact_anonimiseren(user, int(cid))


# ============================================================ contracten & opdrachten
@route("GET", "/api/contracten")
def contracten(req, user):
    return domain.contracten(user, req.qs.get("zoek"), req.qs.get("status"), req.qs.get("soort"), req.qs.get("klant_id"))


@route("POST", "/api/contracten")
def ct_nieuw(req, user):
    return domain.contract_aanmaken(user, req.body)


@route("GET", r"/api/contracten/(\d+)")
def ct(req, user, cid):
    return domain.contract(user, int(cid))


@route("PUT", r"/api/contracten/(\d+)")
def ct_upd(req, user, cid):
    return domain.contract_wijzigen(user, int(cid), req.body)


@route("POST", r"/api/contracten/(\d+)/verlengen")
def ct_verl(req, user, cid):
    return domain.contract_verlengen(user, int(cid), req.body)


@route("POST", r"/api/contracten/(\d+)/addendum")
def ct_add(req, user, cid):
    return domain.contract_addendum(user, int(cid), req.body)


@route("GET", "/api/opdrachten")
def opdrachten(req, user):
    return domain.opdrachten(user, req.qs.get("contract_id"), req.qs.get("status"))


@route("POST", "/api/opdrachten")
def op_nieuw(req, user):
    return domain.opdracht_opslaan(user, req.body)


@route("GET", r"/api/opdrachten/(\d+)")
def op(req, user, oid):
    return domain.opdracht(user, int(oid))


@route("PUT", r"/api/opdrachten/(\d+)")
def op_upd(req, user, oid):
    return domain.opdracht_opslaan(user, req.body, int(oid))


@route("POST", r"/api/opdrachten/(\d+)/afronden")
def op_af(req, user, oid):
    return domain.opdracht_afronden_bevestigen(user, int(oid))


# ============================================================ RFC's
@route("GET", "/api/rfcs")
def rfcs(req, user):
    return domain.rfcs(user, **{k: req.qs.get(k) for k in ("klant_id", "contract_id", "opdracht_id", "status", "change_type", "zoek")})


@route("POST", "/api/rfcs")
def rfc_nieuw(req, user):
    return domain.rfc(user, domain.rfc_aanmaken(user, req.body))


@route("POST", "/api/rfcs/contracttoets")
def rfc_toets_preview(req, user):
    ct = q1("SELECT * FROM contracten WHERE id=?", (req.body.get("contract_id"),))
    if not ct:
        raise Fout("Kies een contract")
    return domain.contracttoets(ct, req.body.get("gewenste_datum"), domain.num(req.body.get("geschatte_uren")))


@route("GET", r"/api/rfcs/(\d+)")
def rfc(req, user, rid):
    return domain.rfc(user, int(rid))


@route("PUT", r"/api/rfcs/(\d+)")
def rfc_upd(req, user, rid):
    return domain.rfc_wijzigen(user, int(rid), req.body)


@route("POST", r"/api/rfcs/(\d+)/toets")
def rfc_toets(req, user, rid):
    return domain.rfc_toets_opnieuw(user, int(rid))


# ============================================================ algemeen
@route("GET", "/api/zoeken")
def zoeken(req, user):
    return reports.zoeken(user, req.qs.get("q", ""))


@route("GET", r"/api/opmerkingen/(klant|contactpersoon|contract|opdracht|rfc)/(\d+)")
def opm_lijst(req, user, ent, eid):
    return domain.opmerkingen(user, ent, int(eid))


@route("POST", r"/api/opmerkingen/(klant|contactpersoon|contract|opdracht|rfc)/(\d+)")
def opm_nieuw(req, user, ent, eid):
    return domain.opmerking_plaatsen(user, ent, int(eid), req.body.get("tekst"))


@route("PUT", r"/api/opmerkingen/(\d+)")
def opm_upd(req, user, oid):
    return domain.opmerking_wijzigen(user, int(oid), req.body.get("tekst"))


@route("POST", r"/api/opmerkingen/(\d+)/verwijderen")
def opm_del(req, user, oid):
    return domain.opmerking_verwijderen(user, int(oid))


@route("GET", "/api/audit")
def audit(req, user):
    return q("SELECT * FROM audit WHERE entiteit=? AND entiteit_id=? ORDER BY id DESC", (req.qs.get("entiteit"), req.qs.get("id")))


@route("GET", "/api/signalen")
def signalen(req, user):
    alle = user["rol"] in ("beheerder", "directie")
    return q("SELECT s.*, u.naam ontvanger FROM signalen s LEFT JOIN users u ON u.id=s.ontvanger_id WHERE ontvanger_id=? OR ? ORDER BY gelezen, id DESC LIMIT 200",
             (user["id"], alle))


@route("POST", r"/api/signalen/(\d+)/gelezen")
def sig_gelezen(req, user, sid):
    with tx() as c:
        c.execute("UPDATE signalen SET gelezen=1 WHERE id=?", (int(sid),))
    return {"ok": True}


@route("GET", "/api/offertes")
def offertes(req, user):
    rows = q("""SELECT o.*, k.naam klantnaam, r.jira_key, r.titel FROM offertes o JOIN klanten k ON k.id=o.klant_id
                JOIN rfcs r ON r.id=o.rfc_id ORDER BY o.id DESC""")
    if user["rol"] not in domain.WAARDE_ZICHTBAAR:
        for r in rows:
            r["waarde"] = None
    return rows


@route("PUT", r"/api/offertes/(\d+)")
def offerte_upd(req, user, oid):
    if user["rol"] not in ("accountmanager", "beheerder"):
        raise Fout("Geen rechten", 403)
    st = req.body.get("status")
    if st not in ("Concept", "Verstuurd", "Geaccepteerd", "Afgewezen"):
        raise Fout("Ongeldige status")
    with tx() as c:
        c.execute("UPDATE offertes SET status=?, waarde=COALESCE(?,waarde) WHERE id=?", (st, domain.num(req.body.get("waarde")), int(oid)))
        domain.audit(c, user["naam"], "CRM", "offerte", int(oid), "status gewijzigd", {"status": st})
    return {"ok": True}


@route("GET", r"/api/rapport/([a-z-]+)")
def rapport(req, user, naam):
    if naam not in reports.RAPPORTEN:
        raise Fout("Onbekend rapport", 404)
    if naam == "integratiestatus" and user["rol"] not in ("beheerder", "directie"):
        raise Fout("Alleen voor functioneel beheer", 403)
    return reports.RAPPORTEN[naam](user, req.qs)


@route("GET", r"/api/export/([a-z]+)")
def export(req, user, wat):
    if wat.startswith("rapport"):
        pass
    if wat not in reports.EXPORTS:
        raise Fout("Onbekende export", 404)
    return Raw(reports.exporteer(user, wat), "text/csv; charset=utf-8", f"{wat}-{now()[:10]}.csv")


@route("GET", r"/api/export-rapport/([a-z-]+)")
def export_rapport(req, user, naam):
    data = reports.RAPPORTEN[naam](user, req.qs)
    rows = data if isinstance(data, list) else data.get("per_type") or data.get("recente_fouten") or []
    return Raw(reports.exporteer(user, f"rapport {naam}", rows), "text/csv; charset=utf-8", f"{naam}-{now()[:10]}.csv")


@route("POST", r"/api/import/([a-z]+)")
def importeer(req, user, wat):
    return reports.importeer(user, wat, req.body.get("csv", ""), req.body.get("forceer"))


# ============================================================ integratie-beheer
def beheer(user):
    if user["rol"] != "beheerder":
        raise Fout("Alleen voor functioneel beheerder", 403)


@route("GET", "/api/integratie/status")
def int_status(req, user):
    return integration.status()


@route("GET", "/api/integratie/berichten")
def int_berichten(req, user):
    beheer(user)
    st = req.qs.get("status")
    sql = "SELECT id,event_id,richting,type,entiteit,entiteit_id,status,pogingen,volgende_poging,fout,ts,verwerkt FROM berichten"
    return q(sql + (" WHERE status=?" if st else "") + " ORDER BY id DESC LIMIT 300", (st,) if st else ())


@route("GET", r"/api/integratie/berichten/(\d+)")
def int_bericht(req, user, bid):
    beheer(user)
    return q1("SELECT * FROM berichten WHERE id=?", (int(bid),))


@route("POST", r"/api/integratie/berichten/(\d+)/opnieuw")
def int_opnieuw(req, user, bid):
    beheer(user)
    integration.opnieuw(int(bid))
    return {"ok": True}


@route("POST", "/api/integratie/reconciliatie")
def int_recon(req, user):
    beheer(user)
    return integration.reconciliatie()


@route("POST", "/api/integratie/dagtaken")
def int_dag(req, user):
    beheer(user)
    return integration.dagtaken()


@route("GET", "/api/integratie/conflicten")
def int_conf(req, user):
    beheer(user)
    return q("SELECT * FROM conflicten ORDER BY opgelost, id DESC")


@route("POST", r"/api/integratie/conflicten/(\d+)/opgelost")
def int_conf_ok(req, user, cid):
    beheer(user)
    with tx() as c:
        c.execute("UPDATE conflicten SET opgelost=1 WHERE id=?", (int(cid),))
        domain.audit(c, user["naam"], "CRM", "conflict", int(cid), "afgehandeld")
    return {"ok": True}


@route("GET", "/api/integratie/config")
def int_cfg(req, user):
    return {r["sleutel"]: json.loads(r["waarde"]) for r in q("SELECT * FROM config")}


@route("PUT", "/api/integratie/config")
def int_cfg_put(req, user):
    beheer(user)
    with tx() as c:
        for k, v in req.body.items():
            if k not in db.DEFAULT_CONFIG:
                raise Fout(f"Onbekende configuratiesleutel {k}")
            c.execute("UPDATE config SET waarde=? WHERE sleutel=?", (json.dumps(v, ensure_ascii=False), k))
            domain.audit(c, user["naam"], "CRM", "config", None, f"configuratie '{k}' gewijzigd", {"nieuw": v})
    return {"ok": True}


@route("GET", "/api/integratie/contractvalidatie")
def int_validatie(req, user):
    ok, reden = integration.contract_validator(req.qs.get("asset"))
    return {"geldig": ok, "reden": reden}


# ============================================================ Jira-simulator (UI-acties als Jira-gebruiker)
@route("GET", "/api/jira-sim")
def sim(req, user):
    return jira_sim.overzicht()


@route("POST", "/api/jira-sim/offline")
def sim_offline(req, user):
    jira_sim.STATE["offline"] = bool(req.body.get("offline"))
    return {"offline": jira_sim.STATE["offline"]}


def _jira_user(req):
    return req.body.get("als") or "agent.jansen"


@route("POST", "/api/jira-sim/portaal")
def sim_portaal(req, user):
    return {"key": jira_sim.portaal_request(req.body, integration.contract_validator)}


@route("POST", r"/api/jira-sim/issue/([A-Z]+-\d+)/transitie")
def sim_trans(req, user, key):
    jira_sim.transitie(key, req.body["naar"], _jira_user(req), integration.contract_validator)
    return {"ok": True}


@route("POST", r"/api/jira-sim/issue/([A-Z]+-\d+)/velden")
def sim_velden(req, user, key):
    jira_sim.wijzig_velden(key, _jira_user(req), req.body)
    return {"ok": True}


@route("POST", r"/api/jira-sim/issue/([A-Z]+-\d+)/impactanalyse")
def sim_ia(req, user, key):
    jira_sim.impactanalyse(key, _jira_user(req), req.body)
    return {"ok": True}


@route("POST", r"/api/jira-sim/issue/([A-Z]+-\d+)/approval")
def sim_appr(req, user, key):
    jira_sim.approval(key, req.body.get("als") or "cab-lid", req.body["besluit"])
    return {"ok": True}


@route("POST", r"/api/jira-sim/issue/([A-Z]+-\d+)/taak")
def sim_taak(req, user, key):
    return {"key": jira_sim.maak_taak(key, req.body.get("summary") or "Taak", _jira_user(req))}


@route("POST", r"/api/jira-sim/issue/([A-Z]+-\d+)/status")
def sim_status(req, user, key):
    jira_sim.taak_status(key, req.body["status"], _jira_user(req))
    return {"ok": True}


@route("POST", r"/api/jira-sim/issue/([A-Z]+-\d+)/worklog")
def sim_wl(req, user, key):
    jira_sim.worklog(key, req.body["uren"], _jira_user(req))
    return {"ok": True}


# ============================================================ PDF/print impactanalyse (FR-IA-05)
def print_ia(user, iid):
    ia = q1("SELECT * FROM impactanalyses WHERE id=?", (iid,))
    if not ia:
        raise Fout("Niet gevonden", 404)
    r = domain.rfc(user, ia["rfc_id"])
    e = lambda v: html.escape(str(v if v not in (None, "") else "—"))
    rij = lambda k, v: f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>"
    cv = json.loads(ia["contractverschil"] or "{}")
    with tx() as c:
        domain.audit(c, user["naam"], "CRM", "export", ia["rfc_id"], f"impactanalyse v{ia['versie']} als PDF")
    return f"""<!doctype html><html lang="nl"><meta charset="utf-8"><title>Impactanalyse {e(r['jira_key'])} v{ia['versie']}</title>
<style>body{{font:14px/1.5 system-ui,sans-serif;max-width:760px;margin:32px auto;color:#1a1a1a;padding:0 16px}}h1{{font-size:22px;margin:0}}
.sub{{color:#555;margin-bottom:24px}}table{{width:100%;border-collapse:collapse;margin:12px 0 24px}}th,td{{text-align:left;vertical-align:top;padding:6px 8px;border-bottom:1px solid #ddd}}
th{{width:34%;color:#444;font-weight:600}}h2{{font-size:15px;margin:24px 0 4px;border-bottom:2px solid #1a1a1a;padding-bottom:4px}}
.score{{font-size:28px;font-weight:700}}@media print{{.np{{display:none}}}}</style>
<p class="np"><button onclick="print()">Opslaan als PDF / afdrukken</button></p>
<h1>Impactanalyse — {e(r['titel'])}</h1>
<div class="sub">{e(r['jira_key'])} · versie {ia['versie']} · {e(r['klantnaam'])} · contract {e(r['contractnummer'])}</div>
<h2>Samenvatting</h2><table>{rij('Change-type', r['change_type'])}{rij('Prioriteit', r['prioriteit'])}{rij('Gewenste datum', r['gewenste_datum'])}
{rij('Binnen contract', r['binnen_contract'])}<tr><th>Risicoscore (kans × impact)</th><td><span class="score">{ia['risicoscore']}</span> ({ia['kans']} × {ia['impact']}){' — CAB-behandeling vereist' if ia['risicoscore'] >= 15 else ''}</td></tr></table>
<h2>Impact</h2><table>{rij('Techniek', f"{ia['impact_techniek']} — {ia['toelichting_techniek'] or ''}")}{rij('Organisatie / gebruikers', f"{ia['impact_organisatie']} — {ia['toelichting_organisatie'] or ''}")}
{rij('Planning', ia['impact_planning'])}{rij('Kosten', f"€ {ia['kosten']:,.0f}".replace(',', '.'))}{rij('Uren', ia['uren'])}{rij('Contract / SLA', ia['impact_contract'])}
{rij('Security en privacy', ia['security_privacy'])}{rij('DPIA nodig', 'Ja' if ia['dpia_nodig'] else 'Nee')}{rij('Terugvalplan', ia['terugvalplan'])}</table>
<h2>Vergelijking met contract</h2><table>{rij('Change-regime', cv.get('regime'))}
{rij('Resterend urenbudget → na change', f"{cv['uren']['rest_budget']:.0f} u → {cv['uren']['na_change']:.0f} u" if cv.get('uren') else 'Geen urenplafond')}
{rij('SLA-niveau', (cv.get('sla') or {}).get('niveau'))}</table>
<p class="sub">Opgesteld door {e(ia['opgesteld_door'])} op {e((ia['datum'] or '')[:10])}</p></html>"""


# ============================================================ HTTP-plumbing
class Raw:
    def __init__(self, body, ctype, filename=None):
        self.body, self.ctype, self.filename = body, ctype, filename


class Handler(BaseHTTPRequestHandler):
    server_version = "CRM/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("CRM_LOG"):
            sys.stderr.write("%s %s\n" % (self.command, fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str))

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def _dispatch(self, method):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        self.qs = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            if path == "/webhooks/jira" and method == "POST":
                code, res = integration.ontvang_webhook(raw, self.headers.get("X-Hub-Signature"), SECRET)
                return self._json(code, res)
            if path == "/health":
                st = integration.status()
                return self._json(200, {"status": st["health"], "queue": st["queue_diepte"]})
            if path.startswith("/print/impactanalyse/"):
                user = q1("SELECT * FROM users WHERE id=?", (self.qs.get("u"),))
                if not user:
                    raise Fout("Niet ingelogd", 401)
                return self._send(200, print_ia(user, int(path.rsplit("/", 1)[1])), "text/html; charset=utf-8")
            if not path.startswith("/api/"):
                return self._static(path)
            self.body = json.loads(raw or b"{}") if raw else {}
            user = q1("SELECT * FROM users WHERE id=?", (self.headers.get("X-User") or self.qs.get("u") or 0,))
            if not user and path not in ("/api/users", "/api/login"):
                raise Fout("Niet ingelogd", 401)
            for m, rx, fn in ROUTES:
                mt = rx.match(path)
                if m == method and mt:
                    res = fn(self, user, *mt.groups())
                    if isinstance(res, Raw):
                        h = {"Content-Disposition": f'attachment; filename="{res.filename}"'} if res.filename else {}
                        return self._send(200, res.body, res.ctype, h)
                    return self._json(200, res)
            raise Fout("Niet gevonden", 404)
        except Fout as e:
            self._json(e.code, {"fout": str(e), **e.extra})
        except jira_sim.JiraValidatie as e:
            self._json(422, {"fout": str(e)})
        except jira_sim.JiraFout as e:
            self._json(503, {"fout": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"fout": f"Interne fout: {type(e).__name__}"})

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        fp = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not fp.startswith(STATIC) or not os.path.isfile(fp):
            fp = os.path.join(STATIC, "index.html")
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype, {"Cache-Control": "no-cache"})


SECRET = b""


def demo_bootstrap(port):
    """Brengt de seed-data via de echte koppeling in Jira en speelt een paar scenario's af."""
    beheer = q1("SELECT * FROM users WHERE rol='beheerder'")

    def wacht():
        for _ in range(200):
            if not q1("SELECT 1 x FROM berichten WHERE status IN ('wachtend','retry')"):
                time.sleep(0.6)
                if not q1("SELECT 1 x FROM berichten WHERE status IN ('wachtend','retry')"):
                    return
            time.sleep(0.2)

    for kid in (1, 2, 3):
        domain.klant_wijzigen(beheer, kid, {"status": "Actief"})
    for cid in (1, 2, 3, 4, 5):
        domain.contract_wijzigen(beheer, cid, {"status": "Actief"})
    wacht()
    for oid in (1, 2, 3):
        domain.opdracht_opslaan(beheer, {"status": "Lopend"}, oid)
    wacht()
    epics = {o["id"]: o["jira_key"] for o in q("SELECT id, jira_key FROM opdrachten")}
    t1 = jira_sim.maak_taak(epics[2], "Inventarisatie archiefbestanden", "engineer.devries")
    jira_sim.maak_taak(epics[2], "Migratiescript e-Depot", "engineer.devries")
    jira_sim.taak_status(t1, "Done", "engineer.devries")
    jira_sim.worklog(t1, 310, "engineer.devries")
    jira_sim.worklog(epics[3], 690, "integratie.specialist")
    jira_sim.worklog(epics[1], 655, "beheerteam")
    wacht()
    sanne = q1("SELECT * FROM users WHERE id=1")
    r1 = domain.rfc_aanmaken(sanne, {"titel": "Koppeling zaaksysteem met MijnGemeente-portaal", "omschrijving": "Burgers moeten de status van hun aanvraag kunnen volgen.",
                                     "klant_id": 1, "contract_id": 1, "opdracht_id": 1, "aanvrager_id": 1, "change_type": "Normaal",
                                     "prioriteit": "Hoog", "gewenste_datum": db.add_months(db.today(), 2).isoformat()})
    domain.rfc_aanmaken(sanne, {"titel": "Extra exportformaat voor e-Depot", "klant_id": 1, "contract_id": 2, "opdracht_id": 2,
                                "aanvrager_id": 2, "change_type": "Standaard", "prioriteit": "Midden"})
    wacht()
    cust = q1("SELECT account_id FROM j_customers WHERE email='a.verbeek@horizonzorg.nl'")
    ct3 = q1("SELECT jira_key FROM contracten WHERE id=3")
    if cust and ct3["jira_key"]:
        jira_sim.portaal_request({"reporter": cust["account_id"], "contract": ct3["jira_key"], "summary": "Nieuwe interface met labsysteem",
                                  "description": "Koppeling HL7 met het nieuwe laboratoriumsysteem", "changeType": "Normaal", "priority": "Hoog",
                                  "gewenste_datum": db.add_months(db.today(), 3).isoformat()}, integration.contract_validator)
    wacht()
    k1 = q1("SELECT jira_key FROM rfcs WHERE id=?", (r1,))["jira_key"]
    if k1:
        jira_sim.transitie(k1, "Beoordeling", "change.manager", integration.contract_validator)
        jira_sim.impactanalyse(k1, "change.manager", {
            "impact_techniek": "Midden", "toelichting_techniek": "Nieuwe API-koppeling, geen wijziging datamodel",
            "impact_organisatie": "Laag", "toelichting_organisatie": "Communicatie naar burgers via website",
            "impact_planning": "6 weken incl. acceptatietest", "kosten": 18500, "uren": 140, "kans": 3, "impact": 4,
            "impact_contract": "Past binnen strippenkaart; raakt SLA niet", "security_privacy": "Persoonsgegevens via bestaande koppeling; DPIA-update nodig",
            "dpia_nodig": True, "terugvalplan": "Feature-toggle uit; oude statuspagina blijft beschikbaar"})
    wacht()
    integration.dagtaken()
    print("[demo] seed-data gesynchroniseerd met Jira-simulator")


def main():
    global SECRET
    port = int(os.environ.get("PORT", "8420"))
    vers = not os.path.exists(db.DB_PATH)
    db.init()
    SECRET = webhook_secret()
    jira_sim.STATE.update(webhook_url=f"http://127.0.0.1:{port}/webhooks/jira", secret=SECRET)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    integration.start()
    if vers and os.environ.get("CRM_DEMO", "1") == "1":
        threading.Thread(target=demo_bootstrap, args=(port,), daemon=True).start()
    print(f"CRM draait op http://127.0.0.1:{port}  (database: {db.DB_PATH})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        integration.STOP.set()


if __name__ == "__main__":
    main()
