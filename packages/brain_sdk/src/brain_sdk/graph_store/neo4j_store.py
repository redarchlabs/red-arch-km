"""Neo4j graph store implementation with tenant-scoped labels."""

from __future__ import annotations

import logging
import math
import re
from typing import Any, cast

from neo4j import Driver, GraphDatabase

logger = logging.getLogger(__name__)

_LABEL_ENTITY = "Entity"
_REL_TYPE = "REL"
_PROP_NAME = "name"
_PROP_DOCUMENT_KEY = "document_key"
_PROP_TAGS = "tags"
_PROP_ACCESS_KEYS = "access_keys"
_PROP_TYPE = "type"

# Cap on triplets returned to the RAG prompt. Callers slice further; this just
# stops a broad query from dragging the whole graph into memory.
_RELATIONSHIP_SEARCH_LIMIT = 50

# Question words and filler. Without these, "What does the manual say about Adam?"
# matches every triplet containing "the", which is most of them — the graph block
# then drowns the passages it is meant to supplement.
_STOPWORDS = frozenset(
    """
    a about after all also an and any are as at be been before both but by can could
    did do does for from had has have how i if in into is it its me more most must my
    no not of on only or other our out over should so some such than that the their
    them then there these they this to up very was we were what when where which while
    who whom why will with would you your
    """.split()  # noqa: SIM905 - a wrapped word block stays readable; a 70-item list literal would not
)

_MIN_TERM_LEN = 3


