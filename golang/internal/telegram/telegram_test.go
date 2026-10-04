package telegram

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

func TestFilterDocuments(t *testing.T) {
	dir := t.TempDir()
	var files []string
	for i := 0; i < 52; i++ {
		file := filepath.Join(dir, strings.Repeat("a", i+1))
		if err := os.WriteFile(file, nil, 0o644); err != nil {
			t.Fatal(err)
		}
		files = append(files, file)
	}
	large, err := os.Create(filepath.Join(dir, "large"))
	if err != nil {
		t.Fatal(err)
	}
	if err := large.Truncate(50*1024*1024 + 1); err != nil {
		t.Fatal(err)
	}
	if err := large.Close(); err != nil {
		t.Fatal(err)
	}
	files = append([]string{large.Name()}, files...)
	keep, skipped, err := FilterDocuments(files)
	if err != nil || len(keep) != 50 || len(skipped) != 3 || skipped[0] != "large" {
		t.Fatalf("%d %v %v", len(keep), skipped, err)
	}
	exact, err := os.OpenFile(files[1], os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	if err := exact.Truncate(50 * 1024 * 1024); err != nil {
		t.Fatal(err)
	}
	_ = exact.Close()
	keep, skipped, err = FilterDocuments([]string{files[1]})
	if err != nil || len(keep) != 1 || len(skipped) != 0 {
		t.Fatalf("%v %v %v", keep, skipped, err)
	}
	if _, _, err := FilterDocuments([]string{filepath.Join(dir, "missing")}); err == nil {
		t.Fatal("stat error")
	}
}
func TestPost(t *testing.T) {
	root := t.TempDir()
	ctx := context.Background()
	m := &manifest.Manifest{
		Upstream: manifest.Upstream{Ref: "v1"},
		Release: manifest.Release{
			Targets: []manifest.Destination{{Type: "telegram", Chat: "@channel"}},
		},
	}
	if err := Post(ctx, root, "app", m, true); err == nil ||
		err.Error() != "no built assets, nothing to post" {
		t.Fatalf("%v", err)
	}
	for _, dir := range []string{"out", "notes"} {
		if err := os.MkdirAll(filepath.Join(root, ".graves", dir), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	for name, content := range map[string]string{
		"out/a.tgz":         "payload",
		"notes/notes.md":    "LLM notes",
		"notes/fallback.md": "Fallback notes",
	} {
		if err := os.WriteFile(
			filepath.Join(root, ".graves", name),
			[]byte(content),
			0o644,
		); err != nil {
			t.Fatal(err)
		}
	}
	if err := Post(ctx, root, "app", m, true); err != nil {
		t.Fatal(err)
	}
	var posts []string
	server := httptest.NewServer(
		http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if err := r.ParseMultipartForm(1 << 20); err != nil {
				t.Error(err)
				return
			}
			defer func() { _ = r.MultipartForm.RemoveAll() }()
			var rich struct {
				Markdown string `json:"markdown"`
			}
			if err := json.Unmarshal([]byte(r.FormValue("rich_message")), &rich); err != nil {
				t.Error(err)
			}
			posts = append(posts, rich.Markdown)
			file, _, err := r.FormFile("doc0-a.tgz")
			if err != nil {
				t.Error(err)
				return
			}
			data, err := io.ReadAll(file)
			_ = file.Close()
			if err != nil || string(data) != "payload" {
				t.Errorf("documents changed: %q %v", data, err)
			}
			if len(posts) == 1 {
				w.WriteHeader(400)
				_, _ = io.WriteString(w, `{"ok":false,"error_code":400}`)
			} else {
				_, _ = io.WriteString(w, `{"ok":true,"result":{}}`)
			}
		}),
	)
	defer server.Close()
	original := newTelegramClient
	newTelegramClient = func(opts ...ci.TelegramOption) (*ci.TelegramClient, error) {
		return ci.NewTelegramClient(
			append(
				opts,
				ci.WithTelegramServerURL(server.URL),
				ci.WithTelegramBackoff(ci.BackoffOptions{MaxRetriesTime: -1}),
			)...)
	}
	t.Cleanup(func() { newTelegramClient = original })
	t.Setenv("TELEGRAM_BOT_TOKEN", "123:token")
	if err := Post(ctx, root, "app", m, false); err != nil {
		t.Fatal(err)
	}
	if len(posts) != 2 || !strings.HasPrefix(posts[0], "# app 1-p0\n\nLLM notes") ||
		!strings.HasPrefix(posts[1], "# app 1-p0\n\nFallback notes") {
		t.Fatal(posts)
	}
	newTelegramClient = func(_ ...ci.TelegramOption) (*ci.TelegramClient, error) {
		t.Fatal("dry-run opened client")
		return nil, nil
	}
	if err := Post(ctx, root, "app", m, true); err != nil {
		t.Fatal(err)
	}
}
func TestPostNoFallbackOnServerError(t *testing.T) {
	root := t.TempDir()
	for _, dir := range []string{"out", "notes"} {
		if err := os.MkdirAll(filepath.Join(root, ".graves", dir), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	for _, file := range []string{"out/a", "notes/notes.md", "notes/fallback.md"} {
		if err := os.WriteFile(
			filepath.Join(root, ".graves", file),
			[]byte("content"),
			0o644,
		); err != nil {
			t.Fatal(err)
		}
	}
	calls := 0
	server := httptest.NewServer(
		http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			calls++
			w.WriteHeader(500)
			_, _ = io.WriteString(w, `{"ok":false,"error_code":500}`)
		}),
	)
	defer server.Close()
	original := newTelegramClient
	newTelegramClient = func(opts ...ci.TelegramOption) (*ci.TelegramClient, error) {
		return ci.NewTelegramClient(
			append(
				opts,
				ci.WithTelegramServerURL(server.URL),
				ci.WithTelegramBackoff(ci.BackoffOptions{MaxRetriesTime: -1}),
			)...)
	}
	t.Cleanup(func() { newTelegramClient = original })
	t.Setenv("TELEGRAM_BOT_TOKEN", "123:token")
	m := &manifest.Manifest{
		Release: manifest.Release{
			Targets: []manifest.Destination{{Type: "telegram", Chat: "@channel"}},
		},
	}
	if err := Post(context.Background(), root, "app", m, false); err == nil ||
		calls != 1 {
		t.Fatalf("calls %d error %v", calls, err)
	}
}
