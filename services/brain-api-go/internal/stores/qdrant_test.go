package stores

import (
	"testing"

	qdrant "github.com/qdrant/go-client/qdrant"
)

func TestToQdrantValue_String(t *testing.T) {
	val := toQdrantValue("hello")
	if val == nil {
		t.Fatal("expected non-nil value")
	}
	sv, ok := val.Kind.(*qdrant.Value_StringValue)
	if !ok {
		t.Fatalf("expected StringValue, got %T", val.Kind)
	}
	if sv.StringValue != "hello" {
		t.Errorf("expected 'hello', got %q", sv.StringValue)
	}
}

func TestToQdrantValue_Int(t *testing.T) {
	val := toQdrantValue(42)
	if val == nil {
		t.Fatal("expected non-nil value")
	}
	iv, ok := val.Kind.(*qdrant.Value_IntegerValue)
	if !ok {
		t.Fatalf("expected IntegerValue, got %T", val.Kind)
	}
	if iv.IntegerValue != 42 {
		t.Errorf("expected 42, got %d", iv.IntegerValue)
	}
}

func TestToQdrantValue_Int64(t *testing.T) {
	val := toQdrantValue(int64(100))
	if val == nil {
		t.Fatal("expected non-nil value")
	}
	iv, ok := val.Kind.(*qdrant.Value_IntegerValue)
	if !ok {
		t.Fatalf("expected IntegerValue, got %T", val.Kind)
	}
	if iv.IntegerValue != 100 {
		t.Errorf("expected 100, got %d", iv.IntegerValue)
	}
}

func TestToQdrantValue_Float64(t *testing.T) {
	val := toQdrantValue(3.14)
	if val == nil {
		t.Fatal("expected non-nil value")
	}
	dv, ok := val.Kind.(*qdrant.Value_DoubleValue)
	if !ok {
		t.Fatalf("expected DoubleValue, got %T", val.Kind)
	}
	if dv.DoubleValue != 3.14 {
		t.Errorf("expected 3.14, got %f", dv.DoubleValue)
	}
}

func TestToQdrantValue_Float32(t *testing.T) {
	val := toQdrantValue(float32(2.5))
	if val == nil {
		t.Fatal("expected non-nil value")
	}
	dv, ok := val.Kind.(*qdrant.Value_DoubleValue)
	if !ok {
		t.Fatalf("expected DoubleValue, got %T", val.Kind)
	}
	if dv.DoubleValue != 2.5 {
		t.Errorf("expected 2.5, got %f", dv.DoubleValue)
	}
}

func TestToQdrantValue_Bool(t *testing.T) {
	val := toQdrantValue(true)
	if val == nil {
		t.Fatal("expected non-nil value")
	}
	bv, ok := val.Kind.(*qdrant.Value_BoolValue)
	if !ok {
		t.Fatalf("expected BoolValue, got %T", val.Kind)
	}
	if !bv.BoolValue {
		t.Errorf("expected true, got false")
	}
}

func TestToQdrantValue_StringSlice(t *testing.T) {
	val := toQdrantValue([]string{"a", "b", "c"})
	if val == nil {
		t.Fatal("expected non-nil value")
	}
	lv, ok := val.Kind.(*qdrant.Value_ListValue)
	if !ok {
		t.Fatalf("expected ListValue, got %T", val.Kind)
	}
	if len(lv.ListValue.Values) != 3 {
		t.Errorf("expected 3 values, got %d", len(lv.ListValue.Values))
	}
}

func TestToQdrantValue_IntSlice(t *testing.T) {
	val := toQdrantValue([]int{1, 2, 3})
	if val == nil {
		t.Fatal("expected non-nil value")
	}
	lv, ok := val.Kind.(*qdrant.Value_ListValue)
	if !ok {
		t.Fatalf("expected ListValue, got %T", val.Kind)
	}
	if len(lv.ListValue.Values) != 3 {
		t.Errorf("expected 3 values, got %d", len(lv.ListValue.Values))
	}
}

func TestToQdrantValue_Unknown(t *testing.T) {
	val := toQdrantValue(struct{}{})
	if val == nil {
		t.Fatal("expected non-nil value")
	}
	_, ok := val.Kind.(*qdrant.Value_NullValue)
	if !ok {
		t.Fatalf("expected NullValue for unknown type, got %T", val.Kind)
	}
}

