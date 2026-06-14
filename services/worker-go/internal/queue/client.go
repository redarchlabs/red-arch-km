// Package queue provides Redis-based task queue client and server using asynq.
package queue

import (
	"fmt"
	"time"

	"github.com/hibiken/asynq"

	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/config"
	"github.com/redarchlabs/red-arch-km-2/services/worker-go/internal/tasks"
)

// Client wraps asynq.Client for task enqueueing.
type Client struct {
	client *asynq.Client
	cfg    config.RetryPolicy
}

// NewClient creates a new queue client.
func NewClient(redisURL string, retryPolicy config.RetryPolicy) (*Client, error) {
	opt, err := asynq.ParseRedisURI(redisURL)
	if err != nil {
		return nil, fmt.Errorf("parse redis URL: %w", err)
	}

	return &Client{
		client: asynq.NewClient(opt),
		cfg:    retryPolicy,
	}, nil
}

// Close closes the client connection.
func (c *Client) Close() error {
	return c.client.Close()
}

// EnqueueIngest enqueues a document ingestion task.
func (c *Client) EnqueueIngest(payload tasks.IngestPayload) (*asynq.TaskInfo, error) {
	task, err := tasks.NewIngestTask(payload)
	if err != nil {
		return nil, fmt.Errorf("create task: %w", err)
	}

	return c.client.Enqueue(task,
		asynq.MaxRetry(c.cfg.MaxRetries),
		asynq.Timeout(5*time.Minute),
		asynq.Queue("default"),
	)
}

// EnqueueRemove enqueues a document removal task.
func (c *Client) EnqueueRemove(payload tasks.RemovePayload) (*asynq.TaskInfo, error) {
	task, err := tasks.NewRemoveTask(payload)
	if err != nil {
		return nil, fmt.Errorf("create task: %w", err)
	}

	return c.client.Enqueue(task,
		asynq.MaxRetry(c.cfg.MaxRetries),
		asynq.Timeout(1*time.Minute),
		asynq.Queue("default"),
	)
}

// EnqueueUpdateMetadata enqueues a metadata update task.
func (c *Client) EnqueueUpdateMetadata(payload tasks.UpdateMetadataPayload) (*asynq.TaskInfo, error) {
	task, err := tasks.NewUpdateMetadataTask(payload)
	if err != nil {
		return nil, fmt.Errorf("create task: %w", err)
	}

	return c.client.Enqueue(task,
		asynq.MaxRetry(c.cfg.MaxRetries),
		asynq.Timeout(1*time.Minute),
		asynq.Queue("default"),
	)
}
