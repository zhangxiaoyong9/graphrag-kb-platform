"""L1 read-only Cypher validator + L2 row-cap truncation helper.

Both functions are pure (no ``neo4j`` import) so they are unit-testable without
the driver installed. Belt-and-suspenders with L0 (a read-only DB user): either
layer failing alone still blocks writes.
"""

from __future__ import annotations

import re

# Root verbs whose statement is a read. PROFILE/EXPLAIN wrap a read query;
# SHOW lists metadata. Everything else (CREATE/MERGE/DELETE/SET/REMOVE/DROP/
# LOAD CSV/CALL-for-write) is rejected.
_READONLY_VERBS = {"MATCH", "RETURN", "PROFILE", "EXPLAIN", "SHOW"}

# Matches ``CALL <proc>`` and lets us admit read procedures (vector ANN) while
# rejecting write procedures (apoc.create.*). We admit the well-known read-only
# vector / schema-inspection procedures and reject everything else named
# ``create.*`` / ``delete.*`` / ``merge.*`` / ``remove.*``.
_READ_PROC_ALLOW = re.compile(
    r"^db\.(index\.vector\.queryNodes|indexes|constraints|labels|properties|relationshipTypes)",
    re.IGNORECASE,
)
_WRITE_PROC_DENY = re.compile(r"(create|delete|merge|remove|set|drop)", re.IGNORECASE)

# Write/DDL clause keywords that may appear after a read-only root verb
# (e.g. ``MATCH (n) DELETE n`` / ``MATCH (n) SET n.x = 1``). Matched as
# standalone tokens (case-insensitive, word boundaries) so substrings like
# ``REMOVE`` inside a property name don't false-fire. DETACH is admitted on
# its own but is harmless without DELETE; DETACH DELETE is caught by DELETE.
_WRITE_CLAUSE = re.compile(
    r"\b("
    r"CREATE|MERGE|DELETE|DETACH\s+DELETE|SET|REMOVE|DROP|"
    r"LOAD\s+CSV|FOREACH|REPLACE|ADD|RENAME"
    r")\b",
    re.IGNORECASE,
)


def _strip_comments_and_strings(sql: str) -> str:
    """Remove ``//`` line comments and quoted string literals so verbs inside
    strings/comments cannot mask a write verb."""
    out_lines = []
    for line in sql.splitlines():
        # strip // line comments (keep everything before them)
        if "//" in line:
            line = line.split("//", 1)[0]
        out_lines.append(line)
    text = "\n".join(out_lines)
    # strip single-quoted string literals (Cypher uses single quotes)
    text = re.sub(r"'(?:[^'\\\\]|\\\\.)*'", "''", text)
    return text


def _statement_is_readonly(stmt: str) -> bool:
    stmt = stmt.strip()
    if not stmt:
        return True  # empty between semicolons -> vacuously read-only
    tokens = stmt.split()
    # If the first token is PROFILE/EXPLAIN, the wrapped statement must also
    # be read-only (recurse on the remainder).
    if tokens and tokens[0].upper() in {"PROFILE", "EXPLAIN"}:
        return _statement_is_readonly(" ".join(tokens[1:]))
    verb = tokens[0].upper() if tokens else ""
    if verb in _READONLY_VERBS:
        # Head verb is read-only, but a read query never contains a write/DDL
        # clause: scan the whole statement so ``MATCH (n) DELETE n`` is rejected.
        return not _WRITE_CLAUSE.search(stmt)
    if verb == "CALL":
        # parse ``CALL <proc>(...)`` -> the procedure path
        m = re.match(r"\s*CALL\s+([\w\.]+)\s*\(", stmt, re.IGNORECASE)
        proc = m.group(1) if m else ""
        if _READ_PROC_ALLOW.match(proc):
            return True
        if _WRITE_PROC_DENY.search(proc):
            return False
        # Unknown read procedure: be conservative and reject.
        return False
    return False


def is_readonly_cypher(s: str) -> bool:
    """True iff EVERY ``;``-separated statement in ``s`` is read-only.

    Comments and string literals are stripped first so a write verb buried in a
    string cannot slip through. PROFILE/EXPLAIN/SHOW are admitted (read-side).
    CALL is admitted only for known read procedures (vector ANN, schema list);
    unknown procedures are rejected (conservative).
    """
    if not s or not s.strip():
        return False
    cleaned = _strip_comments_and_strings(s)
    statements = cleaned.split(";")
    return all(_statement_is_readonly(stmt) for stmt in statements)


def truncate_rows(rows: list, cap: int) -> tuple[list, bool]:
    """Return ``(rows[:cap], len(rows) > cap)``. The flag feeds ``StreamDone.truncated``."""
    truncated = len(rows) > cap
    return (rows[:cap] if truncated else list(rows)), truncated
