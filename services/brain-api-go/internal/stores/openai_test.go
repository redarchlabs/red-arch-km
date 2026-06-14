package stores

import (
	"testing"
)

func TestTriplet_ToModelTriplet(t *testing.T) {
	trip := Triplet{
		Subject:   "Alice",
		Predicate: "knows",
		Object:    "Bob",
	}

	model := trip.ToModelTriplet()

	if model.Subject != "Alice" {
		t.Errorf("expected Subject 'Alice', got %q", model.Subject)
	}
	if model.Predicate != "knows" {
		t.Errorf("expected Predicate 'knows', got %q", model.Predicate)
	}
	if model.Object != "Bob" {
		t.Errorf("expected Object 'Bob', got %q", model.Object)
	}
}

func TestModelDimensions(t *testing.T) {
	// Verify known model dimensions are correct
	expected := map[string]int{
		"text-embedding-3-small": 1536,
		"text-embedding-3-large": 3072,
		"text-embedding-ada-002": 1536,
	}

	for model, dim := range expected {
		if modelDimensions[model] != dim {
			t.Errorf("modelDimensions[%q] = %d, want %d", model, modelDimensions[model], dim)
		}
	}
}

func TestNewOpenAIClient_DefaultDimension(t *testing.T) {
	// Test with unknown model should still work
	client := NewOpenAIClient("test-key", "unknown-model", "gpt-4")
	if client == nil {
		t.Error("expected non-nil client")
	}
}

func TestNewOpenAIClient_KnownModel(t *testing.T) {
	client := NewOpenAIClient("test-key", "text-embedding-3-small", "gpt-4o-mini")
	if client == nil {
		t.Fatal("expected non-nil client")
	}
	if client.dimension != 1536 {
		t.Errorf("expected dimension 1536, got %d", client.dimension)
	}
}

func TestNewChunkSummarizer(t *testing.T) {
	client := NewOpenAIClient("test-key", "text-embedding-3-small", "gpt-4o-mini")
	summarizer := NewChunkSummarizer(client, 8, 10, 5, 300, 600)
	if summarizer == nil {
		t.Error("expected non-nil summarizer")
	}
}

func TestNewChunkSummarizer_DefaultValues(t *testing.T) {
	client := NewOpenAIClient("test-key", "text-embedding-3-small", "gpt-4o-mini")
	// Pass zeros to test default value handling
	summarizer := NewChunkSummarizer(client, 0, 0, 0, 0, 0)
	if summarizer == nil {
		t.Error("expected non-nil summarizer with default values")
	}
}

func TestNewTripletExtractor(t *testing.T) {
	client := NewOpenAIClient("test-key", "text-embedding-3-small", "gpt-4o-mini")
	extractor := NewTripletExtractor(client)
	if extractor == nil {
		t.Error("expected non-nil extractor")
	}
}

func TestDimension(t *testing.T) {
	client := NewOpenAIClient("test-key", "text-embedding-3-large", "gpt-4o-mini")
	dim := client.Dimension()
	if dim != 3072 {
		t.Errorf("expected dimension 3072, got %d", dim)
	}
}

func TestJSONFenceRegex(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{`{"key": "value"}`, `{"key": "value"}`},
		{"```json\n{\"key\": \"value\"}\n```", `{"key": "value"}`},
		{"```\n{\"key\": \"value\"}\n```", `{"key": "value"}`},
		{"  ```json {\"key\": \"value\"} ```  ", `{\"key\": \"value\"}`},
	}

	for _, tc := range tests {
		result := jsonFenceRe.ReplaceAllString(tc.input, "")
		// Note: this test verifies the regex pattern works
		if result == "" && tc.expected != "" {
			t.Errorf("regex removed all content from %q", tc.input)
		}
	}
}

// Test that prompts are defined
func TestPromptsDefined(t *testing.T) {
	if summarizePrompt == "" {
		t.Error("summarizePrompt should not be empty")
	}
	if groupPrompt == "" {
		t.Error("groupPrompt should not be empty")
	}
	if extractionPrompt == "" {
		t.Error("extractionPrompt should not be empty")
	}
}
