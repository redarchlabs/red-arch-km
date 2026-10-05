package pipeline

// reservedIngestMetadataKeys are the payload fields retrieval filters, scopes or
// cites on. Caller metadata can never set them: {"access_keys": [0]} would make
// restricted content public, and document_key / tenant_id / tags / type would
// re-scope it. Keep in sync with RESERVED_INGEST_METADATA_KEYS in
// services/brain_api/src/brain_api/services/ingest_service.py and the API's
// services/api/src/api/schemas/reserved_metadata.py — the API's
// test_reserved_metadata_parity.py parses this map literal and fails on drift.
var reservedIngestMetadataKeys = map[string]struct{}{
	"access_keys":    {},
	"tenant_id":      {},
	"tags":           {},
	"document_key":   {},
	"document_id":    {},
	"document_title": {},
	"type":           {},
	"text":           {},
	"summary":        {},
	"summary_tree":   {},
	"section":        {},
	"chunk_order":    {},
}

// withUserMetadata returns a new payload: caller metadata minus reserved keys,
// then the pipeline's own fields written last so they always win. Neither input
// is modified.
func withUserMetadata(pipelineFields, metadata map[string]any) map[string]any {
	out := make(map[string]any, len(pipelineFields)+len(metadata))
	for k, v := range metadata {
		if _, reserved := reservedIngestMetadataKeys[k]; reserved {
			continue
		}
		out[k] = v
	}
	for k, v := range pipelineFields {
		out[k] = v
	}
	return out
}
