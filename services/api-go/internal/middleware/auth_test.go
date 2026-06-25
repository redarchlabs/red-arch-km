package middleware

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
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
		{name: "valid bearer token", header: "Bearer token123", expected: "token123"},
		{name: "lowercase bearer", header: "bearer token456", expected: "token456"},
		{name: "no header", header: "", expected: ""},
		{name: "no bearer prefix", header: "token789", expected: ""},
		{name: "basic auth", header: "Basic abc123", expected: ""},
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
	ctx := context.Background()
	if _, ok := GetUserClaims(ctx); ok {
		t.Error("expected no claims in empty context")
	}

	claims := UserClaims{Sub: "user-123", Email: "test@example.com", PreferredUsername: "testuser"}
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
		KeycloakURL:      "http://keycloak:8080",
		KeycloakRealm:    "test",
		KeycloakClientID: "test-client",
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

// --- test helpers ------------------------------------------------------------

type signer struct {
	priv  *rsa.PrivateKey
	priv2 *rsa.PrivateKey // a second key NOT published in the JWKS (for bad-sig tests)
	kid   string
}

// newMockJWKS starts an httptest server that serves the public JWKS (for the
// first key only) at ANY path, mirroring both Keycloak's /certs endpoint and
// Clerk's /.well-known/jwks.json. Returns the server and a signer.
func newMockJWKS(t *testing.T) (*httptest.Server, *signer) {
	t.Helper()

	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate RSA key: %v", err)
	}
	priv2, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("generate 2nd RSA key: %v", err)
	}

	const kid = "test-key-id"
	pubJWK, err := jwk.FromRaw(priv.PublicKey)
	if err != nil {
		t.Fatalf("public JWK: %v", err)
	}
	_ = pubJWK.Set(jwk.KeyIDKey, kid)
	_ = pubJWK.Set(jwk.AlgorithmKey, jwa.RS256)
	_ = pubJWK.Set(jwk.KeyUsageKey, "sig")

	jwks := jwk.NewSet()
	_ = jwks.AddKey(pubJWK)

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(jwks)
	}))
	t.Cleanup(srv.Close)

	return srv, &signer{priv: priv, priv2: priv2, kid: kid}
}

// sign builds and signs a JWT with the published key.
func (s *signer) sign(t *testing.T, claims map[string]any) string {
	t.Helper()
	return s.signWith(t, s.priv, claims)
}

// signBad signs with the unpublished key (signature won't verify).
func (s *signer) signBad(t *testing.T, claims map[string]any) string {
	t.Helper()
	return s.signWith(t, s.priv2, claims)
}

func (s *signer) signWith(t *testing.T, key *rsa.PrivateKey, claims map[string]any) string {
	t.Helper()
	tok := jwt.New()
	for k, v := range claims {
		_ = tok.Set(k, v)
	}
	privJWK, err := jwk.FromRaw(key)
	if err != nil {
		t.Fatalf("private JWK: %v", err)
	}
	_ = privJWK.Set(jwk.KeyIDKey, s.kid)
	_ = privJWK.Set(jwk.AlgorithmKey, jwa.RS256)

	signed, err := jwt.Sign(tok, jwt.WithKey(jwa.RS256, privJWK))
	if err != nil {
		t.Fatalf("sign token: %v", err)
	}
	return string(signed)
}

// serve drives a request with the given bearer token through the middleware and
// returns the response recorder plus any captured claims.
func serve(mw *JWTMiddleware, bearer string) (*httptest.ResponseRecorder, *UserClaims) {
	var captured *UserClaims
	handler := mw.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if c, ok := GetUserClaims(r.Context()); ok {
			captured = &c
		}
		w.WriteHeader(http.StatusOK)
	}))
	r := httptest.NewRequest("GET", "/", nil)
	if bearer != "" {
		r.Header.Set("Authorization", "Bearer "+bearer)
	}
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, r)
	return w, captured
}

func clerkClaims(issuer string) map[string]any {
	return map[string]any{
		jwt.SubjectKey:    "user_2abc",
		jwt.IssuerKey:     issuer,
		jwt.ExpirationKey: time.Now().Add(time.Hour),
		jwt.IssuedAtKey:   time.Now(),
		"azp":             "http://localhost:3000",
		"email":           "alice@example.com",
		"username":        "alice",
	}
}

