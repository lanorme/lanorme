def normalise_slug(text):
    cleaned = text.strip()
    cleaned = cleaned.lower()
    cleaned = cleaned.replace(" ", "-")
    cleaned = re.sub(r"[^a-z0-9-]", "", cleaned)
    cleaned = cleaned.encode("ascii", errors="ignore").decode()
    cleaned = cleaned[:64]
    return cleaned


def normalise_handle(text):
    cleaned = text.strip()
    cleaned = cleaned.lower()
    cleaned = cleaned.replace(" ", "-")
    cleaned = re.sub(r"[^a-z0-9-]", "", cleaned)
    cleaned = cleaned.encode("ascii", errors="replace").decode()
    cleaned = cleaned[:32]
    return cleaned
