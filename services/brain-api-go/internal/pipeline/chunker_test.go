package pipeline

import (
	"strings"
	"testing"
)

func TestChunkText_EmptyInput(t *testing.T) {
	result := ChunkText("", 100, 20)
	if result != nil {
		t.Errorf("expected nil for empty input, got %v", result)
	}
}

func TestChunkText_SingleSentence(t *testing.T) {
	text := "This is a short sentence."
	result := ChunkText(text, 100, 20)
	if len(result) != 1 {
		t.Fatalf("expected 1 chunk, got %d", len(result))
	}
	if result[0] != text {
		t.Errorf("expected %q, got %q", text, result[0])
	}
}

func TestChunkText_MultipleSentences(t *testing.T) {
	text := "First sentence. Second sentence. Third sentence. Fourth sentence."
	result := ChunkText(text, 5, 2)

	if len(result) == 0 {
		t.Fatal("expected at least one chunk")
	}

	// Verify all sentences appear in output
	combined := strings.Join(result, " ")
	for _, s := range []string{"First", "Second", "Third", "Fourth"} {
		if !strings.Contains(combined, s) {
			t.Errorf("expected %q to appear in chunks", s)
		}
	}
}

func TestChunkText_LargeSingleSentence(t *testing.T) {
	// Build a very long sentence
	words := make([]string, 50)
	for i := range words {
		words[i] = "word"
	}
	text := strings.Join(words, " ") + "."

	result := ChunkText(text, 10, 2)
	if len(result) == 0 {
		t.Fatal("expected at least one chunk")
	}

	// A sentence exceeding chunk size should still appear
	if !strings.Contains(result[0], "word") {
		t.Errorf("expected chunk to contain 'word'")
	}
}

func TestChunkText_OverlapSmallerThanChunkSize(t *testing.T) {
	// If chunkSize <= overlap, chunkSize should be adjusted
	text := "One. Two. Three."
	result := ChunkText(text, 2, 5) // overlap > chunkSize
	if result == nil {
		t.Fatal("expected non-nil result even with odd parameters")
	}
}

func TestChunkText_NewlineSeparation(t *testing.T) {
	text := "Line one\nLine two\nLine three"
	result := ChunkText(text, 100, 20)

	if len(result) == 0 {
		t.Fatal("expected at least one chunk")
	}

	combined := strings.Join(result, " ")
	for _, s := range []string{"Line one", "Line two", "Line three"} {
		if !strings.Contains(combined, s) {
			t.Errorf("expected %q to appear in chunks", s)
		}
	}
}

func TestCountWords(t *testing.T) {
	tests := []struct {
		input    string
		expected int
	}{
		{"", 0},
		{"one", 1},
		{"one two", 2},
		{"one  two   three", 3},
		{"  spaced  ", 1},
		{"multiple words in a sentence", 5},
	}

	for _, tc := range tests {
		result := countWords(tc.input)
		if result != tc.expected {
			t.Errorf("countWords(%q) = %d, want %d", tc.input, result, tc.expected)
		}
	}
}

func TestSplitSentences(t *testing.T) {
	tests := []struct {
		input    string
		minCount int
	}{
		{"Single sentence.", 1},
		{"First. Second. Third.", 3},
		{"Question? Answer!", 2},
		{"No ending punctuation", 1},
		{"", 0},
		{"Line one\nLine two", 2},
	}

	for _, tc := range tests {
		result := splitSentences(tc.input)
		if len(result) < tc.minCount {
			t.Errorf("splitSentences(%q) = %d sentences, want at least %d", tc.input, len(result), tc.minCount)
		}
	}
}

func TestCountWordsInSlice(t *testing.T) {
	sentences := []string{"one two", "three four five"}
	result := countWordsInSlice(sentences)
	if result != 5 {
		t.Errorf("countWordsInSlice returned %d, want 5", result)
	}
}

func TestBuildOverlap_Empty(t *testing.T) {
	result := buildOverlap(nil, nil, 5)
	if result != nil {
		t.Errorf("expected nil for empty input, got %v", result)
	}
}

func TestBuildOverlap_ZeroOverlap(t *testing.T) {
	result := buildOverlap([]string{"a", "b"}, []int{1, 1}, 0)
	if result != nil {
		t.Errorf("expected nil for zero overlap, got %v", result)
	}
}

func TestBuildOverlap_Normal(t *testing.T) {
	chunk := []string{"First sentence.", "Second sentence.", "Third sentence."}
	wordCounts := []int{2, 2, 2}
	result := buildOverlap(chunk, wordCounts, 3)

	if len(result) == 0 {
		t.Fatal("expected non-empty overlap")
	}
	// Should contain at least part of the tail
	if !strings.Contains(strings.Join(result, " "), "sentence") {
		t.Errorf("expected overlap to contain 'sentence'")
	}
}
