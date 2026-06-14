package stores

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"regexp"
	"sort"
	"strings"
	"sync"

	"github.com/redarchlabs/red-arch-km-2/services/brain-api-go/internal/models"
	"github.com/sashabaranov/go-openai"
)

// Known embedding dimensions
var modelDimensions = map[string]int{
	"text-embedding-3-small": 1536,
	"text-embedding-3-large": 3072,
	"text-embedding-ada-002": 1536,
}

// OpenAIClient wraps the OpenAI API for embeddings and chat completions.
type OpenAIClient struct {
	client         *openai.Client
	embeddingModel string
	chatModel      string
	dimension      int
}

// NewOpenAIClient creates a new OpenAI client.
func NewOpenAIClient(apiKey, embeddingModel, chatModel string) *OpenAIClient {
	dim := modelDimensions[embeddingModel]
	if dim == 0 {
		dim = 1536 // fallback
	}

	return &OpenAIClient{
		client:         openai.NewClient(apiKey),
		embeddingModel: embeddingModel,
		chatModel:      chatModel,
		dimension:      dim,
	}
}

// Dimension returns the embedding vector dimension.
func (o *OpenAIClient) Dimension() int {
	return o.dimension
}

// Embed generates an embedding for a single text.
func (o *OpenAIClient) Embed(ctx context.Context, text string) ([]float32, error) {
	resp, err := o.client.CreateEmbeddings(ctx, openai.EmbeddingRequest{
		Input: []string{text},
		Model: openai.EmbeddingModel(o.embeddingModel),
	})
	if err != nil {
		return nil, fmt.Errorf("create embedding: %w", err)
	}

	if len(resp.Data) == 0 {
		return nil, fmt.Errorf("no embedding returned")
	}

	return resp.Data[0].Embedding, nil
}

// EmbedBatch generates embeddings for multiple texts.
func (o *OpenAIClient) EmbedBatch(ctx context.Context, texts []string) ([][]float32, error) {
	if len(texts) == 0 {
		return nil, nil
	}

	resp, err := o.client.CreateEmbeddings(ctx, openai.EmbeddingRequest{
		Input: texts,
		Model: openai.EmbeddingModel(o.embeddingModel),
	})
	if err != nil {
		return nil, fmt.Errorf("create embeddings batch: %w", err)
	}

	// Sort by index to maintain order
	sort.Slice(resp.Data, func(i, j int) bool {
		return resp.Data[i].Index < resp.Data[j].Index
	})

	embeddings := make([][]float32, len(resp.Data))
	for i, item := range resp.Data {
		embeddings[i] = item.Embedding
	}

	return embeddings, nil
}

// Chat performs a chat completion.
func (o *OpenAIClient) Chat(ctx context.Context, messages []openai.ChatCompletionMessage) (string, error) {
	resp, err := o.client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model:       o.chatModel,
		Messages:    messages,
		MaxTokens:   1000,
		Temperature: 0.3,
	})
	if err != nil {
		return "", fmt.Errorf("chat completion: %w", err)
	}

	if len(resp.Choices) == 0 {
		return "", fmt.Errorf("no choices returned")
	}

	return resp.Choices[0].Message.Content, nil
}

// ChunkSummarizer summarizes text chunks using OpenAI.
type ChunkSummarizer struct {
	client     *OpenAIClient
	maxWorkers int
	groupSize  int
	maxDepth   int
	chunkMax   int
	groupMax   int
}

const (
	summarizePrompt = "Summarize the following text concisely, preserving key facts and entities. Output only the summary, no preamble."
	groupPrompt     = "You are given a list of summaries from one document. Write one concise summary that captures the combined themes, key facts, and important entities. Output only the summary."
)

// NewChunkSummarizer creates a new chunk summarizer.
func NewChunkSummarizer(client *OpenAIClient, maxWorkers, groupSize, maxDepth, chunkMax, groupMax int) *ChunkSummarizer {
	if maxWorkers <= 0 {
		maxWorkers = 8
	}
	if groupSize <= 0 {
		groupSize = 10
	}
	if maxDepth <= 0 {
		maxDepth = 5
	}
	if chunkMax <= 0 {
		chunkMax = 300
	}
	if groupMax <= 0 {
		groupMax = 600
	}

	return &ChunkSummarizer{
		client:     client,
		maxWorkers: maxWorkers,
		groupSize:  groupSize,
		maxDepth:   maxDepth,
		chunkMax:   chunkMax,
		groupMax:   groupMax,
	}
}

// SummarizeChunk summarizes a single text chunk.
func (s *ChunkSummarizer) SummarizeChunk(ctx context.Context, text string) (string, error) {
	resp, err := s.client.client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: s.client.chatModel,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleSystem, Content: summarizePrompt},
			{Role: openai.ChatMessageRoleUser, Content: text},
		},
		MaxTokens:   s.chunkMax,
		Temperature: 0.3,
	})
	if err != nil {
		return "", err
	}

	if len(resp.Choices) == 0 {
		return "", fmt.Errorf("no choices returned")
	}

	return resp.Choices[0].Message.Content, nil
}

// SummarizeChunks summarizes multiple chunks in parallel.
func (s *ChunkSummarizer) SummarizeChunks(ctx context.Context, chunks []string) []string {
	if len(chunks) == 0 {
		return nil
	}

	results := make([]string, len(chunks))
	var wg sync.WaitGroup
	sem := make(chan struct{}, s.maxWorkers)

	for i, chunk := range chunks {
		wg.Add(1)
		go func(idx int, text string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()

			summary, err := s.SummarizeChunk(ctx, text)
			if err != nil {
				slog.Warn("chunk summary failed", "len", len(text), "error", err)
				// Fall back to truncated chunk
				if len(text) > 1000 {
					summary = text[:1000]
				} else {
					summary = text
				}
			}
			results[idx] = summary
		}(i, chunk)
	}

	wg.Wait()
	return results
}

