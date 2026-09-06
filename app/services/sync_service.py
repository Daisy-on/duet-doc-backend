import hashlib
import json

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.sync import DATA_MODELS, PushRequest

TABLES = {
    "knowledge_base": "knowledge_bases",
    "group": "groups",
    "document": "documents",
    "chat_session": "chat_sessions",
    "chat_message": "chat_messages",
}

JSON_COLUMNS = {
    "chat_message": {
        "web_search_urls",
        "referenced_docs",
        "knowledge_sources",
        "ai_metadata",
    }
}


async def workspace_for_user(session, workspace_id, user_id, *, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    row = (
        (
            await session.execute(
                text("SELECT * FROM workspaces WHERE id=:id AND owner_user_id=:user" + suffix),
                {"id": workspace_id, "user": user_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Workspace not found")
    return row


async def load_entities(session, workspace_id):
    result = {}
    for kind, table in TABLES.items():
        rows = (
            (
                await session.execute(
                    text(f"SELECT * FROM {table} WHERE workspace_id=:wid"), {"wid": workspace_id}
                )
            )
            .mappings()
            .all()
        )
        result[kind] = {row["id"]: dict(row) for row in rows}
    return result


def validate_graph(entities):
    active = {
        kind: {key: row for key, row in rows.items() if not row.get("deleted_at")}
        for kind, rows in entities.items()
    }
    for kind in ("group", "document"):
        for row in active[kind].values():
            if row["kb_id"] not in active["knowledge_base"]:
                raise HTTPException(409, "Knowledge base has active dependents or is missing")
            parent_id = row["parent_group_id"] if kind == "group" else row["group_id"]
            if parent_id is not None:
                parent = active["group"].get(parent_id)
                if parent is None or parent["kb_id"] != row["kb_id"]:
                    raise HTTPException(409, "Group has active dependents or invalid scope")
            if kind == "group":
                seen = {row["id"]}
                depth = 0
                while parent_id is not None:
                    if parent_id in seen:
                        raise HTTPException(409, "Group cycle")
                    seen.add(parent_id)
                    ancestor = active["group"].get(parent_id)
                    if ancestor is None:
                        raise HTTPException(409, "Missing parent group")
                    parent_id = ancestor["parent_group_id"]
                    depth += 1
                if depth != row["depth"] or depth > 5:
                    raise HTTPException(409, "Invalid group depth")
    for row in active["chat_message"].values():
        if row["session_id"] not in active["chat_session"]:
            raise HTTPException(409, "Chat message has no active session")


async def push(session: AsyncSession, user_id, request: PushRequest):
    wid = request.workspace_id
    workspace = await workspace_for_user(session, wid, user_id, lock=True)
    digest = hashlib.sha256(
        json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    prior = (
        (
            await session.execute(
                text(
                    "SELECT request_hash, result FROM sync_mutations "
                    "WHERE workspace_id=:wid AND mutation_id=:mid"
                ),
                {"wid": wid, "mid": request.mutation_id},
            )
        )
        .mappings()
        .first()
    )
    if prior:
        if prior["request_hash"] != digest:
            raise HTTPException(409, "mutation_id reused with different content")
        return prior["result"]

    entities = await load_entities(session, wid)
    for operation in request.operations:
        current = entities[operation.entity_type].get(operation.entity_id)
        revision = current["revision"] if current else 0
        if revision != operation.base_revision:
            # The caller can pull the current snapshot without losing its local draft.
            raise HTTPException(
                409,
                {
                    "code": "REVISION_CONFLICT",
                    "entity_type": operation.entity_type,
                    "entity_id": operation.entity_id,
                    "current_revision": revision,
                },
            )
        if operation.operation == "delete":
            assert current is not None
            entities[operation.entity_type][operation.entity_id] = {
                **current,
                "deleted_at": True,
            }
            if operation.entity_type == "chat_session":
                for message in entities["chat_message"].values():
                    if message["session_id"] == operation.entity_id:
                        message["deleted_at"] = True
        else:
            assert operation.data is not None
            entities[operation.entity_type][operation.entity_id] = {
                **operation.data,
                "id": operation.entity_id,
                "deleted_at": None,
            }
    validate_graph(entities)

    sequence = workspace["sync_sequence"]
    group_end = sequence + len(request.operations)
    results = []
    # Parent tables precede child tables; group self references are deferred until commit.
    for operation in sorted(request.operations, key=lambda op: list(TABLES).index(op.entity_type)):
        table = TABLES[operation.entity_type]
        params = {"wid": wid, "id": operation.entity_id, "rev": operation.base_revision + 1}
        if operation.operation == "delete":
            await session.execute(
                text(
                    f"UPDATE {table} SET deleted_at=now(), updated_at=now(), revision=:rev "
                    "WHERE workspace_id=:wid AND id=:id"
                ),
                params,
            )
            if operation.entity_type == "chat_session":
                await session.execute(
                    text(
                        "UPDATE chat_messages SET deleted_at=now(), updated_at=now(), "
                        "revision=revision+1 WHERE workspace_id=:wid AND session_id=:id "
                        "AND deleted_at IS NULL"
                    ),
                    params,
                )
        else:
            data = DATA_MODELS[operation.entity_type].model_validate(operation.data).model_dump()
            columns = list(data)
            params.update(data)
            json_columns = JSON_COLUMNS.get(operation.entity_type, set())
            for column in json_columns:
                if params[column] is not None:
                    params[column] = json.dumps(jsonable_encoder(params[column]))
            assignments = [f"{col}=EXCLUDED.{col}" for col in columns if col != "created_at"]
            assignments.extend(["revision=EXCLUDED.revision", "deleted_at=NULL"])
            if "updated_at" not in columns:
                assignments.append("updated_at=now()")
            values = [
                f"CAST(:{column} AS jsonb)" if column in json_columns else f":{column}"
                for column in columns
            ]
            await session.execute(
                text(
                    f"INSERT INTO {table} (workspace_id,id,revision,{','.join(columns)}) "
                    f"VALUES (:wid,:id,:rev,{','.join(values)}) "
                    f"ON CONFLICT (workspace_id,id) DO UPDATE SET {','.join(assignments)}"
                ),
                params,
            )
        sequence += 1
        snapshot = (
            (
                await session.execute(
                    text(f"SELECT * FROM {table} WHERE workspace_id=:wid AND id=:id"), params
                )
            )
            .mappings()
            .one()
        )
        result = {
            "entity_type": operation.entity_type,
            "entity_id": operation.entity_id,
            "revision": params["rev"],
            "sequence": sequence,
        }
        await session.execute(
            text(
                "INSERT INTO sync_changes VALUES (:wid,:sequence,:entity_type,:entity_id,"
                ":revision,:operation,:group_end,CAST(:snapshot AS jsonb))"
            ),
            {
                "wid": wid,
                **result,
                "operation": operation.operation,
                "group_end": group_end,
                "snapshot": json.dumps(jsonable_encoder(dict(snapshot))),
            },
        )
        results.append(result)
    await session.execute(
        text("UPDATE workspaces SET sync_sequence=:seq WHERE id=:wid"),
        {"seq": sequence, "wid": wid},
    )
    response = {"mutation_id": str(request.mutation_id), "results": results}
    await session.execute(
        text(
            "INSERT INTO sync_mutations (workspace_id,mutation_id,request_hash,result) "
            "VALUES (:wid,:mid,:hash,CAST(:result AS jsonb))"
        ),
        {"wid": wid, "mid": request.mutation_id, "hash": digest, "result": json.dumps(response)},
    )
    return response


async def pull(session, user_id, workspace_id, cursor, limit):
    workspace = await workspace_for_user(session, workspace_id, user_id, lock=True)
    if cursor > workspace["sync_sequence"]:
        raise HTTPException(400, "Cursor is ahead of this workspace")
    if cursor:
        group_end = await session.scalar(
            text("SELECT group_end FROM sync_changes WHERE workspace_id=:wid AND sequence=:cursor"),
            {"wid": workspace_id, "cursor": cursor},
        )
        if group_end != cursor:
            raise HTTPException(400, "Cursor must be at a change group boundary")
    end = await session.scalar(
        text(
            "SELECT max(group_end) FROM (SELECT group_end FROM sync_changes "
            "WHERE workspace_id=:wid AND sequence>:cursor ORDER BY sequence LIMIT :limit) page"
        ),
        {"wid": workspace_id, "cursor": cursor, "limit": limit},
    )
    changes = (
        (
            await session.execute(
                text(
                    "SELECT * FROM sync_changes WHERE workspace_id=:wid AND sequence>:cursor "
                    "AND sequence<=:end ORDER BY sequence"
                ),
                {"wid": workspace_id, "cursor": cursor, "end": end or cursor},
            )
        )
        .mappings()
        .all()
    )
    results = []
    for change in changes:
        results.append(
            {
                "sequence": change["sequence"],
                "entity_type": change["entity_type"],
                "entity_id": change["entity_id"],
                "group_end": change["group_end"],
                "snapshot": change["snapshot"],
            }
        )
    next_cursor = changes[-1]["sequence"] if changes else cursor
    return {
        "changes": results,
        "next_cursor": next_cursor,
        "has_more": next_cursor < workspace["sync_sequence"],
    }
