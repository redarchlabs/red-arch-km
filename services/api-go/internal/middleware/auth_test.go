package middleware

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/lestrrat-go/jwx/v2/jwa"
	"github.com/lestrrat-go/jwx/v2/jwk"
	"github.com/lestrrat-go/jwx/v2/jwt"
)

func TestExtractBearerToken(t *testing.T) {
	tests := []struct {
		name     string
		header   string
		expected string
	}{
		{
			name:     "valid bearer token",
			header:   "Bearer token123",
			expected: "token123",
		},
		{
			name:     "lowercase bearer",
			header:   "bearer token456",
			expected: "token456",
		},
		{
			name:     "no header",
			header:   "",
			expected: "",
		},
		{
			name:     "no bearer prefix",
			header:   "token789",
			expected: "",
		},
		{
			name:     "basic auth",
			header:   "Basic abc123",
			expected: "",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			r := httptest.NewRequest("GET", "/", nil)
			if tt.header != "" {
				r.Header.Set("Authorization", tt.header)
			}
			got := extractBearerToken(r)
			if got != tt.expected {
				t.Errorf("extractBearerToken() = %q, want %q", got, tt.expected)
			}
		})
	}
}

func TestGetUserClaims(t *testing.T) {
	// Test with no claims
	ctx := context.Background()
	_, ok := GetUserClaims(ctx)
	if ok {
		t.Error("expected no claims in empty context")
	}

	// Test with claims
	claims := UserClaims{
		Sub:               "user-123",
		Email:             "test@example.com",
		PreferredUsername: "testuser",
	}
	ctx = context.WithValue(ctx, userClaimsKey, claims)
	got, ok := GetUserClaims(ctx)
	if !ok {
		t.Fatal("expected claims in context")
	}
	if got.Sub != "user-123" {
		t.Errorf("Sub = %q, want %q", got.Sub, "user-123")
	}
	if got.Email != "test@example.com" {
		t.Errorf("Email = %q, want %q", got.Email, "test@example.com")
	}
}

func TestJWTMiddleware_Handler_MissingToken(t *testing.T) {
	mw := NewJWTMiddleware(JWTConfig{
		KeycloakURL: "http://keycloak:8080",
		Realm:       "test",
		ClientID:    "test-client",
	})

	handler := mw.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("handler should not be called")
	}))

	r := httptest.NewRequest("GET", "/", nil)
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, r)

	if w.Code != http.StatusUnauthorized {
		t.Errorf("status = %d, want %d", w.Code, http.StatusUnauthorized)
	}
}

func TestJWTMiddleware_Handler_WithMockJWKS(t *testing.T) {
	// Generate a test RSA key pair
	privateKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("failed to generate RSA key: %v", err)
	}

	// Create a JWK from the public key
	pubJWK, err := jwk.FromRaw(privateKey.PublicKey)
	if err != nil {
		t.Fatalf("failed to create JWK: %v", err)
	}
	pubJWK.Set(jwk.KeyIDKey, "test-key-id")
	pubJWK.Set(jwk.AlgorithmKey, jwa.RS256)
	pubJWK.Set(jwk.KeyUsageKey, "sig")

	// Create a JWKS
	jwks := jwk.NewSet()
	jwks.AddKey(pubJWK)

	// Start a mock JWKS server
	jwksServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(jwks)
	}))
	defer jwksServer.Close()

	// Create a valid JWT
	token := jwt.New()
	token.Set(jwt.SubjectKey, "test-user-123")
	token.Set(jwt.IssuerKey, jwksServer.URL+"/realms/test")
	token.Set(jwt.AudienceKey, []string{"test-client"})
	token.Set(jwt.ExpirationKey, time.Now().Add(time.Hour))
	token.Set(jwt.IssuedAtKey, time.Now())
	token.Set("email", "test@example.com")
	token.Set("preferred_username", "testuser")

	// Sign the token with the private key
	privJWK, err := jwk.FromRaw(privateKey)
	if err != nil {
		t.Fatalf("failed to create private JWK: %v", err)
	}
	privJWK.Set(jwk.KeyIDKey, "test-key-id")
	privJWK.Set(jwk.AlgorithmKey, jwa.RS256)

	signedToken, err := jwt.Sign(token, jwt.WithKey(jwa.RS256, privJWK))
	if err != nil {
		t.Fatalf("failed to sign token: %v", err)
	}

	// Create the middleware pointing to our mock server
	mw := NewJWTMiddleware(JWTConfig{
		KeycloakURL: jwksServer.URL,
		Realm:       "test",
		ClientID:    "test-client",
	})

	var capturedClaims UserClaims
	handler := mw.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		claims, ok := GetUserClaims(r.Context())
		if !ok {
			t.Error("expected claims in context")
			return
		}
		capturedClaims = claims
		w.WriteHeader(http.StatusOK)
	}))

	r := httptest.NewRequest("GET", "/", nil)
	r.Header.Set("Authorization", "Bearer "+string(signedToken))
	w := httptest.NewRecorder()

	handler.ServeHTTP(w, r)

	if w.Code != http.StatusOK {
		t.Errorf("status = %d, want %d", w.Code, http.StatusOK)
	}
	if capturedClaims.Sub != "test-user-123" {
		t.Errorf("Sub = %q, want %q", capturedClaims.Sub, "test-user-123")
	}
	if capturedClaims.Email != "test@example.com" {
		t.Errorf("Email = %q, want %q", capturedClaims.Email, "test@example.com")
	}
}
