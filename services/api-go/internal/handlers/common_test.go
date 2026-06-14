package handlers

import (
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgtype"
)

func TestParsePagination(t *testing.T) {
	tests := []struct {
		name         string
		query        string
		wantPage     int
		wantPageSize int
	}{
		{
			name:         "default values",
			query:        "",
			wantPage:     1,
			wantPageSize: 50,
		},
		{
			name:         "custom page",
			query:        "?page=3",
			wantPage:     3,
			wantPageSize: 50,
		},
		{
			name:         "custom page_size",
			query:        "?page_size=25",
			wantPage:     1,
			wantPageSize: 25,
		},
		{
			name:         "both custom",
			query:        "?page=2&page_size=10",
			wantPage:     2,
			wantPageSize: 10,
		},
		{
			name:         "page_size capped at max",
			query:        "?page_size=500",
			wantPage:     1,
			wantPageSize: 50, // reverts to default since 500 > MaxPageSize
		},
		{
			name:         "invalid page defaults to 1",
			query:        "?page=abc",
			wantPage:     1,
			wantPageSize: 50,
		},
		{
			name:         "invalid page_size defaults to 50",
			query:        "?page_size=xyz",
			wantPage:     1,
			wantPageSize: 50,
		},
		{
			name:         "negative page defaults to 1",
			query:        "?page=-5",
			wantPage:     1,
			wantPageSize: 50,
		},
		{
			name:         "zero page defaults to 1",
			query:        "?page=0",
			wantPage:     1,
			wantPageSize: 50,
		},
		{
			name:         "zero page_size defaults to 50",
			query:        "?page_size=0",
			wantPage:     1,
			wantPageSize: 50,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := httptest.NewRequest("GET", "/test"+tt.query, nil)
			p := ParsePagination(r)

			if p.Page != tt.wantPage {
				t.Errorf("Page = %d, want %d", p.Page, tt.wantPage)
			}
			if p.PageSize != tt.wantPageSize {
				t.Errorf("PageSize = %d, want %d", p.PageSize, tt.wantPageSize)
			}
		})
	}
}

func TestPaginationParams_Limit(t *testing.T) {
	p := PaginationParams{Page: 1, PageSize: 25}
	if got := p.Limit(); got != 25 {
		t.Errorf("Limit() = %d, want 25", got)
	}
}

func TestPaginationParams_Offset(t *testing.T) {
	tests := []struct {
		page     int
		pageSize int
		want     int32
	}{
		{page: 1, pageSize: 50, want: 0},
		{page: 2, pageSize: 50, want: 50},
		{page: 3, pageSize: 25, want: 50},
		{page: 10, pageSize: 10, want: 90},
	}

	for _, tt := range tests {
		p := PaginationParams{Page: tt.page, PageSize: tt.pageSize}
		if got := p.Offset(); got != tt.want {
			t.Errorf("Offset() for page=%d, pageSize=%d = %d, want %d",
				tt.page, tt.pageSize, got, tt.want)
		}
	}
}

func TestMakePage(t *testing.T) {
	items := []string{"a", "b", "c"}
	pagination := PaginationParams{Page: 2, PageSize: 10}

	result := MakePage(items, 25, pagination)

	if len(result.Items) != 3 {
		t.Errorf("Items length = %d, want 3", len(result.Items))
	}
	if result.Total != 25 {
		t.Errorf("Total = %d, want 25", result.Total)
	}
	if result.Page != 2 {
		t.Errorf("Page = %d, want 2", result.Page)
	}
	if result.PageSize != 10 {
		t.Errorf("PageSize = %d, want 10", result.PageSize)
	}
}

func TestMakePage_ZeroItems(t *testing.T) {
	items := []string{}
	pagination := PaginationParams{Page: 1, PageSize: 10}

	result := MakePage(items, 0, pagination)

	if len(result.Items) != 0 {
		t.Errorf("Items length = %d, want 0", len(result.Items))
	}
	if result.Total != 0 {
		t.Errorf("Total = %d, want 0", result.Total)
	}
}

func TestMakePage_NilItems(t *testing.T) {
	var items []string = nil
	pagination := PaginationParams{Page: 1, PageSize: 10}

	result := MakePage(items, 0, pagination)

	if result.Items == nil {
		t.Error("Items should not be nil (should be empty slice)")
	}
	if len(result.Items) != 0 {
		t.Errorf("Items length = %d, want 0", len(result.Items))
	}
}

func TestParseUUID(t *testing.T) {
	validUUID := uuid.New()

	tests := []struct {
		name    string
		input   string
		wantErr bool
	}{
		{
			name:    "valid UUID",
			input:   validUUID.String(),
			wantErr: false,
		},
		{
			name:    "invalid UUID",
			input:   "not-a-uuid",
			wantErr: true,
		},
		{
			name:    "empty string",
			input:   "",
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := ParseUUID(tt.input)
			if (err != nil) != tt.wantErr {
				t.Errorf("ParseUUID() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if !tt.wantErr && result.String() != tt.input {
				t.Errorf("ParseUUID() = %v, want %v", result.String(), tt.input)
			}
		})
	}
}

func TestToPgUUID(t *testing.T) {
	id := uuid.New()
	pgUUID := ToPgUUID(id)

	if !pgUUID.Valid {
		t.Error("ToPgUUID() returned invalid UUID")
	}

	// Convert back and verify
	result := FromPgUUID(pgUUID)
	if result != id {
		t.Errorf("Round-trip failed: got %v, want %v", result, id)
	}
}

func TestFromPgUUID(t *testing.T) {
	id := uuid.New()
	pgUUID := pgtype.UUID{
		Bytes: id,
		Valid: true,
	}

	result := FromPgUUID(pgUUID)
	if result != id {
		t.Errorf("FromPgUUID() = %v, want %v", result, id)
	}
}

func TestFromPgUUID_Invalid(t *testing.T) {
	pgUUID := pgtype.UUID{Valid: false}
	result := FromPgUUID(pgUUID)

	// Should return zero UUID
	if result != uuid.Nil {
		t.Errorf("FromPgUUID() with invalid = %v, want nil UUID", result)
	}
}