func keycloakClaims(issuer string) map[string]any {
	return map[string]any{
		jwt.SubjectKey:       "kc-uuid-123",
		jwt.IssuerKey:        issuer,
		jwt.AudienceKey:      []string{"redarch-km"},
		jwt.ExpirationKey:    time.Now().Add(time.Hour),
		jwt.IssuedAtKey:      time.Now(),
		"email":              "bob@example.com",
		"preferred_username": "bob",
	}
}

// --- Clerk verify path (AC-1.1, AC-1.3) -------------------------------------

func TestClerk_ValidToken_Authenticates(t *testing.T) {
	srv, s := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	w, claims := serve(mw, s.sign(t, clerkClaims(srv.URL)))
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", w.Code)
	}
	if claims == nil {
		t.Fatal("expected claims")
	}
	if claims.Sub != "user_2abc" {
		t.Errorf("Sub = %q, want user_2abc", claims.Sub)
	}
	if claims.Email != "alice@example.com" {
		t.Errorf("Email = %q, want alice@example.com", claims.Email)
	}
	// Clerk `username` claim maps onto PreferredUsername (AC-1.1).
	if claims.PreferredUsername != "alice" {
		t.Errorf("PreferredUsername = %q, want alice", claims.PreferredUsername)
	}
}

func TestClerk_RejectsBadAZP(t *testing.T) {
	srv, s := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	c := clerkClaims(srv.URL)
	c["azp"] = "http://evil.example.com"
	w, _ := serve(mw, s.sign(t, c))
	if w.Code != http.StatusUnauthorized {
		t.Errorf("bad azp: status = %d, want 401", w.Code)
	}
}

func TestClerk_RejectsMissingAZP(t *testing.T) {
	srv, s := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	c := clerkClaims(srv.URL)
	delete(c, "azp")
	w, _ := serve(mw, s.sign(t, c))
	if w.Code != http.StatusUnauthorized {
		t.Errorf("missing azp: status = %d, want 401", w.Code)
	}
}

func TestClerk_RejectsBadSignature(t *testing.T) {
	srv, s := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	// Signed with a key whose public half is NOT in the JWKS.
	w, _ := serve(mw, s.signBad(t, clerkClaims(srv.URL)))
	if w.Code != http.StatusUnauthorized {
		t.Errorf("bad sig: status = %d, want 401", w.Code)
	}
}

func TestClerk_RejectsExpired(t *testing.T) {
	srv, s := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	c := clerkClaims(srv.URL)
	c[jwt.ExpirationKey] = time.Now().Add(-time.Hour)
	c[jwt.IssuedAtKey] = time.Now().Add(-2 * time.Hour)
	w, _ := serve(mw, s.sign(t, c))
	if w.Code != http.StatusUnauthorized {
		t.Errorf("expired: status = %d, want 401", w.Code)
	}
}

func TestClerk_RejectsFutureNbf(t *testing.T) {
	srv, s := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	c := clerkClaims(srv.URL)
	c[jwt.NotBeforeKey] = time.Now().Add(time.Hour)
	w, _ := serve(mw, s.sign(t, c))
	if w.Code != http.StatusUnauthorized {
		t.Errorf("future nbf: status = %d, want 401", w.Code)
	}
}

// --- Keycloak verify path retained (AC-1.2, AC-1.6) -------------------------

func TestKeycloak_ValidToken_Authenticates(t *testing.T) {
	srv, s := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		KeycloakURL:      srv.URL,
		KeycloakRealm:    "test",
		KeycloakClientID: "redarch-km",
	})

	w, claims := serve(mw, s.sign(t, keycloakClaims(srv.URL+"/realms/test")))
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", w.Code)
	}
	if claims == nil || claims.Sub != "kc-uuid-123" {
		t.Fatalf("claims = %+v, want Sub=kc-uuid-123", claims)
	}
	if claims.PreferredUsername != "bob" {
		t.Errorf("PreferredUsername = %q, want bob", claims.PreferredUsername)
	}
}

