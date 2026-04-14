"""Neo4j graph store implementation with tenant-scoped labels."""

from __future__ import annotations

import logging
import re
from typing import Any

from neo4j import Driver, GraphDatabase

logger = logging.getLogger(__name__)

_LABEL_ENTITY = "Entity"
_REL_TYPE = "REL"
_PROP_NAME = "name"
_PROP_DOCUMENT_KEY = "document_key"
_PROP_TAGS = "tags"
_PROP_ACCESS_KEYS = "access_keys"
_PROP_TYPE = "type"


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

    def _tenant_labels(self, tenant_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_]", "_", tenant_id)
        return f":{_LABEL_ENTITY}:Tenant_{safe}"

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
        return rec[0]["vid"]

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
        sid = self._upsert_vertex(
            tenant_id, subj, document_key=document_key, tags=subj_tags, access_keys=subj_access
        )
        oid = self._upsert_vertex(
            tenant_id, obj, document_key=document_key, tags=obj_tags, access_keys=obj_access
        )

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
        for s, p, o in triplets:
            if s and p and o:
                self.insert_triplet(
                    tenant_id, s, p, o,
                    document_key=document_key,
                    subj_tags=tags, obj_tags=tags,
                    subj_access=access_keys, obj_access=access_keys,
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
            f"RETURN s.{_PROP_NAME} AS subj, r.{_PROP_TYPE} AS pred, o.{_PROP_NAME} AS obj "
            "ORDER BY subj, pred, obj"
        )
        return self._cypher("\n".join(parts), **params)

    def fuzzy_relationship_search(
        self,
        tenant_id: str,
        term: str,
        *,
        tags: list[str] | None = None,
        user_access: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        all_triplets = self._get_all_triplets(tenant_id, tags=tags, user_access=user_access)
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        return [
            t for t in all_triplets
            if any(
                isinstance(t.get(key), str) and pattern.search(t[key])
                for key in ("subj", "obj", "pred")
            )
        ]

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
            f"MATCH (n{lbls})-[r:{_REL_TYPE}]-(m{lbls}) "
            f"WHERE r.{_PROP_DOCUMENT_KEY} = $dk DETACH DELETE r",
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

    def close(self) -> None:
        self._driver.close()
