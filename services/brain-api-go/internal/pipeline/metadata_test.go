package pipeline

import "testing"

// Caller metadata rides along in every payload but must never override the fields
// retrieval filters and scopes on: {"access_keys": [0]} would make restricted
// content public. Mirrors RESERVED_INGEST_METADATA_KEYS in the Python brain-api.
func TestWithUserMetadataReservedFieldsWin(t *testing.T) {
	pipelineFields := map[string]any{
		"access_keys":  []int{42},
		"tags":         []string{"folder:f1"},
		"document_key": "dk1",
		"tenant_id":    "t1",
		"type":         "chunk",
		"text":         "real",
	}
	hostile := map[string]any{
		"access_keys":    []int{0},
		"tags":           []string{"folder:other"},
		"document_key":   "other",
		"document_id":    "other",
		"tenant_id":      "other",
		"type":           "document",
		"text":           "replaced",
		"summary":        "replaced",
		"summary_tree":   "replaced",
		"section":        "replaced",
		"chunk_order":    999,
		"document_title": "replaced",
		"source":         "kept",
	}
	got := withUserMetadata(pipelineFields, hostile)

	if ak, ok := got["access_keys"].([]int); !ok || len(ak) != 1 || ak[0] != 42 {
		t.Fatalf("access_keys overridden: %v", got["access_keys"])
	}
	if got["document_key"] != "dk1" || got["tenant_id"] != "t1" || got["type"] != "chunk" || got["text"] != "real" {
		t.Fatalf("reserved field overridden: %v", got)
	}
	for _, k := range []string{"document_id", "summary", "summary_tree", "section", "chunk_order", "document_title"} {
		if _, present := got[k]; present {
			t.Fatalf("reserved key %q leaked in from metadata", k)
		}
	}
	if got["source"] != "kept" {
		t.Fatalf("non-reserved metadata dropped: %v", got)
	}
}

func TestWithUserMetadataDoesNotMutateInputs(t *testing.T) {
	base := map[string]any{"type": "chunk"}
	meta := map[string]any{"source": "x"}
	_ = withUserMetadata(base, meta)
	if len(base) != 1 || len(meta) != 1 {
		t.Fatalf("inputs mutated: %v %v", base, meta)
	}
}

func TestReservedIngestMetadataKeysMatchPython(t *testing.T) {
	want := []string{
		"access_keys", "tenant_id", "tags", "document_key", "document_id", "document_title",
		"type", "text", "summary", "summary_tree", "section", "chunk_order",
	}
	if len(reservedIngestMetadataKeys) != len(want) {
		t.Fatalf("got %d reserved keys, want %d", len(reservedIngestMetadataKeys), len(want))
	}
	for _, k := range want {
		if _, ok := reservedIngestMetadataKeys[k]; !ok {
			t.Fatalf("missing reserved key %q", k)
		}
	}
}
