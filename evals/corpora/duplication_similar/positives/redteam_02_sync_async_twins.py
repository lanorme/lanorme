# why: positive - a sync/async twin pair copied line for line (only `await` differs); the shared logic belongs in one helper or a generated twin.
def fetch_all(client, urls):
    results = []
    failed = []
    for url in urls:
        response = client.get(url)
        if not response.ok:
            failed.append(url)
            continue
        results.append(response.json())
    if failed:
        log.warning("failed: %s", failed)
    return results


async def fetch_all_async(client, urls):
    results = []
    failed = []
    for url in urls:
        response = await client.get(url)
        if not response.ok:
            failed.append(url)
            continue
        results.append(response.json())
    if failed:
        log.warning("failed: %s", failed)
    return results
