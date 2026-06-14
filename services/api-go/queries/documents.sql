-- name: GetDocument :one
SELECT * FROM documents WHERE id = $1;

-- name: GetDocumentByKey :one
SELECT * FROM documents WHERE document_key = $1 AND org_id = $2;

-- name: ListDocuments :many
SELECT * FROM documents ORDER BY created_at DESC LIMIT $1 OFFSET $2;

-- name: ListDocumentsForOrg :many
SELECT * FROM documents WHERE org_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3;

-- name: ListDocumentsForFolders :many
-- List documents that belong to any of the given folder IDs (or have NULL folder_id)
SELECT * FROM documents
WHERE org_id = $1 AND (folder_id = ANY($2::uuid[]) OR (folder_id IS NULL AND $3 = true))
ORDER BY created_at DESC
LIMIT $4 OFFSET $5;

-- name: CountDocuments :one
SELECT COUNT(*) FROM documents;

-- name: CountDocumentsForOrg :one
SELECT COUNT(*) FROM documents WHERE org_id = $1;

-- name: CountDocumentsForFolders :one
SELECT COUNT(*) FROM documents
WHERE org_id = $1 AND (folder_id = ANY($2::uuid[]) OR (folder_id IS NULL AND $3 = true));

-- name: CreateDocument :one
INSERT INTO documents (
    id, title, description, text, document_key, document_url,
    processing_status, processing_details, metadata, use_knowledge_graph,
    org_id, folder_id, uploaded_by_id
)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
RETURNING *;

-- name: UpdateDocument :one
UPDATE documents SET
    title = COALESCE(sqlc.narg('title'), title),
    description = COALESCE(sqlc.narg('description'), description),
    text = COALESCE(sqlc.narg('text'), text),
    document_url = COALESCE(sqlc.narg('document_url'), document_url),
    processing_status = COALESCE(sqlc.narg('processing_status'), processing_status),
    processing_details = COALESCE(sqlc.narg('processing_details'), processing_details),
    metadata = COALESCE(sqlc.narg('metadata'), metadata),
    use_knowledge_graph = COALESCE(sqlc.narg('use_knowledge_graph'), use_knowledge_graph),
    folder_id = CASE
        WHEN sqlc.narg('folder_id')::uuid IS NOT NULL THEN sqlc.narg('folder_id')::uuid
        WHEN sqlc.narg('clear_folder')::boolean = true THEN NULL
        ELSE folder_id
    END,
    updated_at = NOW()
WHERE id = $1
RETURNING *;

-- name: UpdateDocumentStatus :exec
UPDATE documents SET
    processing_status = $2,
    processing_details = $3,
    updated_at = NOW()
WHERE id = $1;

-- name: DeleteDocument :exec
DELETE FROM documents WHERE id = $1;

-- name: ListDocumentsByFolder :many
SELECT * FROM documents WHERE folder_id = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3;

-- name: CountDocumentsByFolder :one
SELECT COUNT(*) FROM documents WHERE folder_id = $1;

-- name: ListDocumentsWithNullFolder :many
SELECT * FROM documents WHERE org_id = $1 AND folder_id IS NULL ORDER BY created_at DESC LIMIT $2 OFFSET $3;

-- name: CountDocumentsWithNullFolder :one
SELECT COUNT(*) FROM documents WHERE org_id = $1 AND folder_id IS NULL;

-- Document Tags

-- name: ListDocumentTags :many
SELECT t.* FROM tags t
JOIN document_tags dt ON dt.tag_id = t.id
WHERE dt.document_id = $1;

-- name: AddDocumentTag :exec
INSERT INTO document_tags (document_id, tag_id) VALUES ($1, $2) ON CONFLICT DO NOTHING;

-- name: RemoveDocumentTag :exec
DELETE FROM document_tags WHERE document_id = $1 AND tag_id = $2;

-- name: ClearDocumentTags :exec
DELETE FROM document_tags WHERE document_id = $1;

-- name: SetDocumentTags :exec
-- Clear and reset all tags for a document
DELETE FROM document_tags WHERE document_id = $1;
