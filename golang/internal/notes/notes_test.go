// cspell:ignore pipefail
package notes

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	ci "github.com/megalomania428/go-lib-ci"
	"github.com/megalomania428/update-graves/golang/internal/manifest"
)

func TestBudget(t *testing.T) {
	if got := Budget(4096, "приложение", "v1"); got != 4096-utf8.RuneCountInString(
		"# приложение v1\n\n",
	) {
		t.Fatal(got)
	}
}
func TestFallback(t *testing.T) {
	upstream := manifest.Upstream{URL: "https://example.test", Ref: "v1"}
	files := []string{
		"001.patch",
		"002.patch",
		"001.patch",
		strings.Repeat("long", 100) + ".patch",
	}
	for _, budget := range []int{4096, 160, 100, 40, 1, 0} {
		text := Fallback("app", "v1", upstream, files, budget)
		if utf8.RuneCountInString(text) > budget {
			t.Fatalf("budget %d: %s", budget, text)
		}
		if text != Fallback("app", "v1", upstream, files, budget) {
			t.Fatal("nondeterministic")
		}
		if budget == 4096 && strings.Count(text, "- 001.patch") != 1 {
			t.Fatal(text)
		}
		if budget == 160 && !strings.Contains(text, "- …and 1 more") {
			t.Fatal(text)
		}
	}
	text := Fallback("app", "v1", upstream, nil, 4096)
	if !strings.Contains(text, "Upstream: https://example.test v1") ||
		!strings.HasSuffix(text, "Generated without LLM release notes.") {
		t.Fatal(text)
	}
}
func Test_attemptNotes(t *testing.T) {
	original := pause
	var delays []time.Duration
	pause = func(_ context.Context, delay time.Duration) error {
		delays = append(
			delays,
			delay,
		)
		return nil
	}
	t.Cleanup(func() { pause = original })
	calls := 0
	text, err := attemptNotes(
		context.Background(),
		3,
		func(context.Context) (string, error) {
			calls++
			switch calls {
			case 1:
				return "", errors.New("exit 1")
			case 2:
				return "  ", nil
			case 3:
				return "1234", nil
			default:
				return " ёж ", nil
			}
		},
	)
	if err != nil || text != "ёж" || calls != 4 ||
		!reflect.DeepEqual(
			delays,
			[]time.Duration{3 * time.Second, 9 * time.Second, 15 * time.Second},
		) {
		t.Fatalf("%s %v %d %v", text, err, calls, delays)
	}
	calls = 0
	_, err = attemptNotes(
		context.Background(),
		3,
		func(context.Context) (string, error) { calls++; return "1234", nil },
	)
	if err == nil || calls != 4 {
		t.Fatalf("%d %v", calls, err)
	}
	pause = func(context.Context, time.Duration) error { return context.Canceled }
	if _, err := attemptNotes(
		context.Background(),
		3,
		func(context.Context) (string, error) { return "", nil },
	); !errors.Is(
		err,
		context.Canceled,
	) {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := sleep(ctx, time.Hour); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	if err := sleep(context.Background(), time.Millisecond); err != nil {
		t.Fatal(err)
	}
}
func Test_writeConfig(t *testing.T) {
	for key, value := range map[string]string{
		"LLM_URL":        "https://llm.test",
		"LLM_NAME":       "test-model",
		"LLM_KEY":        "secret",
		"FETCH_MCP_URL":  "https://fetch.test",
		"FETCH_MCP_KEY":  "fetch-key",
		"SEARCH_MCP_URL": "https://search.test",
		"SEARCH_MCP_KEY": "",
		"LLM_EFF":        "",
		"LLM_RSN":        "",
		"LLM_CTX":        "",
		"LLM_MAX":        "",
	} {
		t.Setenv(key, value)
	}
	dir := t.TempDir()
	if err := writeConfig(dir); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"settings", "models", "mcp"} {
		data, err := os.ReadFile(filepath.Join(dir, name+".json"))
		if err != nil {
			t.Fatal(err)
		}
		var value map[string]any
		if err := json.Unmarshal(data, &value); err != nil {
			t.Fatal(err)
		}
		if name == "mcp" {
			servers := value["mcpServers"].(map[string]any)
			if len(servers) != 1 || servers["wripy"].(map[string]any)["directTools"] != false {
				t.Fatal(value)
			}
		}
		info, err := os.Stat(filepath.Join(dir, name+".json"))
		if err != nil || info.Mode().Perm() != 0o644 {
			t.Fatalf("%v %v", info, err)
		}
	}
	for _, key := range []string{"LLM_EFF", "LLM_RSN", "LLM_CTX", "LLM_MAX"} {
		t.Run(key, func(t *testing.T) {
			t.Setenv(key, "invalid")
			if err := writeConfig(dir); err == nil {
				t.Fatal("invalid config")
			}
		})
	}
	g := &Generator{Name: "app", Tag: "app-v1", Manifest: &manifest.Manifest{}}
	files := []string{"app.md", "patches/linux/001.patch"}
	args := strings.Join(g.containerArgs("/context", "/config", files, 123), " ")
	for _, want := range []string{
		"PI_MCP_CONFIG_MODE=exclusive",
		"--tools mcp ",
		" @app.md @patches/linux/001.patch",
		"123 characters",
		"/context:/workspace/notes:ro",
		"/config/models.json:/home/coder/.pi/agent/models.json:ro",
	} {
		if !strings.Contains(args, want) {
			t.Fatal(args)
		}
	}
	if strings.Contains(args, "secret") || strings.Contains(args, "fetch-key") {
		t.Fatal("secret in command")
	}
}
func fixtureGenerator(t *testing.T) *Generator {
	t.Helper()
	root := t.TempDir()
	source := filepath.Join(root, "sources", "app")
	if err := os.MkdirAll(filepath.Join(source, "patches"), 0o755); err != nil {
		t.Fatal(err)
	}
	data := "upstream: {url: 'https://example.test/upstream" +
		".git', ref: v1, patch: 0}\nshared: [_shared/to" +
		"ol]\ntargets:\n  - name: linux\n    patches: [" +
		"patches]\n    steps: [echo ok]\n  - name: wind" +
		"ows\n    patches: [patches]\n    steps: [echo ok]\n"
	for file, content := range map[string]string{
		"build.yaml":         data,
		"README.md":          "Application description",
		"patches/001.patch":  "Patch contents",
		"patches/ignore.txt": "not a patch",
	} {
		if err := os.WriteFile(
			filepath.Join(source, file),
			[]byte(content),
			0o644,
		); err != nil {
			t.Fatal(err)
		}
	}
	m, err := manifest.Load(filepath.Join(source, "build.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	return &Generator{Root: root, Name: "app", Tag: "app-v1", Manifest: m}
}
func TestGenerator_Generate(t *testing.T) {
	g := fixtureGenerator(t)
	files, err := g.PatchFiles()
	if err != nil || len(files) != 1 {
		t.Fatalf("%v %v", files, err)
	}
	text, err := g.Generate(context.Background(), true)
	if err != nil || !strings.Contains(text, "001.patch") {
		t.Fatalf("%s %v", text, err)
	}
	for _, file := range []string{"notes.md", "fallback.md"} {
		data, err := os.ReadFile(filepath.Join(g.Root, ".graves", "notes", file))
		if err != nil || string(data) != text {
			t.Fatalf("%s %v", data, err)
		}
	}
	t.Setenv("LLM_URL", "")
	if _, err := g.Generate(context.Background(), false); err != nil {
		t.Fatal(err)
	}
	g.Manifest.Release.NotesLimit = 1
	if _, err := g.Generate(context.Background(), true); err == nil {
		t.Fatal("header budget")
	}
}
func TestGeneratorLLMCommand(t *testing.T) {
	g := fixtureGenerator(t)
	gitRun(t, g.Root, "init", "-q", "-b", "master")
	gitRun(t, g.Root, "add", ".")
	gitRun(t, g.Root, "commit", "-qm", "initial")
	gitRun(t, g.Root, "tag", "app-v1")
	for key, value := range map[string]string{"LLM_URL": "https://llm.test",
		"LLM_NAME": "model", "LLM_KEY": "not-a-real-key"} {
		t.Setenv(key, value)
	}
	bin := t.TempDir()
	script := "#!/usr/bin/env bash\nset -euo pipefail\n" +
		"printf 'diagnostic only' >&2\nprintf 'First release notes.'\n"
	if err := os.WriteFile(
		filepath.Join(bin, "podman"),
		[]byte(script),
		0o755,
	); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	original := ensurePackages
	ensurePackages = func(context.Context, []string, ci.EnsurePackagesOptions) error {
		return nil
	}
	t.Cleanup(func() { ensurePackages = original })
	text, err := g.Generate(context.Background(), false)
	if err != nil || text != "First release notes." {
		t.Fatalf("stdout mixed with diagnostics: %q %v", text, err)
	}
}
func gitRun(t *testing.T, root string, args ...string) {
	t.Helper()
	if err := ci.RunCommand(
		context.Background(),
		ci.WithCommand("git", args...),
		ci.WithCommandDir(root),
		ci.WithCommandEnv(
			"GIT_AUTHOR_NAME=Test",
			"GIT_AUTHOR_EMAIL=test@example.test",
			"GIT_COMMITTER_NAME=Test",
			"GIT_COMMITTER_EMAIL=test@example.test",
		),
	); err != nil {
		t.Fatal(err)
	}
}
func TestGenerator_upstreamLog(t *testing.T) {
	g := fixtureGenerator(t)
	upstream := t.TempDir()
	gitRun(t, upstream, "init", "-q", "-b", "master")
	file := filepath.Join(upstream, "app")
	if err := os.WriteFile(file, []byte("old"), 0o644); err != nil {
		t.Fatal(err)
	}
	gitRun(t, upstream, "add", ".")
	gitRun(t, upstream, "commit", "-qm", "initial upstream")
	gitRun(t, upstream, "tag", "v1")
	if err := os.WriteFile(file, []byte("new"), 0o644); err != nil {
		t.Fatal(err)
	}
	gitRun(t, upstream, "add", ".")
	gitRun(t, upstream, "commit", "-qm", "upstream change")
	gitRun(t, upstream, "tag", "v2")
	build := filepath.Join(g.Root, "sources", "app", "build.yaml")
	data, err := os.ReadFile(build)
	if err != nil {
		t.Fatal(err)
	}
	data = []byte(strings.ReplaceAll(string(data), "https://example.test/upstream.git",
		upstream))
	if err := os.WriteFile(build, data, 0o644); err != nil {
		t.Fatal(err)
	}
	gitRun(t, g.Root, "init", "-q", "-b", "master")
	gitRun(t, g.Root, "add", ".")
	gitRun(t, g.Root, "commit", "-qm", "first release")
	gitRun(t, g.Root, "tag", "app-v1")
	g.Manifest.Upstream = manifest.Upstream{URL: upstream, Ref: "v2"}
	dir := t.TempDir()
	if err := g.upstreamLog(context.Background(), "app-v1", dir); err != nil {
		t.Fatal(err)
	}
	log, err := os.ReadFile(filepath.Join(dir, "upstream.log"))
	if err != nil || !strings.Contains(string(log), "upstream change") ||
		strings.Contains(string(log), "initial upstream") {
		t.Fatalf("incorrect upstream range: %s %v", log, err)
	}
	if err := g.upstreamLog(context.Background(), "missing", t.TempDir()); err == nil {
		t.Fatal("missing old manifest")
	}
	g.Manifest.Upstream.URL = "https://different.test/upstream.git"
	if err := g.upstreamLog(context.Background(), "app-v1", t.TempDir()); err != nil {
		t.Fatal("different upstream repositories should not be compared")
	}
}
func TestGenerator_makeContext(t *testing.T) {
	g := fixtureGenerator(t)
	ctx := context.Background()
	gitRun(t, g.Root, "init", "-q", "-b", "master")
	gitRun(t, g.Root, "add", ".")
	gitRun(t, g.Root, "commit", "-qm", "initial")
	gitRun(t, g.Root, "tag", "app-v1")
	files, err := g.PatchFiles()
	if err != nil {
		t.Fatal(err)
	}
	first := t.TempDir()
	if err := g.makeContext(ctx, first, files, 4096); err != nil {
		t.Fatal(err)
	}
	app, _ := os.ReadFile(filepath.Join(first, "app.md"))
	if !strings.Contains(string(app), "This is the first release") {
		t.Fatal(string(app))
	}
	patch, err := os.ReadFile(filepath.Join(first, "patches", "linux", "001.patch"))
	if err != nil || string(patch) != "Patch contents" {
		t.Fatalf("%q %v", patch, err)
	}
	attached, err := contextFiles(first)
	want := "app.md patches/linux/001.patch patches.md"
	if err != nil || strings.Join(attached, " ") != want {
		t.Fatalf("%v %v", attached, err)
	}
	if _, err := contextFiles(filepath.Join(first, "missing")); err == nil {
		t.Fatal("missing context directory")
	}
	if err := os.WriteFile(
		filepath.Join(g.Root, "sources", "app", "README.md"),
		[]byte("Changed description"),
		0o644,
	); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(g.Root, "_shared"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(
		filepath.Join(g.Root, "_shared", "tool"),
		[]byte("shared context"),
		0o644,
	); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(
		filepath.Join(g.Root, "other"),
		[]byte("out of scope"),
		0o644,
	); err != nil {
		t.Fatal(err)
	}
	gitRun(t, g.Root, "add", ".")
	gitRun(t, g.Root, "commit", "-qm", "update")
	gitRun(t, g.Root, "tag", "app-v2")
	g.Tag = "app-v2"
	dir := t.TempDir()
	if err := g.makeContext(ctx, dir, files, 4096); err != nil {
		t.Fatal(err)
	}
	diff, err := os.ReadFile(filepath.Join(dir, "changes.diff"))
	if err != nil || !strings.Contains(string(diff), "shared context") ||
		strings.Contains(string(diff), "out of scope") {
		t.Fatalf("%s %v", diff, err)
	}
	app, _ = os.ReadFile(filepath.Join(dir, "app.md"))
	if strings.Contains(string(app), "first release") {
		t.Fatal(string(app))
	}
	if _, err := os.Stat(filepath.Join(dir, "upstream.log")); !os.IsNotExist(err) {
		t.Fatal("unchanged upstream shouldn't produce log")
	}
}
