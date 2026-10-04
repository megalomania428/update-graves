// cspell:ignore pipefail

// Package builder fetches upstream sources, executes steps and checks assets.
package builder

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	ci "github.com/megalomania428/go-lib-ci"
	"github.com/megalomania428/update-graves/golang/internal/manifest"
)

var ensurePackages = ci.EnsurePackages

// Builder holds the settings shared by fetch and build in a matrix job.
type Builder struct {
	Root, Name, Mode, Tag string
	Manifest              *manifest.Manifest
	Target                *manifest.Target
}

// SourceDir returns the absolute recipe directory.
func (b *Builder) SourceDir() string { return filepath.Join(b.Root, "sources", b.Name) }

// WorkDir returns an absolute build workspace path.
func (b *Builder) WorkDir(name string) string {
	return filepath.Join(b.Root, ".graves", name)
}

// Version builds the recipe version and marks drafts with the builder run.
func Version(mode string, m *manifest.Manifest) string {
	patch := 0
	if m.Upstream.Patch != nil {
		patch = *m.Upstream.Patch
	}
	version := strings.TrimPrefix(m.Upstream.Ref, "v") + "-p" + strconv.Itoa(patch)
	if mode != "release" {
		version += ".dev." + Run()
	}
	return version
}

// Run returns the run and attempt numbers of the current builder job.
func Run() string {
	return ci.EnvDefault("GITHUB_RUN_NUMBER", "0") + "." +
		ci.EnvDefault("GITHUB_RUN_ATTEMPT", "0")
}

// FileVersion makes a version, including branch refs with slashes, safe for file names.
func FileVersion(version string) string {
	return strings.NewReplacer(".", "_", "/", "_").Replace(version)
}

// Fetch creates a clean clone and applies only the selected target's patches.
func (b *Builder) Fetch(ctx context.Context) error {
	for _, name := range []string{"upstream", "out"} {
		if err := os.RemoveAll(b.WorkDir(name)); err != nil {
			return err
		}
		if name != "upstream" {
			if err := os.MkdirAll(b.WorkDir(name), 0o755); err != nil {
				return err
			}
		}
	}
	if err := ci.GitClone(ctx, ci.WithGitURL(b.Manifest.Upstream.URL),
		ci.WithGitRef(b.Manifest.Upstream.Ref), ci.WithGitDepth(1),
		ci.WithGitDir(b.WorkDir("upstream"))); err != nil {
		return err
	}
	var dirs []string
	for _, patch := range b.Target.Patches {
		dirs = append(dirs, filepath.Join(b.SourceDir(), patch))
	}
	if len(dirs) != 0 {
		_, err := ci.ApplyPatches(ctx, ci.WithPatchTarget(b.WorkDir("upstream")),
			ci.WithPatchDirs(dirs...))
		return err
	}
	return nil
}

// Environment returns the GRAVES_* environment of every build step.
func (b *Builder) Environment() []string {
	tag := b.Tag
	if b.Mode == "draft" {
		tag = ""
	}
	version := Version(b.Mode, b.Manifest)
	return []string{"GRAVES_NAME=" + b.Name, "GRAVES_TARGET=" + b.Target.Name,
		"GRAVES_SOURCE_DIR=" + b.SourceDir(), "GRAVES_UPSTREAM_DIR=" + b.WorkDir("upstream"),
		"GRAVES_OUT_DIR=" + b.WorkDir("out"),
		"GRAVES_UPSTREAM_REF=" + b.Manifest.Upstream.Ref,
		"GRAVES_MODE=" + b.Mode, "GRAVES_TAG=" + tag,
		"GRAVES_VERSION=" + version, "GRAVES_FILE_VERSION=" + FileVersion(version),
		"GRAVES_RUN=" + Run()}
}

// ProxyEnvironment lists the configured proxy variables forwarded to containers.
func ProxyEnvironment() []string {
	var env []string
	for _, key := range []string{"HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy",
		"SOCKS_PROXY", "socks_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"} {
		if value := os.Getenv(key); value != "" {
			env = append(env, key+"="+value)
		}
	}
	return env
}

// Build executes every recipe step and only then checks the produced assets.
func (b *Builder) Build(ctx context.Context) error {
	if b.Target.Image != "" {
		err := ensurePackages(ctx, []string{"podman"}, ci.EnsurePackagesOptions{})
		if err != nil {
			return err
		}
	}
	for i, step := range b.Target.Steps {
		name, args := "bash", []string{"-euo", "pipefail", "-c", step}
		if b.Target.Image != "" {
			name = "podman"
			args = []string{"run", "--rm", "--network", "host", "-v", b.Root + ":" + b.Root,
				"-w", b.SourceDir()}
			for _, env := range append(b.Environment(), ProxyEnvironment()...) {
				args = append(args, "-e", env)
			}
			args = append(args, b.Target.Image, "bash", "-euo", "pipefail", "-c", step)
		}
		err := ci.RunCommand(ctx, ci.WithCommand(name, args...),
			ci.WithCommandDir(b.SourceDir()), ci.WithCommandEnv(b.Environment()...))
		if err != nil {
			return fmt.Errorf("%s/%s step %d: %w", b.Name, b.Target.Name, i+1, err)
		}
	}
	return b.Package()
}

// Package publishes nothing itself: it only validates what the steps produced.
func (b *Builder) Package() error {
	dir := b.WorkDir("out")
	entries, err := os.ReadDir(dir)
	if err != nil {
		return err
	}
	version := FileVersion(Version(b.Mode, b.Manifest))
	var names []string
	for _, entry := range entries {
		info, err := entry.Info()
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("asset %s: only regular files are published", entry.Name())
		}
		if !strings.Contains(entry.Name(), version) {
			fmt.Fprintf(os.Stderr, "graves: asset %s misses version %s in its name\n",
				entry.Name(), version)
		}
		names = append(names, entry.Name())
	}
	if len(names) == 0 {
		return fmt.Errorf("no assets in %s", dir)
	}
	fmt.Printf("assets: %s\n", strings.Join(names, " "))
	return nil
}
