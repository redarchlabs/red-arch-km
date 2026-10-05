package pipeline

import (
	"reflect"
	"testing"
)

// End to end through IngestDocument: both the chunk and the document payloads
// must go through withUserMetadata, so caller metadata cannot rewrite
// access_keys ([0] = public), document_key, tags or tenant_id on any record.
// metadata_test.go covers withUserMetadata itself.
func TestPipeline_IngestDocument_MetadataCannotOverrideReservedFields(t *testing.T) {
	p, vector, _, _, _, _ := newMockPipeline()

	req := IngestRequest{
		TenantID:    "tenant1",
		DocumentKey: "doc1",
		Title:       "Restricted",
		Text:        "Confidential content. It has multiple sentences.",
		Tags:        []string{"folder:f1"},
		AccessKeys:  []int{42},
		Metadata: map[string]any{
			"tenant_id":      "other-tenant",
			"document_key":   "someone-elses-doc",
			"document_id":    "forged",
			"document_title": "Forged",
			"tags":           []string{"folder:escaped"},
			"access_keys":    []int{0},
			"type":           "document",
			"text":           "injected",
			"summary":        "injected",
			"chunk_order":    999,
			"author":         "Ada",
		},
	}
	if _, err := p.IngestDocument(ctx(), req); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	chunks := vector.Upserted["chunks"]
	docs := vector.Upserted["documents"]
	if len(chunks) == 0 || len(docs) != 1 {
		t.Fatalf("expected chunk records and one document record, got %d / %d", len(chunks), len(docs))
	}

	check := func(kind string, payload map[string]any, wantType string) {
		t.Helper()
		if payload["tenant_id"] != "tenant1" {
			t.Errorf("%s tenant_id overridden: %v", kind, payload["tenant_id"])
		}
		if payload["document_key"] != "doc1" {
			t.Errorf("%s document_key overridden: %v", kind, payload["document_key"])
		}
		if payload["document_title"] != "Restricted" {
			t.Errorf("%s document_title overridden: %v", kind, payload["document_title"])
		}
		if !reflect.DeepEqual(payload["tags"], []string{"folder:f1"}) {
			t.Errorf("%s tags overridden: %v", kind, payload["tags"])
		}
		if !reflect.DeepEqual(payload["access_keys"], []int{42}) {
			t.Errorf("%s access_keys overridden: %v", kind, payload["access_keys"])
		}
		if payload["type"] != wantType {
			t.Errorf("%s type overridden: %v", kind, payload["type"])
		}
		if payload["document_id"] == "forged" {
			t.Errorf("%s document_id overridden", kind)
		}
		if payload["summary"] == "injected" {
			t.Errorf("%s summary overridden", kind)
		}
		if payload["author"] != "Ada" {
			t.Errorf("%s lost non-reserved metadata: %v", kind, payload["author"])
		}
	}
	for i, rec := range chunks {
		check("chunk", rec.Payload, "chunk")
		if rec.Payload["chunk_order"] != i {
			t.Errorf("chunk_order overridden: %v", rec.Payload["chunk_order"])
		}
		if rec.Payload["text"] == "injected" {
			t.Error("chunk text overridden")
		}
	}
	check("document", docs[0].Payload, "document")
	for _, k := range []string{"text", "chunk_order"} {
		if _, ok := docs[0].Payload[k]; ok {
			t.Errorf("document payload leaked reserved metadata key %q", k)
		}
	}
	if req.Metadata["access_keys"] == nil {
		t.Error("caller metadata map was mutated")
	}
}
