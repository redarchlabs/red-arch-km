package handlers

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/middleware"
	"github.com/redarchlabs/red-arch-km-2/services/api-go/internal/repository"
)

// fakeProvisioner is an in-memory userProvisioner for testing the first-login
// relink/provision branches without a database.
type fakeProvisioner struct {
	byEmail    repository.UserProfile
	byEmailErr error
	relinked   repository.UserProfile
	relinkErr  error
	upserted   repository.UserProfile
	upsertErr  error

	byEmailCalled bool
	relinkCalled  bool
	relinkArg     repository.RelinkAuthSubjectParams
	upsertCalled  bool
	upsertArg     repository.UpsertUserProfileParams
}

func (f *fakeProvisioner) GetUserProfileByEmail(_ context.Context, _ string) (repository.UserProfile, error) {
	f.byEmailCalled = true
	return f.byEmail, f.byEmailErr
}

func (f *fakeProvisioner) RelinkAuthSubject(_ context.Context, arg repository.RelinkAuthSubjectParams) (repository.UserProfile, error) {
	f.relinkCalled = true
	f.relinkArg = arg
	return f.relinked, f.relinkErr
}

func (f *fakeProvisioner) UpsertUserProfile(_ context.Context, arg repository.UpsertUserProfileParams) (repository.UserProfile, error) {
	f.upsertCalled = true
	f.upsertArg = arg
	return f.upserted, f.upsertErr
}

// AC-4.2: verified email matching an existing profile rebinds auth_subject.
func TestProvisionOrRelink_VerifiedEmailRelinks(t *testing.T) {
	existingID := ToPgUUID(uuid.New())
	f := &fakeProvisioner{
		byEmail:  repository.UserProfile{ID: existingID, Email: "alice@example.com"},
		relinked: repository.UserProfile{ID: existingID, AuthSubject: "user_clerk", Email: "alice@example.com"},
	}
	claims := middleware.UserClaims{Sub: "user_clerk", Email: "alice@example.com", EmailVerified: true}

	got, err := provisionOrRelinkUser(context.Background(), f, claims)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !f.relinkCalled {
		t.Fatal("expected RelinkAuthSubject to be called")
	}
	if f.upsertCalled {
		t.Error("must NOT provision a new row when relinking")
	}
	if f.relinkArg.AuthSubject != "user_clerk" || f.relinkArg.ID != existingID {
		t.Errorf("relink args = %+v, want ID=%v AuthSubject=user_clerk", f.relinkArg, existingID)
	}
	if got.AuthSubject != "user_clerk" {
		t.Errorf("returned AuthSubject = %q, want user_clerk", got.AuthSubject)
	}
}

// AC-4.3 (anti-takeover): an UNVERIFIED email matching an existing profile is
// refused — never relinked.
func TestProvisionOrRelink_UnverifiedEmailRefused(t *testing.T) {
	f := &fakeProvisioner{
		byEmail: repository.UserProfile{ID: ToPgUUID(uuid.New()), Email: "victim@example.com"},
	}
	claims := middleware.UserClaims{Sub: "user_attacker", Email: "victim@example.com", EmailVerified: false}

	_, err := provisionOrRelinkUser(context.Background(), f, claims)
	if !errors.Is(err, errEmailTakenUnverified) {
		t.Fatalf("err = %v, want errEmailTakenUnverified", err)
	}
	if f.relinkCalled {
		t.Error("must NOT relink on an unverified email (takeover)")
	}
	if f.upsertCalled {
		t.Error("must NOT provision over an existing email")
	}
}

// AC-4.4: a brand-new user (no email match) provisions a fresh profile with
// is_site_admin=false.
func TestProvisionOrRelink_NewUserProvisions(t *testing.T) {
	newProfile := repository.UserProfile{AuthSubject: "user_new", Email: "new@example.com"}
	f := &fakeProvisioner{byEmailErr: pgx.ErrNoRows, upserted: newProfile}
	claims := middleware.UserClaims{Sub: "user_new", Email: "new@example.com", EmailVerified: true, PreferredUsername: "newbie"}

	got, err := provisionOrRelinkUser(context.Background(), f, claims)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if f.relinkCalled {
		t.Error("must NOT relink when there is no email match")
	}
	if !f.upsertCalled {
		t.Fatal("expected UpsertUserProfile to be called")
	}
	if f.upsertArg.AuthSubject != "user_new" || f.upsertArg.Username != "newbie" {
		t.Errorf("upsert args = %+v, want AuthSubject=user_new Username=newbie", f.upsertArg)
	}
	if f.upsertArg.IsSiteAdmin.Bool || !f.upsertArg.IsSiteAdmin.Valid {
		t.Errorf("new user must be is_site_admin=false (got %+v)", f.upsertArg.IsSiteAdmin)
	}
	if got.AuthSubject != "user_new" {
		t.Errorf("returned AuthSubject = %q, want user_new", got.AuthSubject)
	}
}

// An empty email skips the relink lookup entirely and provisions directly.
func TestProvisionOrRelink_NoEmailSkipsLookup(t *testing.T) {
	f := &fakeProvisioner{upserted: repository.UserProfile{AuthSubject: "user_noemail"}}
	claims := middleware.UserClaims{Sub: "user_noemail", Email: ""}

	if _, err := provisionOrRelinkUser(context.Background(), f, claims); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if f.byEmailCalled {
		t.Error("must NOT query by email when claims.Email is empty")
	}
	if !f.upsertCalled {
		t.Error("expected UpsertUserProfile to be called")
	}
}

// A real DB error from the email lookup propagates (not treated as no-match).
func TestProvisionOrRelink_EmailLookupErrorPropagates(t *testing.T) {
	dbErr := errors.New("connection reset")
	f := &fakeProvisioner{byEmailErr: dbErr}
	claims := middleware.UserClaims{Sub: "user_x", Email: "x@example.com", EmailVerified: true}

	_, err := provisionOrRelinkUser(context.Background(), f, claims)
	if !errors.Is(err, dbErr) {
		t.Fatalf("err = %v, want the underlying DB error", err)
	}
	if f.upsertCalled || f.relinkCalled {
		t.Error("must not provision/relink when the email lookup errored")
	}
}
