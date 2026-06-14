package handlers

import (
	"net/http"
	"strconv"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"
)

// Pagination defaults
const (
	DefaultPage     = 1
	DefaultPageSize = 50
	MaxPageSize     = 200
)

// PaginationParams holds pagination parameters.
type PaginationParams struct {
	Page     int
	PageSize int
}

// Offset returns the offset for SQL queries.
func (p PaginationParams) Offset() int32 {
	return int32((p.Page - 1) * p.PageSize)
}

// Limit returns the limit for SQL queries.
func (p PaginationParams) Limit() int32 {
	return int32(p.PageSize)
}

// ParsePagination extracts pagination from request query params.
func ParsePagination(r *http.Request) PaginationParams {
	page := DefaultPage
	pageSize := DefaultPageSize

	if p := r.URL.Query().Get("page"); p != "" {
		if v, err := strconv.Atoi(p); err == nil && v > 0 {
			page = v
		}
	}

	if ps := r.URL.Query().Get("page_size"); ps != "" {
		if v, err := strconv.Atoi(ps); err == nil && v > 0 && v <= MaxPageSize {
			pageSize = v
		}
	}

	return PaginationParams{Page: page, PageSize: pageSize}
}

// PaginatedResponse is a generic paginated response.
type PaginatedResponse[T any] struct {
	Items    []T   `json:"items"`
	Total    int64 `json:"total"`
	Page     int   `json:"page"`
	PageSize int   `json:"page_size"`
}

// MakePage creates a paginated response.
func MakePage[T any](items []T, total int64, params PaginationParams) PaginatedResponse[T] {
	if items == nil {
		items = []T{}
	}
	return PaginatedResponse[T]{
		Items:    items,
		Total:    total,
		Page:     params.Page,
		PageSize: params.PageSize,
	}
}

// UUID helpers

// ToPgUUID converts a google/uuid.UUID to pgtype.UUID.
func ToPgUUID(id uuid.UUID) pgtype.UUID {
	return pgtype.UUID{Bytes: id, Valid: true}
}

// FromPgUUID converts a pgtype.UUID to google/uuid.UUID.
func FromPgUUID(id pgtype.UUID) uuid.UUID {
	if !id.Valid {
		return uuid.Nil
	}
	return uuid.UUID(id.Bytes)
}

// ToTimestamptz converts a time.Time to pgtype.Timestamptz.
func ToTimestamptz(t time.Time) pgtype.Timestamptz {
	return pgtype.Timestamptz{Time: t, Valid: true}
}

// ParseUUID parses a UUID string, returning an error if invalid.
func ParseUUID(s string) (uuid.UUID, error) {
	return uuid.Parse(s)
}
