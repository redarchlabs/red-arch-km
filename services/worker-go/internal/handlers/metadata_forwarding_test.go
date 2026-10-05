package handlers

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/hibiken/asynq"

	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/client"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/tasks"
)

// forwardRaw runs a raw update-metadata task payload through the handler and a
// real brain client, returning the JSON body brain-api received.
func forwardRaw(t *testing.T, raw string) map[string]json.RawMessage {
	t.Helper()
	var received map[string]json.RawMessage
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		if err := json.Unmarshal(body, &received); err != nil {
			t.Errorf("brain-api got invalid JSON: %v", err)
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"status":"updated"}`))
	}))
	defer server.Close()

	handler := NewMetadataHandler(client.NewBrainClient(server.URL, "test-key"))
	task := asynq.NewTask(tasks.TypeUpdateMetadata, []byte(raw))
	if err := handler.ProcessTask(context.Background(), task); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if received == nil {
		t.Fatal("brain-api was not called")
	}
	return received
}

// A document made public carries new_access_keys: [] — brain-api stores the
// public sentinel for it. Dropping the empty list left the document restricted.
func TestMetadataHandler_ForwardsEmptyListsAsAChange(t *testing.T) {
	got := forwardRaw(t, `{"tenant_id":"t1","document_key":"k1","new_tags":[],"new_access_keys":[]}`)

	keys, ok := got["new_access_keys"]
	if !ok {
		t.Fatal("new_access_keys was dropped; a change to public must reach brain-api")
	}
	if string(keys) != "[]" {
		t.Errorf("new_access_keys = %s, want []", keys)
	}
	tags, ok := got["new_tags"]
	if !ok {
		t.Fatal("new_tags was dropped; clearing a document's tags must reach brain-api")
	}
	if string(tags) != "[]" {
		t.Errorf("new_tags = %s, want []", tags)
	}
}

func TestMetadataHandler_OmitsAbsentFields(t *testing.T) {
	for _, raw := range []string{
		`{"tenant_id":"t1","document_key":"k1"}`,
		`{"tenant_id":"t1","document_key":"k1","new_tags":null,"new_access_keys":null}`,
	} {
		got := forwardRaw(t, raw)
		for _, field := range []string{"new_access_keys", "new_tags", "title"} {
			if v, ok := got[field]; ok {
				t.Errorf("%s: %s should be omitted (no change), got %s", raw, field, v)
			}
		}
	}
}

func TestMetadataHandler_ForwardsValues(t *testing.T) {
	got := forwardRaw(t, `{"tenant_id":"t1","document_key":"k1","title":"T","new_tags":["a"],"new_access_keys":[3,4]}`)
	if string(got["new_access_keys"]) != "[3,4]" {
		t.Errorf("new_access_keys = %s", got["new_access_keys"])
	}
	if string(got["new_tags"]) != `["a"]` {
		t.Errorf("new_tags = %s", got["new_tags"])
	}
	if string(got["title"]) != `"T"` {
		t.Errorf("title = %s", got["title"])
	}
}
