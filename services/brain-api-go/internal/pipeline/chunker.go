// Package pipeline implements the document ingestion pipeline.
package pipeline

import (
	"regexp"
	"strings"
	"unicode"
)

// Sentence boundary detection patterns
var (
	// Match sentence-ending punctuation followed by space or end
	sentenceEndRe = regexp.MustCompile(`[.!?]+\s+`)
)

// ChunkText splits text into chunks with specified size and overlap.
// Size and overlap are in approximate words (simpler than tokens for Go).
func ChunkText(text string, chunkSizeWords, overlapWords int) []string {
	if text == "" {
		return nil
	}
	if chunkSizeWords <= overlapWords {
		chunkSizeWords = overlapWords + 1
	}

	sentences := splitSentences(text)
	if len(sentences) == 0 {
		return nil
	}

	// Calculate word counts for each sentence
	wordCounts := make([]int, len(sentences))
	for i, s := range sentences {
		wordCounts[i] = countWords(s)
	}

	var chunks []string
	var currentChunk []string
	currentWords := 0
	i := 0

	for i < len(sentences) {
		sentence := sentences[i]
		words := wordCounts[i]

		// If a single sentence exceeds chunk size, include it as its own chunk
		if words > chunkSizeWords {
			if len(currentChunk) > 0 {
				chunks = append(chunks, strings.Join(currentChunk, " "))
				currentChunk = nil
				currentWords = 0
			}
			chunks = append(chunks, sentence)
			i++
			continue
		}

		// Check if adding this sentence would exceed limit
		if currentWords+words <= chunkSizeWords {
			currentChunk = append(currentChunk, sentence)
			currentWords += words
			i++
		} else {
			// Emit current chunk
			chunks = append(chunks, strings.Join(currentChunk, " "))

			// Build overlap from tail of current chunk
			overlapChunk := buildOverlap(currentChunk, wordCounts[i-len(currentChunk):i], overlapWords)

			currentChunk = overlapChunk
			currentWords = countWordsInSlice(overlapChunk)
		}
	}

	// Emit remaining chunk
	if len(currentChunk) > 0 {
		chunks = append(chunks, strings.Join(currentChunk, " "))
	}

	return chunks
}

func splitSentences(text string) []string {
	// Normalize whitespace
	text = strings.TrimSpace(text)
	if text == "" {
		return nil
	}

	// Split on sentence boundaries
	parts := sentenceEndRe.Split(text, -1)
	delimiters := sentenceEndRe.FindAllString(text, -1)

	var sentences []string
	for i, part := range parts {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		// Re-attach delimiter
		if i < len(delimiters) {
			part += strings.TrimSuffix(delimiters[i], " ")
		}
		sentences = append(sentences, part)
	}

	// If no sentence boundaries found, split by newlines
	if len(sentences) <= 1 && strings.Contains(text, "\n") {
		lines := strings.Split(text, "\n")
		sentences = nil
		for _, line := range lines {
			line = strings.TrimSpace(line)
			if line != "" {
				sentences = append(sentences, line)
			}
		}
	}

	// If still just one chunk, return it
	if len(sentences) == 0 {
		sentences = []string{text}
	}

	return sentences
}

func countWords(s string) int {
	count := 0
	inWord := false
	for _, r := range s {
		if unicode.IsSpace(r) {
			inWord = false
		} else if !inWord {
			inWord = true
			count++
		}
	}
	return count
}

func countWordsInSlice(sentences []string) int {
	total := 0
	for _, s := range sentences {
		total += countWords(s)
	}
	return total
}

func buildOverlap(chunk []string, wordCounts []int, overlapWords int) []string {
	if len(chunk) == 0 || overlapWords <= 0 {
		return nil
	}

	var overlap []string
	overlapCount := 0

	// Start from the end and work backwards
	for i := len(chunk) - 1; i >= 0 && overlapCount < overlapWords; i-- {
		idx := i
		if idx < len(wordCounts) {
			if overlapCount+wordCounts[idx] > overlapWords && len(overlap) > 0 {
				break
			}
			overlap = append([]string{chunk[i]}, overlap...)
			overlapCount += wordCounts[idx]
		}
	}

	return overlap
}
