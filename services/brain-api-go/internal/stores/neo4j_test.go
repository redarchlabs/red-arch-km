package stores

import (
	"testing"
)

func TestNonAlphanumericRe(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{"abc123", "abc123"},
		{"test-tenant", "test_tenant"},
		{"org.company", "org_company"},
		{"user@email", "user_email"},
		{"tenant_id_123", "tenant_id_123"},
		{"special!@#chars", "special___chars"},
	}

	for _, tc := range tests {
		result := nonAlphanumericRe.ReplaceAllString(tc.input, "_")
		if result != tc.expected {
			t.Errorf("sanitize(%q) = %q, want %q", tc.input, result, tc.expected)
		}
	}
}

func TestTenantLabelsFormat(t *testing.T) {
	// Test the tenant label format without requiring a real Neo4j connection
	tenantID := "org-123"
	safe := nonAlphanumericRe.ReplaceAllString(tenantID, "_")
	expected := ":Entity:Tenant_org_123"

	result := ":" + labelEntity + ":Tenant_" + safe
	if result != expected {
		t.Errorf("tenantLabels(%q) = %q, want %q", tenantID, result, expected)
	}
}

func TestTenantLabels_SpecialChars(t *testing.T) {
	tenantID := "user@domain.com"
	safe := nonAlphanumericRe.ReplaceAllString(tenantID, "_")
	expected := ":Entity:Tenant_user_domain_com"

	result := ":" + labelEntity + ":Tenant_" + safe
	if result != expected {
		t.Errorf("tenantLabels(%q) = %q, want %q", tenantID, result, expected)
	}
}

func TestConstants(t *testing.T) {
	// Verify expected constants are defined
	if labelEntity != "Entity" {
		t.Errorf("labelEntity should be 'Entity', got %q", labelEntity)
	}
	if relType != "REL" {
		t.Errorf("relType should be 'REL', got %q", relType)
	}
	if propName != "name" {
		t.Errorf("propName should be 'name', got %q", propName)
	}
	if propDocKey != "document_key" {
		t.Errorf("propDocKey should be 'document_key', got %q", propDocKey)
	}
	if propTags != "tags" {
		t.Errorf("propTags should be 'tags', got %q", propTags)
	}
	if propAccessKey != "access_keys" {
		t.Errorf("propAccessKey should be 'access_keys', got %q", propAccessKey)
	}
	if propType != "type" {
		t.Errorf("propType should be 'type', got %q", propType)
	}
}
