"""Publish a public, unauthenticated fallback catalog after a complete release."""
import base64
import json
import os
import urllib.error
import urllib.request

repository = os.environ["GITHUB_REPOSITORY"]
token = os.environ["GH_TOKEN"]
api = "https://api.github.com/repos/" + repository

def request(path, data=None):
    headers = {"User-Agent": "NeonReleaseCatalog", "Accept": "application/vnd.github+json",
               "Authorization": "Bearer " + token}
    req = urllib.request.Request(api + path, headers=headers,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 method="PUT" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)

releases = request("/releases?per_page=100")
catalog = []
for release in releases:
    if release.get("draft"):
        continue
    item = {key: release.get(key) for key in ("tag_name", "name", "body", "published_at", "prerelease", "draft")}
    item["assets"] = [
        {key: asset.get(key) for key in ("name", "browser_download_url", "size", "digest")}
        for asset in release.get("assets", [])
    ]
    catalog.append(item)
path = "/contents/release-catalog.json"
payload = {"branch": "main", "message": "Update public installer release catalog",
           "content": base64.b64encode(json.dumps(catalog, ensure_ascii=False, indent=2).encode()).decode()}
try:
    existing = request(path + "?ref=main")
    payload["sha"] = existing["sha"]
except urllib.error.HTTPError as exc:
    if exc.code != 404:
        raise
request(path, payload)
print("Published public catalog:", len(catalog), "releases. No login needed by installers.")