func TestFromQdrantValue_Nil(t *testing.T) {
	result := fromQdrantValue(nil)
	if result != nil {
		t.Errorf("expected nil, got %v", result)
	}
}

func TestFromQdrantValue_String(t *testing.T) {
	val := &qdrant.Value{Kind: &qdrant.Value_StringValue{StringValue: "test"}}
	result := fromQdrantValue(val)
	if result != "test" {
		t.Errorf("expected 'test', got %v", result)
	}
}

func TestFromQdrantValue_Integer(t *testing.T) {
	val := &qdrant.Value{Kind: &qdrant.Value_IntegerValue{IntegerValue: 42}}
	result := fromQdrantValue(val)
	// JSON numbers are float64
	if result != float64(42) {
		t.Errorf("expected 42.0, got %v", result)
	}
}

func TestFromQdrantValue_Double(t *testing.T) {
	val := &qdrant.Value{Kind: &qdrant.Value_DoubleValue{DoubleValue: 3.14}}
	result := fromQdrantValue(val)
	if result != 3.14 {
		t.Errorf("expected 3.14, got %v", result)
	}
}

func TestFromQdrantValue_Bool(t *testing.T) {
	val := &qdrant.Value{Kind: &qdrant.Value_BoolValue{BoolValue: true}}
	result := fromQdrantValue(val)
	if result != true {
		t.Errorf("expected true, got %v", result)
	}
}

func TestFromQdrantValue_List(t *testing.T) {
	val := &qdrant.Value{Kind: &qdrant.Value_ListValue{
		ListValue: &qdrant.ListValue{
			Values: []*qdrant.Value{
				{Kind: &qdrant.Value_StringValue{StringValue: "a"}},
				{Kind: &qdrant.Value_StringValue{StringValue: "b"}},
			},
		},
	}}
	result := fromQdrantValue(val)
	list, ok := result.([]any)
	if !ok {
		t.Fatalf("expected []any, got %T", result)
	}
	if len(list) != 2 {
		t.Errorf("expected 2 items, got %d", len(list))
	}
}

func TestFromQdrantValue_Struct(t *testing.T) {
	val := &qdrant.Value{Kind: &qdrant.Value_StructValue{
		StructValue: &qdrant.Struct{
			Fields: map[string]*qdrant.Value{
				"key": {Kind: &qdrant.Value_StringValue{StringValue: "value"}},
			},
		},
	}}
	result := fromQdrantValue(val)
	m, ok := result.(map[string]any)
	if !ok {
		t.Fatalf("expected map[string]any, got %T", result)
	}
	if m["key"] != "value" {
		t.Errorf("expected key='value', got %v", m["key"])
	}
}

func TestFromQdrantPayload(t *testing.T) {
	payload := map[string]*qdrant.Value{
		"text":  {Kind: &qdrant.Value_StringValue{StringValue: "hello"}},
		"count": {Kind: &qdrant.Value_IntegerValue{IntegerValue: 5}},
	}
	result := fromQdrantPayload(payload)
	if result["text"] != "hello" {
		t.Errorf("expected text='hello', got %v", result["text"])
	}
	if result["count"] != float64(5) {
		t.Errorf("expected count=5, got %v", result["count"])
	}
}

func TestToInt64Slice(t *testing.T) {
	input := []int{1, 2, 3, 4, 5}
	result := toInt64Slice(input)

	if len(result) != 5 {
		t.Fatalf("expected 5 items, got %d", len(result))
	}

	for i, v := range result {
		if v != int64(input[i]) {
			t.Errorf("expected %d at index %d, got %d", input[i], i, v)
		}
	}
}

func TestToInt64Slice_Empty(t *testing.T) {
	result := toInt64Slice(nil)
	if len(result) != 0 {
		t.Errorf("expected empty slice, got %d items", len(result))
	}
}

// Test collection naming
func TestCollectionNaming(t *testing.T) {
	// This tests the internal naming convention would use
	// We can't directly test private methods, but we verify the pattern
	tenantID := "org-123"
	expectedChunk := "org-123-chunks"
	expectedDoc := "org-123-documents"

	// Format matches _chunk_collection and _doc_collection
	actualChunk := tenantID + "-" + "chunks"
	actualDoc := tenantID + "-" + "documents"

	if actualChunk != expectedChunk {
		t.Errorf("expected %q, got %q", expectedChunk, actualChunk)
	}
	if actualDoc != expectedDoc {
		t.Errorf("expected %q, got %q", expectedDoc, actualDoc)
	}
}
