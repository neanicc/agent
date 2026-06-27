def transform(rows: list[dict]) -> list[dict]:
    """Normalize customer plan names to upper case."""
    return [{**r, "plan": r["plan"].upper()} for r in rows]