class Neo4jGraphStore:
    """GraphStore implementation backed by Neo4j with tenant isolation via labels."""

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        *,
        database: str | None = None,
    ) -> None:
        self._driver: Driver = GraphDatabase.driver(uri, auth=(user, password))
        self._db = database

    def _cypher(self, query: str, **params: Any) -> list[dict[str, Any]]:
        with self._driver.session(database=self._db) as sess:
            result = sess.run(query, **params)
            return [r.data() for r in result]

    def _tenant_label(self, tenant_id: str) -> str:
        """Bare tenant label, sanitized for safe interpolation into Cypher.

        Labels cannot be parameterized in Cypher, so this is the only place the
        tenant id reaches a query as literal text — hence the strict whitelist.
        Matches the fact store's labelling so both read the same nodes.
        """
        return f"Tenant_{re.sub(r'[^A-Za-z0-9_]', '_', tenant_id)}"

    def _tenant_labels(self, tenant_id: str) -> str:
        return f":{_LABEL_ENTITY}:{self._tenant_label(tenant_id)}"

    def initialize_tenant(self, tenant_id: str) -> None:
        lbls = self._tenant_labels(tenant_id)
        cypher = f"""
        MERGE (a{lbls} {{_init: true}})
        SET a.{_PROP_ACCESS_KEYS} = [], a.{_PROP_TAGS} = [], a.{_PROP_NAME} = ''
        MERGE (b{lbls} {{_init_b: true}})
        SET b.{_PROP_ACCESS_KEYS} = [], b.{_PROP_TAGS} = [], b.{_PROP_NAME} = ''
        MERGE (a)-[r:{_REL_TYPE}]->(b)
        SET r.{_PROP_TYPE} = ''
        WITH a, b, r
        DETACH DELETE a, b
        """
        self._cypher(cypher)
        logger.info("Initialized Neo4j tenant: %s", tenant_id)

    def _upsert_vertex(
        self,
        tenant_id: str,
        name: str,
        *,
        document_key: str | None = None,
        tags: list[str] | None = None,
        access_keys: list[int] | None = None,
    ) -> str:
        lbls = self._tenant_labels(tenant_id)
        merge_props = f"{{{_PROP_NAME}: $name"
        if document_key:
            merge_props += f", {_PROP_DOCUMENT_KEY}: $dk"
        merge_props += "}"

        cyph = (
            f"MERGE (v{lbls} {merge_props})\n"
            f"ON CREATE SET v.{_PROP_TAGS} = $tags, v.{_PROP_ACCESS_KEYS} = $access_keys\n"
            f"ON MATCH SET "
            f"v.{_PROP_TAGS} = apoc.coll.toSet(coalesce(v.{_PROP_TAGS}, []) + $tags), "
            f"v.{_PROP_ACCESS_KEYS} = apoc.coll.toSet(coalesce(v.{_PROP_ACCESS_KEYS}, []) + $access_keys)\n"
            "RETURN elementId(v) AS vid"
        )

        rec = self._cypher(cyph, name=name, dk=document_key, tags=tags or [], access_keys=access_keys or [])
        return cast("str", rec[0]["vid"])

    def insert_triplet(
        self,
        tenant_id: str,
        subj: str,
        pred: str,
        obj: str,
        *,
        document_key: str | None = None,
        subj_tags: list[str] | None = None,
        obj_tags: list[str] | None = None,
        subj_access: list[int] | None = None,
        obj_access: list[int] | None = None,
    ) -> None:
        sid = self._upsert_vertex(tenant_id, subj, document_key=document_key, tags=subj_tags, access_keys=subj_access)
        oid = self._upsert_vertex(tenant_id, obj, document_key=document_key, tags=obj_tags, access_keys=obj_access)

        cyph = (
            "MATCH (s) WHERE elementId(s) = $sid\n"
            "MATCH (o) WHERE elementId(o) = $oid\n"
            f"MERGE (s)-[r:{_REL_TYPE} {{{_PROP_TYPE}: $pred}}]->(o)\n"
        )
        if document_key:
            cyph += f"SET r.{_PROP_DOCUMENT_KEY} = $dk\n"
        cyph += "SET r.tenant_id = $tid\n"

        self._cypher(cyph, sid=sid, oid=oid, pred=pred, dk=document_key, tid=tenant_id)

    def insert_triplets(
        self,
        tenant_id: str,
        triplets: list[tuple[str, str, str]],
        *,
        document_key: str | None = None,
        tags: list[str] | None = None,
        access_keys: list[int] | None = None,
    ) -> None:
        """Batch-insert all triplets in one Cypher round-trip via UNWIND.

        Previously this iterated `insert_triplet` once per tuple, each of
        which ran three Cypher queries — 50 triplets cost ~150 round-trips.
        The UNWIND variant does it in one statement; per-chunk ingest
        latency for a document with ~10 triplets/chunk drops roughly an
        order of magnitude.
        """
        clean = [{"subj": s, "pred": p, "obj": o} for s, p, o in triplets if s and p and o]
        if not clean:
            return

        lbls = self._tenant_labels(tenant_id)
        cypher = f"""
UNWIND $triplets AS t
MERGE (s{lbls} {{{_PROP_NAME}: t.subj}})
  ON CREATE SET s.{_PROP_TAGS} = $tags,
                s.{_PROP_ACCESS_KEYS} = $access_keys
  ON MATCH  SET s.{_PROP_TAGS} = apoc.coll.toSet(coalesce(s.{_PROP_TAGS}, []) + $tags),
                s.{_PROP_ACCESS_KEYS} = apoc.coll.toSet(coalesce(s.{_PROP_ACCESS_KEYS}, []) + $access_keys)
MERGE (o{lbls} {{{_PROP_NAME}: t.obj}})
  ON CREATE SET o.{_PROP_TAGS} = $tags,
                o.{_PROP_ACCESS_KEYS} = $access_keys
  ON MATCH  SET o.{_PROP_TAGS} = apoc.coll.toSet(coalesce(o.{_PROP_TAGS}, []) + $tags),
                o.{_PROP_ACCESS_KEYS} = apoc.coll.toSet(coalesce(o.{_PROP_ACCESS_KEYS}, []) + $access_keys)
FOREACH (_ IN CASE WHEN $dk IS NOT NULL THEN [1] ELSE [] END |
    SET s.{_PROP_DOCUMENT_KEY} = $dk, o.{_PROP_DOCUMENT_KEY} = $dk
)
MERGE (s)-[r:{_REL_TYPE} {{{_PROP_TYPE}: t.pred}}]->(o)
SET r.tenant_id = $tid
FOREACH (_ IN CASE WHEN $dk IS NOT NULL THEN [1] ELSE [] END |
    SET r.{_PROP_DOCUMENT_KEY} = $dk
)
"""
        self._cypher(
            cypher,
            triplets=clean,
            tags=tags or [],
            access_keys=access_keys or [],
            dk=document_key,
            tid=tenant_id,
        )

    def _get_all_triplets(
        self,
        tenant_id: str,
        *,
        tags: list[str] | None = None,
        user_access: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        lbls = self._tenant_labels(tenant_id)
        params: dict[str, Any] = {}
        parts = [
            f"MATCH (s{lbls})-[r:{_REL_TYPE}]->(o{lbls})",
            "WITH s, r, o",
        ]

        conds: list[str] = []

        if tags:
            conds.append(f"(any(t IN $tags WHERE t IN s.{_PROP_TAGS}) OR any(t IN $tags WHERE t IN o.{_PROP_TAGS}))")
            params["tags"] = tags

        if user_access is not None:
            access_check = (
                f"((size(s.{_PROP_ACCESS_KEYS}) = 0 OR size([k IN s.{_PROP_ACCESS_KEYS} WHERE k IN $keys]) > 0) "
                f"OR (size(o.{_PROP_ACCESS_KEYS}) = 0 OR size([k IN o.{_PROP_ACCESS_KEYS} WHERE k IN $keys]) > 0))"
            )
            conds.append(access_check)
            params["keys"] = user_access

        if conds:
            parts.append(f"WHERE {' AND '.join(conds)}")

        parts.append(
            f"RETURN s.{_PROP_NAME} AS subj, r.{_PROP_TYPE} AS pred, o.{_PROP_NAME} AS obj ORDER BY subj, pred, obj"
        )
        return self._cypher("\n".join(parts), **params)

    @staticmethod
    def _search_terms(term: str) -> list[str]:
        """Split a query into lowercase content tokens to match individually.

        Callers pass a whole user question here. Matching it as one literal string
        only ever hits if a triplet field contains the entire sentence verbatim,
        which effectively never happens — so tokenize and match any content word.

        Falls back to the raw term when every token is filtered out, so short or
        stopword-only queries ("Ur", "who?") degrade to the old behaviour rather
        than silently matching nothing.
        """
        tokens = [t for t in re.split(r"[^a-z0-9]+", term.lower()) if t]
        content = [t for t in tokens if len(t) >= _MIN_TERM_LEN and t not in _STOPWORDS]
        if content:
            return list(dict.fromkeys(content))  # de-dup, keep order
        cleaned = term.strip().lower()
        return [cleaned] if cleaned else []

    def _claim_search(
        self,
        tenant_id: str,
        terms: list[str],
        *,
        tags: list[str] | None = None,
        user_access: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Search the reified claim graph written by the fact engine.

        Shape is ``(s:Entity)-[:SUBJECT]->(c:Claim)-[:OBJECT]->(o:Entity)``, with
        literal objects held on ``c.object_value`` instead of an object node —
        hence the OPTIONAL MATCH and the coalesce.

        Only ``active`` claims are returned: superseded/contradicted/retracted ones
        are retained for history and would otherwise feed the model stale facts.
        """
        label = self._tenant_label(tenant_id)
        params: dict[str, Any] = {"terms": terms, "limit": _RELATIONSHIP_SEARCH_LIMIT}
        conds = ["c.status = 'active'"]

        if tags:
            conds.append(f"any(t IN $tags WHERE t IN coalesce(c.{_PROP_TAGS}, []))")
            params["tags"] = tags

        if user_access is not None:
            conds.append(
                f"(size(coalesce(c.{_PROP_ACCESS_KEYS}, [])) = 0 "
                f"OR size([k IN coalesce(c.{_PROP_ACCESS_KEYS}, []) WHERE k IN $keys]) > 0)"
            )
            params["keys"] = user_access

        # Terms are weighted by inverse document frequency. Counting matched terms
        # equally lets words naming the corpus itself dominate: for "What does Come,
        # Follow Me teach about the Fall of Adam and Eve?", every claim about the
        # manual scores on "come"/"follow" and crowds out the claims about the Fall.
        # Weighting by rarity puts the topical terms on top.
        cypher = f"""
        MATCH (s:{_LABEL_ENTITY}:{label})-[:SUBJECT]->(c:Claim:{label})
        WHERE {" AND ".join(conds)}
        OPTIONAL MATCH (c)-[:OBJECT]->(o:{_LABEL_ENTITY}:{label})
        WITH s.canonical_name AS subj,
             c.predicate AS pred,
             coalesce(o.canonical_name, toString(c.object_value), '') AS obj
        WITH subj, pred, obj,
             toLower(coalesce(subj, '') + ' ' + coalesce(pred, '') + ' ' + obj) AS hay
        WITH collect({{subj: subj, pred: pred, obj: obj, hay: hay}}) AS rows
        WITH rows, [t IN $terms | size([r IN rows WHERE r.hay CONTAINS t])] AS df
        UNWIND rows AS r
        WITH r, reduce(sc = 0.0, i IN range(0, size($terms) - 1) |
                 sc + CASE WHEN r.hay CONTAINS $terms[i]
                           THEN 1.0 / (1.0 + log(1.0 + toFloat(df[i])))
                           ELSE 0.0 END) AS score
        WHERE score > 0
        RETURN DISTINCT r.subj AS subj, r.pred AS pred, r.obj AS obj, score
        ORDER BY score DESC, subj, pred, obj
        LIMIT $limit
        """
        return self._cypher(cypher, **params)

    def _legacy_triplet_search(
        self,
        tenant_id: str,
        terms: list[str],
        *,
        tags: list[str] | None = None,
        user_access: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Search plain ``(:Entity)-[:REL]->(:Entity)`` triplets.

        Still populated for orgs ingested before the fact engine, and by
        ``insert_triplet``, so both shapes have to be searched.
        """
        rows = [
            (t, " ".join(str(t.get(k) or "") for k in ("subj", "pred", "obj")).lower())
            for t in self._get_all_triplets(tenant_id, tags=tags, user_access=user_access)
        ]
        # Same IDF weighting as the claim path, so scores from the two shapes are
        # comparable when the caller merges them.
        weights = {t: 1.0 / (1.0 + math.log(1.0 + sum(1 for _, hay in rows if t in hay))) for t in terms}

        matched = [
            {**triplet, "score": sum(weights[t] for t in terms if t in hay)}
            for triplet, hay in rows
            if any(t in hay for t in terms)
        ]
        matched.sort(key=lambda t: (-t["score"], str(t.get("subj") or ""), str(t.get("pred") or "")))
        return matched[:_RELATIONSHIP_SEARCH_LIMIT]

    def fuzzy_relationship_search(
        self,
        tenant_id: str,
        term: str,
        *,
        tags: list[str] | None = None,
        user_access: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Find triplets related to ``term`` across both graph shapes.

        Returns ``{subj, pred, obj}`` dicts ordered by how many query terms each
        matched, so callers taking a head slice get the most relevant ones.
        """
        terms = self._search_terms(term)
        if not terms:
            return []

        results = self._claim_search(tenant_id, terms, tags=tags, user_access=user_access)
        results += self._legacy_triplet_search(tenant_id, terms, tags=tags, user_access=user_access)

        # Both shapes can describe the same fact; key on the rendered triplet.
        seen: set[tuple[str, str, str]] = set()
        merged: list[dict[str, Any]] = []
        for t in sorted(results, key=lambda r: -float(r.get("score") or 0.0)):
            key = (str(t.get("subj") or ""), str(t.get("pred") or ""), str(t.get("obj") or ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append({"subj": t.get("subj"), "pred": t.get("pred"), "obj": t.get("obj")})
        return merged[:_RELATIONSHIP_SEARCH_LIMIT]

    def fuzzy_entity_search(
        self,
        tenant_id: str,
        term: str,
        *,
        tags: list[str] | None = None,
        user_access: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        lbls = self._tenant_labels(tenant_id)
        params: dict[str, Any] = {"term": term.lower()}
        parts = [f"MATCH (e{lbls})"]
        conds: list[str] = []

        if tags:
            conds.append(f"any(t IN $tags WHERE t IN e.{_PROP_TAGS})")
            params["tags"] = tags

        if user_access is not None:
            conds.append(
                f"(size(e.{_PROP_ACCESS_KEYS}) = 0 OR size([k IN e.{_PROP_ACCESS_KEYS} WHERE k IN $keys]) > 0)"
            )
            params["keys"] = user_access

        conds.append(f"toLower(e.{_PROP_NAME}) CONTAINS $term")

        if conds:
            parts.append(f"WHERE {' AND '.join(conds)}")
        parts.append(f"RETURN e.{_PROP_NAME} AS name ORDER BY name")
        return self._cypher("\n".join(parts), **params)

    def delete_by_document_key(self, tenant_id: str, document_key: str) -> None:
        lbls = self._tenant_labels(tenant_id)
        self._cypher(
            f"MATCH (n{lbls})-[r:{_REL_TYPE}]-(m{lbls}) WHERE r.{_PROP_DOCUMENT_KEY} = $dk DETACH DELETE r",
            dk=document_key,
        )
        self._cypher(
            f"MATCH (n{lbls}) WHERE n.{_PROP_DOCUMENT_KEY} = $dk DETACH DELETE n",
            dk=document_key,
        )
        logger.info("Deleted graph data for document %s in tenant %s", document_key, tenant_id)

    def update_metadata(
        self,
        tenant_id: str,
        document_key: str,
        *,
        tags: list[str] | None = None,
        access_keys: list[int] | None = None,
    ) -> None:
        lbls = self._tenant_labels(tenant_id)
        set_clauses: list[str] = []
        params: dict[str, Any] = {"dk": document_key}

        if tags is not None:
            set_clauses.append(f"v.{_PROP_TAGS} = $tags")
            params["tags"] = tags
        if access_keys is not None:
            set_clauses.append(f"v.{_PROP_ACCESS_KEYS} = $access_keys")
            params["access_keys"] = access_keys

        if not set_clauses:
            return

        cypher = (
            f"MATCH (v{lbls}) WHERE v.{_PROP_DOCUMENT_KEY} = $dk "
            f"SET {', '.join(set_clauses)} "
            "RETURN count(v) AS updated"
        )
        result = self._cypher(cypher, **params)
        count = result[0]["updated"] if result else 0
        logger.info("Updated %d Neo4j nodes for document %s", count, document_key)

    def delete_tenant(self, tenant_id: str) -> None:
        """Detach-delete every node carrying this tenant's label.

        Uses DETACH DELETE so relationships to/from any matched node are
        removed in the same statement. The tenant label is enough on its
        own to scope — no WHERE clause needed.
        """
        lbls = self._tenant_labels(tenant_id)
        # Count first (COUNT after DETACH DELETE is unreliable across driver
        # versions), then delete.
        count_result = self._cypher(f"MATCH (n{lbls}) RETURN count(n) AS c")
        count = count_result[0]["c"] if count_result else 0
        if count:
            self._cypher(f"MATCH (n{lbls}) DETACH DELETE n")
        logger.info("Deleted %d Neo4j nodes for tenant %s", count, tenant_id)

    def close(self) -> None:
        self._driver.close()