func TestKeycloak_RejectsWrongAudience(t *testing.T) {
	srv, s := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		KeycloakURL:      srv.URL,
		KeycloakRealm:    "test",
		KeycloakClientID: "redarch-km",
	})

	c := keycloakClaims(srv.URL + "/realms/test")
	c[jwt.AudienceKey] = []string{"some-other-client"}
	w, _ := serve(mw, s.sign(t, c))
	if w.Code != http.StatusUnauthorized {
		t.Errorf("wrong aud: status = %d, want 401", w.Code)
	}
}

// --- Dual-verify routing by issuer (AC-1.2) ---------------------------------

func TestDualVerify_RoutesByIssuer(t *testing.T) {
	srv, s := newMockJWKS(t)
	// Both providers configured against the same mock JWKS.
	mw := NewJWTMiddleware(JWTConfig{
		KeycloakURL:      srv.URL,
		KeycloakRealm:    "test",
		KeycloakClientID: "redarch-km",
		ClerkIssuer:      srv.URL + "/clerk",
		ClerkAllowedAZP:  []string{"http://localhost:3000"},
	})

	// A Clerk-issued token authenticates via the Clerk verifier (azp checked).
	if w, _ := serve(mw, s.sign(t, clerkClaims(srv.URL+"/clerk"))); w.Code != http.StatusOK {
		t.Errorf("clerk token via dual-verify: status = %d, want 200", w.Code)
	}
	// A Keycloak-issued token still authenticates via the Keycloak verifier.
	if w, _ := serve(mw, s.sign(t, keycloakClaims(srv.URL+"/realms/test"))); w.Code != http.StatusOK {
		t.Errorf("keycloak token via dual-verify: status = %d, want 200", w.Code)
	}
	// A Clerk token must NOT pass the Keycloak audience path — its azp is still
	// enforced (route is by issuer, not by trying every verifier).
	c := clerkClaims(srv.URL + "/clerk")
	c["azp"] = "http://evil.example.com"
	if w, _ := serve(mw, s.sign(t, c)); w.Code != http.StatusUnauthorized {
		t.Errorf("clerk token bad azp via dual-verify: status = %d, want 401", w.Code)
	}
}

func TestDualVerify_RejectsUnknownIssuer(t *testing.T) {
	srv, s := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	c := clerkClaims("https://attacker.example.com")
	w, _ := serve(mw, s.sign(t, c))
	if w.Code != http.StatusUnauthorized {
		t.Errorf("unknown issuer: status = %d, want 401", w.Code)
	}
}

// --- JWKS outage is graceful, not fatal (AC-1.4) ----------------------------

func TestClerk_JWKSOutage_ReturnsUnauthorizedNotPanic(t *testing.T) {
	srv, s := newMockJWKS(t)
	token := s.sign(t, clerkClaims(srv.URL))
	// Take the JWKS endpoint down before the first request.
	srv.Close()

	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	w, _ := serve(mw, token)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("jwks outage: status = %d, want 401 (no panic)", w.Code)
	}
}

// --- Algorithm-confusion vectors (security-analyst LOW-1) --------------------
// The verifier pins RS256 via the JWKS keys' published `alg`; these assert the
// two classic downgrade attacks fail closed.

// An HS256 token signed with the RSA public-key bytes as the HMAC secret (the
// canonical RS256→HS256 confusion) must be rejected — the keyset is RS256-only.
func TestClerk_RejectsHS256AlgConfusion(t *testing.T) {
	srv, s := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	// What an attacker has: the public key (published in the JWKS), used as the
	// symmetric HMAC secret.
	pubDER, err := x509.MarshalPKIXPublicKey(&s.priv.PublicKey)
	if err != nil {
		t.Fatalf("marshal public key: %v", err)
	}
	tok := jwt.New()
	for k, v := range clerkClaims(srv.URL) {
		_ = tok.Set(k, v)
	}
	forged, err := jwt.Sign(tok, jwt.WithKey(jwa.HS256, pubDER))
	if err != nil {
		t.Fatalf("sign HS256: %v", err)
	}

	w, _ := serve(mw, string(forged))
	if w.Code != http.StatusUnauthorized {
		t.Errorf("HS256 alg-confusion: status = %d, want 401", w.Code)
	}
}

