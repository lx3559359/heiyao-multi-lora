"""Compare a local weight with published versions of the same website LoRA."""
import asyncio
import hashlib
import re
from unicodedata import normalize


def family_and_version(title):
    title = normalize("NFKC", title).casefold()
    match = re.search(r"lora\s*[:_-]?\s*v(\d{1,5})(?!\d)", title)
    if match is None:
        return None, None, None
    prefix = re.sub(r"角色\s*$", "", title[:match.start()])
    return re.sub(r"[\s|·]+", "", prefix), int(match[1]), f"v{match[1]}"


def fingerprint(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


async def inspect_slot(site, name, path):
    current = {"name": name, "version": None}
    result = {"current": current, "latest": None, "state": "missing" if path is None else "unknown"}
    if path is None:
        return result
    items, result["index_online"] = await asyncio.gather(site.search(""), site.refresh_index())
    available_pins = await site.catalog_weights(items)
    size = path.stat().st_size
    records = list(available_pins.items()) + list(site.index.items()) + list(site.bundled_index.items())
    candidates = [(resource_id, row) for resource_id, row in records if row["bytes"] == size]
    if not candidates:
        return result
    digest = await asyncio.to_thread(fingerprint, path)
    matches = [(resource_id, row) for resource_id, row in candidates if row["sha256"] == digest]
    if not matches:
        return result
    published = {item["id"]: item["title"] for item in items}
    identities = []
    for resource_id, row in matches:
        title = row.get("catalog_title") or published.get(resource_id, row.get("title", ""))
        family, _, _ = family_and_version(title)
        version = re.fullmatch(r"v(\d{1,5})", row["version"])
        identities.append((family or title or resource_id, int(version[1]) if version else -1, resource_id, row, title))
    if len({identity[0] for identity in identities}) != 1:
        result["state"] = "ambiguous"
        return result

    family, local_version, resource_id, record, title = max(identities, key=lambda item: item[1])
    current.update(id=resource_id, version=record["version"], title=title)
    if local_version < 0:
        if resource_id in published:
            result.update(state="current", latest={"id": resource_id, "title": published[resource_id], "version": record["version"]})
        return result

    siblings = []
    for published_id, published_title in published.items():
        series, rank, version = family_and_version(published_title)
        if series == family:
            siblings.append((rank, published_id, published_title, version))
    if not siblings:
        result["state"] = "unlisted"
        return result
    newest_rank = max(entry[0] for entry in siblings)
    newest = [entry for entry in siblings if entry[0] == newest_rank]
    # Prefer the exact current release when website entries repeat the same version.
    _, latest_id, latest_title, latest_version = next(
        (entry for entry in newest if entry[1] == resource_id), newest[0],
    )
    latest = {"id": latest_id, "title": latest_title, "version": latest_version, "available": False}
    result["latest"] = latest
    if newest_rank <= local_version:
        result["state"] = "current"
        return result

    pins = [available_pins.get(entry[1]) for entry in newest]
    if len(newest) > 1 and (None in pins or len({pin["sha256"] for pin in pins}) != 1):
        result["state"] = "ambiguous"
        return result
    pin = available_pins.get(latest_id)
    if not pin or pin["version"] != latest_version:
        result["state"] = "unverified"
        return result
    if pin["sha256"] == digest:
        result["state"] = "equivalent"
        return result
    latest.update(await site.detail(latest_id))
    result["state"] = "update"
    return result
