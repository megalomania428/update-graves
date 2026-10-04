// cspell:ignore pipefail
package builder

import (
	"context"
	"os"
	"path/filepath"
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
		"GRAVES_TARGET=linux",
	} {
		if !strings.Contains(env, want) {
			t.Fatal(env)
		}
	}
	t.Setenv("HTTPS_PROXY", "http://proxy.test")
	if !strings.Contains(
		strings.Join(ProxyEnvironment(), "\n"),
		"HTTPS_PROXY=http://proxy.test",
	) {
		t.Fatal("proxy")
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
}
func writeFile(t *testing.T, name, data string) {
	t.Helper()
	if err := os.WriteFile(name, []byte(data), 0o644); err != nil {
		t.Fatal(err)
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
	if err := b.Fetch(ctx); err != nil {
		t.Fatal(err)
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
}