// SummarizeDocument creates a hierarchical summary from chunk summaries.
func (s *ChunkSummarizer) SummarizeDocument(ctx context.Context, chunkSummaries []string) string {
	if len(chunkSummaries) == 0 {
		return ""
	}
	if len(chunkSummaries) == 1 {
		return chunkSummaries[0]
	}

	level := chunkSummaries
	for depth := 0; depth < s.maxDepth; depth++ {
		var groups [][]string
		for i := 0; i < len(level); i += s.groupSize {
			end := i + s.groupSize
			if end > len(level) {
				end = len(level)
			}
			groups = append(groups, level[i:end])
		}

		slog.Debug("hierarchical summary", "depth", depth, "items", len(level), "groups", len(groups))

		// Summarize groups in parallel
		results := make([]string, len(groups))
		var wg sync.WaitGroup
		sem := make(chan struct{}, s.maxWorkers)

		for i, group := range groups {
			wg.Add(1)
			go func(idx int, summaries []string) {
				defer wg.Done()
				sem <- struct{}{}
				defer func() { <-sem }()

				results[idx] = s.summarizeGroup(ctx, summaries)
			}(i, group)
		}

		wg.Wait()
		level = results

		if len(level) <= 1 {
			break
		}
	}

	if len(level) > 0 {
		return level[0]
	}
	return ""
}

func (s *ChunkSummarizer) summarizeGroup(ctx context.Context, summaries []string) string {
	if len(summaries) == 1 {
		return summaries[0]
	}

	// Filter empty summaries
	var nonEmpty []string
	for _, sum := range summaries {
		if strings.TrimSpace(sum) != "" {
			nonEmpty = append(nonEmpty, sum)
		}
	}

	if len(nonEmpty) == 0 {
		slog.Warn("summarize group: all items empty", "count", len(summaries))
		return ""
	}
	if len(nonEmpty) == 1 {
		return nonEmpty[0]
	}

	var combined strings.Builder
	for _, s := range nonEmpty {
		combined.WriteString("- ")
		combined.WriteString(s)
		combined.WriteString("\n\n")
	}

	resp, err := s.client.client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: s.client.chatModel,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleSystem, Content: groupPrompt},
			{Role: openai.ChatMessageRoleUser, Content: combined.String()},
		},
		MaxTokens:   s.groupMax,
		Temperature: 0.3,
	})
	if err != nil {
		slog.Error("group summary failed", "items", len(summaries), "error", err)
		// Return longest summary as fallback
		longest := nonEmpty[0]
		for _, s := range nonEmpty[1:] {
			if len(s) > len(longest) {
				longest = s
			}
		}
		return longest
	}

	if len(resp.Choices) == 0 {
		return nonEmpty[0]
	}

	return resp.Choices[0].Message.Content
}

// TripletExtractor extracts subject-predicate-object triplets from text.
type TripletExtractor struct {
	client *OpenAIClient
}

const extractionPrompt = `Extract knowledge graph triplets from the following text.
Return a JSON array of objects with keys "subject", "predicate", "object".
Each should be a short, normalized phrase. Return only valid JSON, no markdown fences.
If no triplets can be extracted, return an empty array [].`

var jsonFenceRe = regexp.MustCompile("(?s)^```(?:json)?\\s*|\\s*```$")

// NewTripletExtractor creates a new triplet extractor.
func NewTripletExtractor(client *OpenAIClient) *TripletExtractor {
	return &TripletExtractor{client: client}
}

// Extract extracts triplets from text.
func (e *TripletExtractor) Extract(ctx context.Context, text string) ([]Triplet, error) {
	resp, err := e.client.client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
		Model: e.client.chatModel,
		Messages: []openai.ChatCompletionMessage{
			{Role: openai.ChatMessageRoleSystem, Content: extractionPrompt},
			{Role: openai.ChatMessageRoleUser, Content: text},
		},
		MaxTokens:   1000,
		Temperature: 0.1,
	})
	if err != nil {
		return nil, fmt.Errorf("triplet extraction: %w", err)
	}

	if len(resp.Choices) == 0 {
		return nil, nil
	}

	raw := resp.Choices[0].Message.Content
	cleaned := strings.TrimSpace(jsonFenceRe.ReplaceAllString(raw, ""))

	var data []map[string]string
	if err := json.Unmarshal([]byte(cleaned), &data); err != nil {
		slog.Warn("failed to parse triplet JSON", "raw", cleaned[:min(200, len(cleaned))])
		return nil, nil
	}

	var triplets []Triplet
	for _, item := range data {
		s := strings.TrimSpace(item["subject"])
		p := strings.TrimSpace(item["predicate"])
		o := strings.TrimSpace(item["object"])
		if s != "" && p != "" && o != "" {
			triplets = append(triplets, Triplet{Subject: s, Predicate: p, Object: o})
		}
	}

	return triplets, nil
}

// Triplet represents a knowledge graph relationship (local to this package).
type Triplet struct {
	Subject   string
	Predicate string
	Object    string
}

// ToModelTriplet converts to the models.Triplet type.
func (t Triplet) ToModelTriplet() models.Triplet {
	return models.Triplet{
		Subject:   t.Subject,
		Predicate: t.Predicate,
		Object:    t.Object,
	}
}
