# CRM met Jira-integratie — proof of concept

Werkende implementatie van het *Functioneel en Non-functioneel Ontwerp* (22 nov 2026): CRM, integratielaag en een
Jira Cloud-simulator, end-to-end (vervolgstap 9.3.3 uit het ontwerp).

## Starten

Alleen Python 3.10+ nodig, geen extra pakketten.

```bash
cd crm
python3 -m app.server
```

Open http://127.0.0.1:8420 en kies een gebruiker (gesimuleerde SSO). Bij de eerste start wordt demo-data aangemaakt en
via de echte koppeling naar de Jira-simulator gesynchroniseerd. Opnieuw beginnen: verwijder `crm/data/`.

Omgevingsvariabelen: `PORT` (8420), `CRM_DB` (pad naar SQLite), `CRM_DEMO=0` (geen demo-scenario's),
`JIRA_MODE=cloud` + variabelen in `app/jira_cloud.py` voor een echte Jira-site.

## Architectuur

| Bestand | Rol |
|---|---|
| `app/domain.py` | Gegevensmodel-logica, validaties, contracttoets, risicoscore, RBAC, audit trail, outbox-events |
| `app/integration.py` | Integratielaag: berichtenqueue (beide richtingen), mapping, idempotentie, lusvoorkoming, conflictregel, retry + DLQ, reconciliatie, dagtaken |
| `app/jira_sim.py` | Jira Cloud-sandbox: JSM Organizations/Customers, Assets, Change-workflow met validator, Epics, worklogs, HMAC-webhooks |
| `app/jira_cloud.py` | Adapter voor echte Jira Cloud (REST v3, JSM, Assets) — **niet getest tegen een echte site** |
| `app/reports.py` | Zes rapportages, globale zoekfunctie, CSV-import/export |
| `app/server.py` | HTTP/JSON-API, webhook-endpoint, health check |
| `static/` | Webinterface (vanilla JS, responsive, licht/donker) |

CRM en Jira praten nooit rechtstreeks: elke wijziging wordt in dezelfde transactie als outbox-bericht vastgelegd
en door de worker via de adapter verwerkt. Jira-webhooks komen via `/webhooks/jira` (HMAC-gevalideerd) in dezelfde queue.

## Wat je kunt uitproberen

- **Jira-simulator → "Storing simuleren"**: wijzig daarna iets in het CRM. Het CRM blijft werken, berichten gaan op
  retry met exponentiële backoff en na 5 pogingen naar de dead-letter queue (Integratiestatus → DLQ → Opnieuw).
- **Conflict**: zet storing aan, wijzig de titel van een RFC in het CRM én in de simulator ("Velden wijzigen"). De
  laatste wijziging wint en het conflict staat bij Integratiestatus → Conflicten.
- **Request via klantportaal** op het verlopen contract CT-2023-0015: het CRM weigert koppeling en de validator
  blokkeert "Indienen".
- **Impactanalyse** met kans × impact ≥ 15: directe goedkeuring wordt geblokkeerd, CAB-approval is vereist.
- **Uren loggen** op een Epic: bestede uren, voortgang en signalen bij 75%/90% verschijnen in het CRM.
- Inloggen als Servicemanager of Consultant: contractwaarde is afgeschermd.

## Dekking van de eisen

Gerealiseerd: FR-KL-01..04, 06 · FR-CP-01..05 · FR-CT-01..07 · FR-OP-01..04 · FR-RFC-01..07 · FR-IA-01..05 (PDF via
afdrukken) · FR-AL-01..04 · alle synchronisatieregels uit 5.3 · de scenario's uit 5.4 · RBAC uit hoofdstuk 6 · de zes
rapportages uit hoofdstuk 7 (filter + export) · NFR-BC-02, NFR-SE-05/06, NFR-PR-03/04/05, NFR-BH-01/03, NFR-BR-02.

Niet of alleen gesimuleerd:
- **FR-KL-05** (KvK API) en **FR-AL-05** (Engelse interface) zijn niet gebouwd.
- **SSO/MFA via Entra ID** is gesimuleerd met een gebruikerskeuze; er is geen echte authenticatie. Niet in productie zetten zonder OIDC.
- **Jira Cloud-adapter** is geschreven volgens de API-documentatie maar niet tegen een echte site getest; Assets-attribuut-ID's moeten per site worden geconfigureerd.
- Infrastructuur-eisen (TLS, AES-256 at rest, key vault, SIEM, back-ups, OTAP, 99,5% beschikbaarheid, pentest, DPIA) horen bij hosting en zijn hier niet ingericht.
- SQLite is prima voor de PoC; voor NFR-SC (20.000 RFC's, 1.000 events/min, horizontaal schalen) is PostgreSQL + een echte queue nodig.
