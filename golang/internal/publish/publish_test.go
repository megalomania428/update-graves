// cspell:ignore commitish
package publish

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	ci "github.com/megalomania428/go-lib-ci"
	"github.com/megalomania428/update-graves/golang/internal/manifest"
)

func TestPublisher_Destinations(t *testing.T) {
	t.Setenv("GH_TOKEN", "self-token")
	t.Setenv("RELEASES_TOKEN", "external-token")
	p := &Publisher{Repo: "owner/current", Mode: "release", Manifest: &manifest.Manifest{
		Release: manifest.Release{
			Targets: []manifest.Destination{
				{Type: "repo", Repo: "private"},
				{Type: "self"},
				{Type: "telegram", Chat: "@channel"},
				{Type: "self"},
			},
		}}}
	d, err := p.Destinations()
	if err != nil || len(d) != 2 || d[0].Repo != "owner/private" ||
		d[0].Token != "external-token" ||
		d[1].Token != "self-token" {
		t.Fatalf("%v %v", d, err)
	}
	p.Mode = "draft"
	d, err = p.Destinations()
	if err != nil || len(d) != 1 || d[0].Repo != "owner/current" {
		t.Fatalf("%v %v", d, err)
	}
	manifestOf := func(targets ...manifest.Destination) *manifest.Manifest {
		return &manifest.Manifest{Release: manifest.Release{Targets: targets}}
	}
	private := manifest.Destination{Type: "repo", Repo: "private"}
	for _, tt := range []struct {
		name        string
		manifest    *manifest.Manifest
		repo, token string
	}{
		{"no manifest", nil, "owner/current", "self-token"},
		{"external only", manifestOf(private, manifest.Destination{Type: "telegram",
			Chat: "@channel"}), "owner/private", "external-token"},
		{"first external", manifestOf(manifest.Destination{Type: "repo", Repo: "first"},
			manifest.Destination{Type: "repo", Repo: "second"}), "owner/first",
			"external-token"},
		{"telegram only", manifestOf(manifest.Destination{Type: "telegram",
			Chat: "@channel"}), "owner/current", "self-token"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			draft := &Publisher{Repo: "owner/current", Mode: "draft", Manifest: tt.manifest}
			d, err := draft.Destinations()
			if err != nil || len(d) != 1 || d[0].Repo != tt.repo || d[0].Token != tt.token {
				t.Fatalf("%v %v", d, err)
			}
		})
	}
	p.Mode = "release"
	t.Setenv("RELEASES_TOKEN", "")
	if _, err := p.Destinations(); err == nil {
		t.Fatal("missing PAT")
	}
	p.Dry = true
	if _, err := p.Destinations(); err != nil {
		t.Fatal(err)
	}
	external := &Publisher{Repo: "owner/current", Mode: "draft",
		Manifest: manifestOf(private)}
	if _, err := external.Destinations(); err == nil ||
		!strings.Contains(err.Error(), "release token required for owner/private") {
		t.Fatalf("missing PAT of an external draft: %v", err)
	}
	external.Dry = true
	if _, err := external.Destinations(); err != nil {
		t.Fatal(err)
	}
	p.Repo = "invalid"
	if _, err := p.Destinations(); err == nil {
		t.Fatal("repository format")
	}
}
func TestPublisher_Ensure(t *testing.T) {
	ctx := context.Background()
	calls, uploads := 0, 0
	var paths []string
	draftCreated := false
	server := httptest.NewServer(
		http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			calls++
			paths = append(paths, r.URL.Path)
			token := "self-token"
			if strings.Contains(r.URL.Path, "/private/") {
				token = "external-token"
			}
			if r.Header.Get("Authorization") != "Bearer "+token {
				t.Error("destination token")
			}
			switch {
			case strings.Contains(r.URL.Path, "/assets"):
				if r.Method == "GET" {
					_, _ = io.WriteString(w, `[]`)
				} else {
					uploads++
					_, _ = io.WriteString(w, `{"id":1}`)
				}
			case strings.Contains(r.URL.Path, "/tags/"):
				_, _ = io.WriteString(w, `{"id":11,"name":"app-v1","body":"notes"}`)
			case r.Method == "GET":
				if draftCreated {
					_, _ = io.WriteString(
						w,
						`[{"id":22,"tag_name":"v999","draft":true,"name`+
							`":"branch [feature] binaries","body":"branch [`+
							`feature] build from recent commit"}]`,
					)
				} else {
					_, _ = io.WriteString(w, `[]`)
				}
			case r.Method == "POST":
				var body map[string]any
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Error(err)
				}
				if body["tag_name"] != "v999" || body["draft"] != true ||
					body["name"] != "branch [feature] binaries" ||
					body["body"] != "branch [feature] build from recent commit" {
					t.Error(body)
				}
				if _, exists := body["target_commitish"]; exists {
					t.Error("unexpected target")
				}
				draftCreated = true
				_, _ = io.WriteString(w, `{"id":22}`)
			default:
				t.Errorf("unexpected %s %s", r.Method, r.URL)
			}
		}),
	)
	defer server.Close()
	original := newGitHubClient
	newGitHubClient = func(opts ...ci.GitHubOption) (*ci.GitHubClient, error) {
		return ci.NewGitHubClient(
			append(
				opts,
				ci.WithGitHubAPIBaseURL(server.URL),
				ci.WithGitHubUploadBaseURL(server.URL),
				ci.WithGitHubNoProgressBar(true),
				ci.WithGitHubBackoff(ci.BackoffOptions{MaxRetriesTime: -1}),
			)...)
	}
	t.Cleanup(func() { newGitHubClient = original })
	t.Setenv("GH_TOKEN", "self-token")
	t.Setenv("RELEASES_TOKEN", "external-token")
	t.Setenv("GITHUB_HEAD_REF", "feature")
	p := &Publisher{Root: t.TempDir(), Repo: "owner/current", Mode: "draft"}
	id, err := p.Ensure(ctx, "ignored")
	if err != nil || id != 22 {
		t.Fatalf("%d %v", id, err)
	}
	p.Mode, p.Tag, p.Name = "release", "app-v1", "app"
	p.Manifest = &manifest.Manifest{
		Release: manifest.Release{
			Targets: []manifest.Destination{{Type: "self"}, {Type: "repo", Repo: "private"}},
		},
	}
	id, err = p.Ensure(ctx, "notes")
	if err != nil || id != 11 {
		t.Fatalf("%d %v", id, err)
	}
	dir := filepath.Join(p.Root, ".graves", "out")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(
		filepath.Join(dir, "app.tgz"),
		[]byte("payload"),
		0o644,
	); err != nil {
		t.Fatal(err)
	}
	if err := p.Upload(ctx); err != nil || uploads != 2 {
		t.Fatalf("%d %v", uploads, err)
	}
	p.Mode = "draft"
	t.Setenv("GRAVES_RELEASE_ID", "22")
	t.Setenv("GRAVES_RELEASE_REPO", "owner/current")
	if err := p.Upload(ctx); err != nil || uploads != 3 {
		t.Fatalf("%d %v", uploads, err)
	}
	t.Setenv("GRAVES_RELEASE_ID", "")
	if err := p.Upload(ctx); err == nil {
		t.Fatal("missing release ID")
	}
	t.Setenv("GRAVES_RELEASE_ID", "22")
	for _, repo := range []string{"owner/private", ""} {
		t.Setenv("GRAVES_RELEASE_REPO", repo)
		err := p.Upload(ctx)
		if err == nil || !strings.Contains(err.Error(), "differs from draft destination") ||
			uploads != 3 {
			t.Fatalf("%q: %d %v", repo, uploads, err)
		}
	}
	p.Mode, p.Dry = "release", true
	before := calls
	if _, err := p.Ensure(ctx, "notes"); err != nil {
		t.Fatal(err)
	}
	if err := p.Upload(ctx); err != nil {
		t.Fatal(err)
	}
	if calls != before {
		t.Fatal("dry-run called API")
	}
	p = &Publisher{Root: p.Root, Repo: "owner/current", Mode: "draft", Name: "app",
		Manifest: &manifest.Manifest{Release: manifest.Release{
			Targets: []manifest.Destination{{Type: "repo", Repo: "private"}},
		}}}
	paths = nil
	id, err = p.Ensure(ctx, "ignored")
	if err != nil || id != 22 || len(paths) == 0 {
		t.Fatalf("%d %v %v", id, err, paths)
	}
	for _, path := range paths {
		if !strings.HasPrefix(path, "/repos/owner/private/") {
			t.Fatalf("draft left its destination: %v", paths)
		}
	}
	t.Setenv("GRAVES_RELEASE_REPO", "owner/private")
	paths = nil
	if err := p.Upload(ctx); err != nil || uploads != 4 || len(paths) == 0 {
		t.Fatalf("%d %v %v", uploads, err, paths)
	}
	for _, path := range paths {
		if !strings.HasPrefix(path, "/repos/owner/private/") {
			t.Fatalf("draft upload left its destination: %v", paths)
		}
	}
	t.Setenv("GRAVES_RELEASE_REPO", "owner/current")
	if err := p.Upload(ctx); err == nil || uploads != 4 ||
		!strings.Contains(err.Error(), "differs from draft destination") {
		t.Fatalf("%d %v", uploads, err)
	}
}
func TestFiles(t *testing.T) {
	root := t.TempDir()
	if files, err := Files(root); err != nil || len(files) != 0 {
		t.Fatalf("%v %v", files, err)
	}
	p := &Publisher{Root: root}
	if err := p.Upload(context.Background()); err == nil {
		t.Fatal("no assets")
	}
	dir := filepath.Join(root, ".graves", "out")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"b", "a"} {
		if err := os.WriteFile(filepath.Join(dir, name), nil, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	files, err := Files(root)
	if err != nil || filepath.Base(files[0]) != "a" || filepath.Base(files[1]) != "b" {
		t.Fatalf("%v %v", files, err)
	}
	if err := os.Mkdir(filepath.Join(dir, "directory"), 0o755); err != nil {
		t.Fatal(err)
	}
	if _, err := Files(root); err == nil {
		t.Fatal("directory asset")
	}
}