// An unsigned (alg:none) token must be rejected.
func TestClerk_RejectsAlgNone(t *testing.T) {
	srv, s := newMockJWKS(t)
	_ = s
	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	b64 := func(v any) string {
		raw, _ := json.Marshal(v)
		return base64.RawURLEncoding.EncodeToString(raw)
	}
	header := b64(map[string]string{"alg": "none", "typ": "JWT"})
	payload := b64(map[string]any{
		"sub": "user_2abc",
		"iss": srv.URL,
		"azp": "http://localhost:3000",
		"exp": time.Now().Add(time.Hour).Unix(),
	})
	noneToken := strings.Join([]string{header, payload, ""}, ".")

	w, _ := serve(mw, noneToken)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("alg:none: status = %d, want 401", w.Code)
	}
}

// --- Fail-closed identity contract (QA HIGH-1) ------------------------------

// A validly-signed, correct-issuer, azp-valid token with NO `sub` must be
// rejected — auth must never grant an empty identity.
func TestClerk_RejectsMissingSub(t *testing.T) {
	srv, s := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	c := clerkClaims(srv.URL)
	delete(c, jwt.SubjectKey)
	w, _ := serve(mw, s.sign(t, c))
	if w.Code != http.StatusUnauthorized {
		t.Errorf("missing sub: status = %d, want 401", w.Code)
	}
}

func TestClerk_RejectsEmptyAZP(t *testing.T) {
	srv, s := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	c := clerkClaims(srv.URL)
	c["azp"] = ""
	w, _ := serve(mw, s.sign(t, c))
	if w.Code != http.StatusUnauthorized {
		t.Errorf("empty azp: status = %d, want 401", w.Code)
	}
}

// --- Cross-provider key confusion (QA MEDIUM-2) -----------------------------
// Two DISTINCT mock JWKS (distinct keys). A token claiming the Clerk issuer but
// signed by the Keycloak key must 401 — each verifier reads ONLY its own keyset.
func TestDualVerify_RejectsCrossProviderKey(t *testing.T) {
	srvKC, sKC := newMockJWKS(t)
	srvClerk, sClerk := newMockJWKS(t)

	mw := NewJWTMiddleware(JWTConfig{
		KeycloakURL:      srvKC.URL,
		KeycloakRealm:    "test",
		KeycloakClientID: "redarch-km",
		ClerkIssuer:      srvClerk.URL,
		ClerkAllowedAZP:  []string{"http://localhost:3000"},
	})

	// iss = Clerk, but signed with the Keycloak key → Clerk verifier's keyset
	// (srvClerk) has no matching key → signature fails → 401.
	if w, _ := serve(mw, sKC.sign(t, clerkClaims(srvClerk.URL))); w.Code != http.StatusUnauthorized {
		t.Errorf("cross-provider key: status = %d, want 401", w.Code)
	}
	// Sanity: a token properly signed by the Clerk key still authenticates.
	if w, _ := serve(mw, sClerk.sign(t, clerkClaims(srvClerk.URL))); w.Code != http.StatusOK {
		t.Errorf("legit clerk token: status = %d, want 200", w.Code)
	}
}

// --- Malformed bearer reaches the routing parse first (QA LOW-3) -------------
func TestMalformedBearer_RejectedNotPanic(t *testing.T) {
	srv, _ := newMockJWKS(t)
	mw := NewJWTMiddleware(JWTConfig{
		ClerkIssuer:     srv.URL,
		ClerkAllowedAZP: []string{"http://localhost:3000"},
	})

	for _, bad := range []string{"not.a.jwt", "a.b", "garbage", "...", "ZZZ.ZZZ.ZZZ"} {
		w, _ := serve(mw, bad)
		if w.Code != http.StatusUnauthorized {
			t.Errorf("malformed %q: status = %d, want 401 (no panic)", bad, w.Code)
		}
	}
}
