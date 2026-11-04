// cspell:ignore pipefail
package builder

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	ci "github.com/megalomania428/go-lib-ci"
	"github.com/megalomania428/update-graves/golang/internal/manifest"
)

func testBuilder(t *testing.T) *Builder {
	t.Helper()
	root := t.TempDir()
	target := &manifest.Target{
		Name: "linux",
		Steps: []string{
			"printf '%s' \"$GRAVES_NAME:$GRAVES_VERSION:$GRAVES_RUN\" " +
				">\"$GRAVES_OUT_DIR/app-$GRAVES_FILE_VERSION.tgz\"",
		},
	}
	patch := 0
	b := &Builder{Root: root, Name: "app", Mode: "draft", Target: target,
		Manifest: &manifest.Manifest{
			Upstream: manifest.Upstream{URL: "local", Ref: "v1.2.3", Patch: &patch},
		}}
	for _, dir := range []string{b.SourceDir(), b.WorkDir("out")} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return b
}
func TestVersion(t *testing.T) {
	t.Setenv("GITHUB_RUN_NUMBER", "42")
	t.Setenv("GITHUB_RUN_ATTEMPT", "1")
	patch := 3
	m := &manifest.Manifest{Upstream: manifest.Upstream{Ref: "v1.15.13", Patch: &patch}}
	if got := Version("release", m); got != "1.15.13-p3" {
		t.Fatal(got)
	}
	if got := Version("draft", m); got != "1.15.13-p3.dev.42.1" {
		t.Fatal(got)
	}
	if got := Version("release", &manifest.Manifest{}); got != "-p0" {
		t.Fatal(got)
	}
	width := 3
	m.Upstream = manifest.Upstream{Ref: strings.Repeat("a", 40), Version: "3.3.0",
		Patch: &patch, PatchWidth: &width}
	if got := Version("release", m); got != "3.3.0-p003" {
		t.Fatal(got)
	}
	if got := Version("draft", m); got != "3.3.0-p003.dev.42.1" {
		t.Fatal(got)
	}
}
func TestFileVersion(t *testing.T) {
	if got := FileVersion("1.15.13-p0.dev.42.1"); got != "1_15_13-p0_dev_42_1" {
		t.Fatal(got)
	}
	if got := FileVersion("release/1.2.3-p0"); got != "release_1_2_3-p0" {
		t.Fatal(got)
	}
}
func TestBuilder_Build(t *testing.T) {
	b := testBuilder(t)
	b.Tag = "app-v-stale"
	if strings.Contains(strings.Join(b.Environment(), "\n"), "GRAVES_TAG=app-v-stale") {
		t.Fatal("draft steps inherited a stale release tag")
	}
	t.Setenv("GITHUB_RUN_NUMBER", "")
	t.Setenv("GITHUB_RUN_ATTEMPT", "")
	if err := b.Build(context.Background()); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(b.WorkDir("out"),
		"app-1_2_3-p0_dev_0_0.tgz"))
	if err != nil || string(data) != "app:1.2.3-p0.dev.0.0:0.0" {
		t.Fatalf("%q %v", data, err)
	}
	b.Manifest.Upstream.Ref = "release/1.2.3"
	if err := b.Build(context.Background()); err != nil {
		t.Fatal(err)
	}
	data, err = os.ReadFile(filepath.Join(b.WorkDir("out"),
		"app-release_1_2_3-p0_dev_0_0.tgz"))
	if err != nil || string(data) != "app:release/1.2.3-p0.dev.0.0:0.0" {
		t.Fatalf("%q %v", data, err)
	}
	b.Manifest.Upstream.Ref = "v1.2.3"
	b.Target.Steps = []string{"exit 7", "touch never"}
	if err := b.Build(context.Background()); err == nil {
		t.Fatal("step failure")
	}
	if _, err := os.Stat(filepath.Join(b.SourceDir(), "never")); !os.IsNotExist(err) {
		t.Fatal("ran after failure")
	}
	b.Mode, b.Tag = "release", "app-v2.3.4p5"
	t.Setenv("GITHUB_RUN_NUMBER", "19")
	t.Setenv("GITHUB_RUN_ATTEMPT", "2")
	env := strings.Join(b.Environment(), "\n")
	for _, want := range []string{
		"GRAVES_VERSION=1.2.3-p0",
		"GRAVES_FILE_VERSION=1_2_3-p0",
		"GRAVES_RUN=19.2",
		"GRAVES_SOURCE_DIR=" + b.SourceDir(),
		"GRAVES_UPSTREAM_DIR=" + b.WorkDir("upstream"),
		"GRAVES_DOWNLOADS_DIR=" + b.WorkDir("downloads"),
		"GRAVES_TARGET=linux",
	} {
		if !strings.Contains(env, want) {
			t.Fatal(env)
		}
	}
	if strings.Contains(env, "GRAVES_UPSTREAM_ASSET") {
		t.Fatal("git upstream has no asset")
	}
	t.Setenv("HTTPS_PROXY", "http://proxy.test")
	if !strings.Contains(
		strings.Join(ProxyEnvironment(), "\n"),
		"HTTPS_PROXY=http://proxy.test",
	) {
		t.Fatal("proxy")
	}
}
func TestBuilder_EnvironmentRelease(t *testing.T) {
	b := testBuilder(t)
	patch := 1
	b.Manifest.Upstream = manifest.Upstream{
		Release: &manifest.ReleaseAsset{Repo: "owner/sources", Tag: "sources-app",
			Asset: "app.apk", SHA256: strings.Repeat("a", 64)},
		Version: "1.2.3", Patch: &patch,
	}
	env := b.Environment()
	for _, want := range []string{
		"GRAVES_UPSTREAM_REF=sources-app",
		"GRAVES_UPSTREAM_ASSET=" + filepath.Join(b.Root, ".graves", "upstream", "app.apk"),
		"GRAVES_UPSTREAM_DIR=" + filepath.Join(b.Root, ".graves", "upstream"),
		"GRAVES_DOWNLOADS_DIR=" + filepath.Join(b.Root, ".graves", "downloads"),
	} {
		if !slices.Contains(env, want) {
			t.Fatalf("missing %s in %v", want, env)
		}
	}
}
func TestBuilder_BuildDeclared(t *testing.T) {
	t.Setenv("GITHUB_RUN_NUMBER", "")
	t.Setenv("GITHUB_RUN_ATTEMPT", "")
	for _, tt := range []struct{ name, key, cert, want string }{
		{"empty secret", "", "cert", "declared TEST_SIGN_KEY is empty"},
		{"empty variable", "key", "", "declared TEST_SIGN_CERT is empty"},
		{"both set", "key", "cert", ""},
	} {
		t.Run(tt.name, func(t *testing.T) {
			b := testBuilder(t)
			b.Target.Secrets = []string{"TEST_SIGN_KEY"}
			b.Target.Vars = []string{"TEST_SIGN_CERT"}
			t.Setenv("TEST_SIGN_KEY", tt.key)
			t.Setenv("TEST_SIGN_CERT", tt.cert)
			b.Target.Steps = []string{
				"touch marker",
				"printf '%s:%s' \"$TEST_SIGN_KEY\" \"$TEST_SIGN_CERT\" " +
					">\"$GRAVES_OUT_DIR/app-$GRAVES_FILE_VERSION.tgz\"",
			}
			err := b.Build(context.Background())
			_, statErr := os.Stat(filepath.Join(b.SourceDir(), "marker"))
			if tt.want != "" {
				if err == nil || !strings.Contains(err.Error(), tt.want) ||
					!os.IsNotExist(statErr) {
					t.Fatalf("%v %v", err, statErr)
				}
				return
			}
			data, readErr := os.ReadFile(filepath.Join(b.WorkDir("out"),
				"app-1_2_3-p0_dev_0_0.tgz"))
			if err != nil || readErr != nil || string(data) != "key:cert" {
				t.Fatalf("%q %v %v", data, err, readErr)
			}
		})
	}
}
func TestBuilder_Package(t *testing.T) {
	for _, tt := range []struct {
		name, want string
		configure  func(*Builder)
	}{
		{"empty", "no assets in", func(_ *Builder) {}},
		{"directory", "only regular files are published", func(b *Builder) {
			if err := os.Mkdir(filepath.Join(b.WorkDir("out"), "app"), 0o755); err != nil {
				t.Fatal(err)
			}
		}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			b := testBuilder(t)
			tt.configure(b)
			err := b.Package()
			if err == nil || !strings.Contains(err.Error(), tt.want) {
				t.Fatalf("expected %q: %v", tt.want, err)
			}
		})
	}
	b := testBuilder(t)
	writeFile(t, filepath.Join(b.WorkDir("out"), "app-1_2_3-p0_dev_0_0.tgz"), "payload")
	writeFile(t, filepath.Join(b.WorkDir("out"), "unversioned.tgz"), "payload")
	if err := b.Package(); err != nil {
		t.Fatal(err)
	}
}
func TestBuilderContainerCommand(t *testing.T) {
	b := testBuilder(t)
	b.Target.Image = "test-image"
	b.Target.Secrets = []string{"TEST_SIGN_KEY"}
	t.Setenv("TEST_SIGN_KEY", "secret-value")
	b.Target.Steps = append(b.Target.Steps,
		"printf '%s' \"$TEST_SIGN_KEY\" >\"$GRAVES_OUT_DIR/key.txt\"")
	bin := t.TempDir()
	log := filepath.Join(t.TempDir(), "podman-args")
	t.Setenv("FAKE_PODMAN_LOG", log)
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	script := "#!/usr/bin/env bash\nset -euo pipefail\n" +
		"printf '%s\\n' \"$@\" >\"$FAKE_PODMAN_LOG\"\n" +
		"while [[ $1 != test-image ]]; do shift; done\nshift\nexec \"$@\"\n"
	if err := os.WriteFile(
		filepath.Join(bin, "podman"),
		[]byte(script),
		0o755,
	); err != nil {
		t.Fatal(err)
	}
	original := ensurePackages
	ensurePackages = func(context.Context, []string, ci.EnsurePackagesOptions) error {
		return nil
	}
	t.Cleanup(func() { ensurePackages = original })
	if err := b.Build(context.Background()); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	for _, arg := range []string{b.Root + ":" + b.Root, b.SourceDir(),
		"GRAVES_UPSTREAM_DIR=" + b.WorkDir("upstream"), "--network\nhost"} {
		if !strings.Contains(string(data), arg) {
			t.Fatalf("missing container argument %s", arg)
		}
	}
	if !strings.Contains(string(data), "-e\nTEST_SIGN_KEY\n") ||
		strings.Contains(string(data), "secret-value") {
		t.Fatalf("declared secret must be passed by name only: %s", data)
	}
	key, err := os.ReadFile(filepath.Join(b.WorkDir("out"), "key.txt"))
	if err != nil || string(key) != "secret-value" {
		t.Fatalf("%q %v", key, err)
	}
}
func writeFile(t *testing.T, name, data string) {
	t.Helper()
	if err := os.WriteFile(name, []byte(data), 0o644); err != nil {
		t.Fatal(err)
	}
}
func sha256Hex(data string) string {
	sum := sha256.Sum256([]byte(data))
	return hex.EncodeToString(sum[:])
}
func TestBuilder_FetchRelease(t *testing.T) {
	const apk, jar = "apk payload", "jar payload"
	bodies := map[string]string{"app.apk": apk, "tool.jar": jar}
	releases := map[string]string{
		"/repos/owner/sources/releases/tags/sources-app": "app.apk",
		"/repos/owner/tools/releases/tags/v1.0.0":        "tool.jar",
	}
	original := fetchRelease
	var calls []ci.FetchOptions
	fetchRelease = func(ctx context.Context, opts ci.FetchOptions) (string, error) {
		calls = append(calls, opts)
		opts.MaxRetriesTime, opts.StallTimeout, opts.NoProgressBar = -1, -1, true
		return original(ctx, opts)
	}
	t.Cleanup(func() { fetchRelease = original })
	for _, tt := range []struct{ name, token, pin, digest, hide, want string }{
		{name: "token", token: "token"},
		{name: "anonymous"},
		{name: "recipe sha256", pin: strings.Repeat("0", 64), want: "recipe pins"},
		{name: "API digest", digest: strings.Repeat("0", 64), want: "digest mismatch"},
		{name: "missing asset", hide: "tool.jar", want: "not found"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			calls = nil
			auth := map[string]string{}
			var server *httptest.Server
			server = httptest.NewServer(http.HandlerFunc(
				func(w http.ResponseWriter, r *http.Request) {
					auth[r.URL.Path] = r.Header.Get("Authorization")
					if name, ok := releases[r.URL.Path]; ok {
						digest := sha256Hex(bodies[name])
						if name == "app.apk" && tt.digest != "" {
							digest = tt.digest
						}
						assets := fmt.Sprintf(`[{"name":%q,"url":"%s/download/%s",`+
							`"digest":"sha256:%s"}]`, name, server.URL, name, digest)
						if name == tt.hide {
							assets = "[]"
						}
						_, _ = fmt.Fprintf(w, `{"tag_name":"t","assets":%s}`, assets)
						return
					}
					name, ok := strings.CutPrefix(r.URL.Path, "/download/")
					if !ok || r.Header.Get("Accept") != "application/octet-stream" {
						http.NotFound(w, r)
						return
					}
					_, _ = io.WriteString(w, bodies[name])
				}))
			t.Cleanup(server.Close)
			t.Setenv("GITHUB_API_URL", server.URL)
			t.Setenv("RELEASES_TOKEN", tt.token)
			b := testBuilder(t)
			pin, patch := sha256Hex(apk), 1
			if tt.pin != "" {
				pin = tt.pin
			}
			b.Manifest.Upstream = manifest.Upstream{
				Release: &manifest.ReleaseAsset{Repo: "owner/sources", Tag: "sources-app",
					Asset: "app.apk", SHA256: pin},
				Version: "1.2.3", Patch: &patch,
			}
			b.Target.Downloads = []manifest.ReleaseAsset{{Repo: "owner/tools", Tag: "v1.0.0",
				Asset: "tool.jar", SHA256: sha256Hex(jar)}}
			stale := []string{
				filepath.Join(b.WorkDir("upstream"), "stale"),
				filepath.Join(b.WorkDir("downloads"), "stale"),
			}
			for _, file := range stale {
				if err := os.MkdirAll(filepath.Dir(file), 0o755); err != nil {
					t.Fatal(err)
				}
				writeFile(t, file, "old")
			}
			err := b.Fetch(context.Background())
			upstreamFile := filepath.Join(b.WorkDir("upstream"), "app.apk")
			if tt.want != "" {
				if err == nil || !strings.Contains(err.Error(), tt.want) {
					t.Fatalf("expected %q: %v", tt.want, err)
				}
				if _, err := os.Stat(upstreamFile); tt.hide == "" && !os.IsNotExist(err) {
					t.Fatal("rejected asset was kept")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			for file, want := range map[string]string{
				upstreamFile: apk,
				filepath.Join(b.WorkDir("downloads"), "tool.jar"): jar,
			} {
				data, err := os.ReadFile(file)
				info, statErr := os.Stat(file)
				if err != nil || statErr != nil || string(data) != want ||
					info.Mode().Perm() != 0o644 {
					t.Fatalf("%s: %q %v %v", file, data, err, statErr)
				}
			}
			for _, file := range stale {
				if _, err := os.Stat(file); !os.IsNotExist(err) {
					t.Fatalf("stale %s survived", file)
				}
			}
			if b.Pin != sha256Hex(apk) {
				t.Fatal(b.Pin)
			}
			wantAuth := ""
			if tt.token != "" {
				wantAuth = "Bearer " + tt.token
			}
			for _, path := range []string{
				"/repos/owner/sources/releases/tags/sources-app", "/download/app.apk",
				"/repos/owner/tools/releases/tags/v1.0.0", "/download/tool.jar",
			} {
				if got, ok := auth[path]; !ok || got != wantAuth {
					t.Fatalf("%s: authorization %q, expected %q", path, got, wantAuth)
				}
			}
			if len(calls) != 2 || calls[0].AssetName != "app.apk" ||
				calls[1].AssetName != "tool.jar" {
				t.Fatalf("%+v", calls)
			}
			for _, call := range calls {
				if call.CIMode == nil || !*call.CIMode {
					t.Fatalf("download without CI mode: %+v", call)
				}
			}
		})
	}
}
func TestBuilder_Fetch(t *testing.T) {
	b := testBuilder(t)
	upstream := t.TempDir()
	ctx := context.Background()
	for _, args := range [][]string{
		{"init", "-q", "-b", "master"},
		{"add", "."},
		{
			"-c",
			"user.name=Test",
			"-c",
			"user.email=test@example.test",
			"commit",
			"--allow-empty",
			"-qm",
			"initial",
		},
	} {
		if args[0] == "add" {
			writeFile(t, filepath.Join(upstream, "file"), "old\n")
		}
		if err := ci.RunCommand(
			ctx,
			ci.WithCommand("git", args...),
			ci.WithCommandDir(upstream),
		); err != nil {
			t.Fatal(err)
		}
	}
	b.Manifest.Upstream = manifest.Upstream{URL: upstream, Ref: "master"}
	if err := os.MkdirAll(b.WorkDir("downloads"), 0o755); err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(b.WorkDir("downloads"), "stale"), "old")
	if err := b.Fetch(ctx); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(b.WorkDir("downloads"))
	if err != nil || len(entries) != 0 {
		t.Fatalf("%v %v", entries, err)
	}
	clone, err := ci.CommandOutput(ctx, ci.WithCommand("git", "rev-parse", "HEAD"),
		ci.WithCommandDir(b.WorkDir("upstream")))
	if err != nil || b.Pin == "" || b.Pin != strings.TrimSpace(clone) {
		t.Fatalf("%q %q %v", b.Pin, clone, err)
	}
	patches := filepath.Join(b.SourceDir(), "patches")
	if err := os.Mkdir(patches, 0o755); err != nil {
		t.Fatal(err)
	}
	writeFile(
		t,
		filepath.Join(patches, "001.patch"),
		"--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new\n",
	)
	b.Target.Patches = []string{"patches"}
	if err := b.Fetch(ctx); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(b.WorkDir("upstream"), "file"))
	if err != nil || string(data) != "new\n" {
		t.Fatalf("%q %v", data, err)
	}
	b.Target.Patches = []string{"missing"}
	if err := b.Fetch(ctx); err == nil {
		t.Fatal("missing patches")
	}
	sha, err := ci.CommandOutput(ctx, ci.WithCommand("git", "rev-parse", "HEAD"),
		ci.WithCommandDir(upstream))
	if err != nil {
		t.Fatal(err)
	}
	b.Target.Patches = []string{"patches"}
	b.Manifest.Upstream = manifest.Upstream{URL: upstream, Ref: strings.TrimSpace(sha),
		Version: "1.2.3"}
	if err := b.Fetch(ctx); err != nil {
		t.Fatal(err)
	}
	head, err := ci.CommandOutput(ctx, ci.WithCommand("git", "rev-parse", "HEAD"),
		ci.WithCommandDir(b.WorkDir("upstream")))
	if err != nil || head != sha || b.Pin != strings.TrimSpace(head) {
		t.Fatalf("%q %q %v", head, b.Pin, err)
	}
	data, err = os.ReadFile(filepath.Join(b.WorkDir("upstream"), "file"))
	if err != nil || string(data) != "new\n" {
		t.Fatalf("%q %v", data, err)
	}
	b.Manifest.Upstream.Ref = strings.Repeat("0", 40)
	if err := b.Fetch(ctx); err == nil {
		t.Fatal("missing commit")
	}
}
