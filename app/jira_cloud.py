"""Adapter voor een echte Jira Cloud-site (REST API v3, JSM API, Assets API).

LET OP: deze adapter is geschreven tegen de gedocumenteerde Atlassian-API's maar is NIET getest tegen een echte site.
Activeren met JIRA_MODE=cloud en de omgevingsvariabelen hieronder. In productie komen de secrets uit een key vault
(NFR-SE-04) en gebruikt de koppeling een serviceaccount met scoped API-token of OAuth 2.0 (3LO).

  JIRA_BASE_URL         https://<site>.atlassian.net
  JIRA_EMAIL            e-mail van het serviceaccount
  JIRA_API_TOKEN        scoped API-token
  JIRA_SERVICEDESK_ID   id van de JSM-servicedesk voor Changes
  JIRA_CHANGE_REQTYPE   request type id voor 'Change'
  JIRA_ASSETS_WORKSPACE workspace-id van Assets
  JIRA_FIELD_MAP        JSON: {"cf_contract": "customfield_10050", ...}
  JIRA_ASSETS_TYPES     JSON: {"Klant": {"id": "1", "attrs": {"Klantnummer": "11", ...}}, "Contract": {...}}
"""
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .jira_sim import JiraFout, JiraValidatie


class CloudAdapter:
    naam = "Jira Cloud (REST v3)"

    def __init__(self):
        e = os.environ
        self.base = e["JIRA_BASE_URL"].rstrip("/")
        tok = base64.b64encode(f"{e['JIRA_EMAIL']}:{e['JIRA_API_TOKEN']}".encode()).decode()
        self.headers = {"Authorization": f"Basic {tok}", "Accept": "application/json", "Content-Type": "application/json"}
        self.sd = e.get("JIRA_SERVICEDESK_ID")
        self.ws = e.get("JIRA_ASSETS_WORKSPACE")
        self.fmap = json.loads(e.get("JIRA_FIELD_MAP", "{}"))
        self.types = json.loads(e.get("JIRA_ASSETS_TYPES", "{}"))

    # ---------------------------------------------------------------- http met throttling/backoff op 429
    def _req(self, method, url, body=None, tries=4):
        if not url.startswith("http"):
            url = self.base + url
        data = json.dumps(body).encode() if body is not None else None
        for n in range(tries):
            req = urllib.request.Request(url, data=data, method=method, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    txt = r.read()
                    return json.loads(txt) if txt else None
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(float(e.headers.get("Retry-After") or 2 ** n))
                    continue
                raise JiraValidatie(f"Jira {e.code}: {e.read()[:300].decode(errors='replace')}")
            except urllib.error.URLError as e:
                raise JiraFout(str(e))
        raise JiraFout(f"Jira bleef 429/5xx teruggeven op {method} {url}")

    def _f(self, fields):
        return {self.fmap.get(k, k): v for k, v in fields.items() if k in self.fmap or k in ("priority",)}

    # ---------------------------------------------------------------- JSM
    def create_organization(self, naam):
        r = self._req("POST", "/rest/servicedeskapi/organization", {"name": naam})
        if self.sd:
            self._req("POST", f"/rest/servicedeskapi/servicedesk/{self.sd}/organization", {"organizationId": int(r["id"])})
        return r["id"]

    def rename_organization(self, org_id, naam):
        pass  # JSM API kent geen rename; naam staat in het Assets-object Klant

    def add_customer(self, org_id, email, naam):
        try:
            r = self._req("POST", "/rest/servicedeskapi/customer", {"email": email, "displayName": naam})
            aid = r["accountId"]
        except JiraValidatie:
            r = self._req("GET", "/rest/api/3/user/search?" + urllib.parse.urlencode({"query": email}))
            aid = r[0]["accountId"]
        self._req("POST", f"/rest/servicedeskapi/organization/{org_id}/user", {"accountIds": [aid]})
        return aid

    def remove_customer(self, org_id, account_id):
        if org_id:
            self._req("DELETE", f"/rest/servicedeskapi/organization/{org_id}/user", {"accountIds": [account_id]})

    def gdpr_request(self, account_id):
        # Verwijdering van Atlassian-accounts verloopt via de Atlassian-beheerder (Personal data request); hier een melding.
        return {"status": "handmatig af te handelen via admin.atlassian.com", "accountId": account_id}

    # ---------------------------------------------------------------- Assets
    def upsert_asset(self, objtype, key, label, attrs):
        t = self.types[objtype]
        body = {"objectTypeId": t["id"], "attributes": [
            {"objectTypeAttributeId": t["attrs"][k], "objectAttributeValues": [{"value": str(v)}]}
            for k, v in attrs.items() if k in t["attrs"] and v is not None]}
        api = f"https://api.atlassian.com/jsm/assets/workspace/{self.ws}/v1/object"
        if key:
            self._req("PUT", f"{api}/{key}", body)
            return key
        return self._req("POST", f"{api}/create", body)["objectKey"]

    # ---------------------------------------------------------------- issues
    def create_issue(self, project, issuetype, summary, description, fields, parent=None, status=None):
        f = {"project": {"key": project}, "issuetype": {"name": issuetype}, "summary": summary,
             "description": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": description or "-"}]}]},
             **self._f(fields)}
        if parent:
            f["parent"] = {"key": parent}
        if "priority" in fields:
            f["priority"] = {"name": fields["priority"]}
        return self._req("POST", "/rest/api/3/issue", {"fields": f})["key"]

    def update_issue(self, key, summary=None, description=None, fields=None, parent=None):
        f = self._f(fields or {})
        if summary:
            f["summary"] = summary
        if parent:
            f["parent"] = {"key": parent}
        if fields and "priority" in fields:
            f["priority"] = {"name": fields["priority"]}
        self._req("PUT", f"/rest/api/3/issue/{key}", {"fields": f})

    def _norm(self, i):
        inv = {v: k for k, v in self.fmap.items()}
        f = i["fields"]
        return {"key": i["key"], "issuetype": f["issuetype"]["name"], "summary": f.get("summary"), "description": "",
                "status": f["status"]["name"], "parent": (f.get("parent") or {}).get("key"), "updated": f.get("updated", "")[:19],
                "updated_by": None, "fields": {inv.get(k, k): v for k, v in f.items()}}

    def get_issue(self, key):
        return self._norm(self._req("GET", f"/rest/api/3/issue/{key}"))

    def search(self, issuetype=None):
        jql = f'issuetype = "{issuetype}"' if issuetype else "order by updated"
        out, token = [], None
        while True:
            p = {"jql": jql, "maxResults": 100, "fields": "*all"}
            if token:
                p["nextPageToken"] = token
            r = self._req("GET", "/rest/api/3/search/jql?" + urllib.parse.urlencode(p))
            out += [self._norm(i) for i in r.get("issues", [])]
            token = r.get("nextPageToken")
            if not token:
                return out

    def epic_voortgang(self, epic_key):
        kids = self._req("GET", "/rest/api/3/search/jql?" + urllib.parse.urlencode(
            {"jql": f"parent = {epic_key}", "fields": "status,timespent", "maxResults": 500})).get("issues", [])
        done = sum(1 for k in kids if k["fields"]["status"]["statusCategory"]["key"] == "done")
        sec = sum(k["fields"].get("timespent") or 0 for k in kids)
        return {"issues": len(kids), "done": done, "pct": round(100 * done / len(kids), 1) if kids else 0.0, "uren": round(sec / 3600, 2)}
