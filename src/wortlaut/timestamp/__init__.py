"""wortlaut.timestamp — RFC-3161-Zeitstempel (Spec 0076).

Eigener Infrastruktur-Layer (Muster: ``wortlaut.archive``). Stempelt den
content_hash einer archivierten Quelle mit einer unabhängigen TSA (RFC 3161)
und verifiziert Token gegen die im Paket gepinnten Trust-Anker (Root UND Leaf,
§0a-🔴). Importiert keinen anderen wortlaut-Layer (AC19).
"""
