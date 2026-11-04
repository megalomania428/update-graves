// cspell:ignore pipefail

// Package builder fetches upstream sources, executes steps and checks assets.
package builder

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	ci "github.com/megalomania428/go-lib-ci"
	"github.com/megalomania428/update-graves/golang/internal/manifest"
)

var (
	ensurePackages = ci.EnsurePackages
	fetchRelease   = ci.FetchGitHubRelease
)

// Builder holds the settings shared by fetch and build in a matrix job.
type Builder struct {
	Root, Name, Mode, Tag string
	Manifest              *manifest.Manifest
	Target                *manifest.Target
	// Pin is the upstream commit SHA or the release asset SHA256 resolved by Fetch.
	Pin string
}

// SourceDir returns the absolute recipe directory.
func (b *Builder) SourceDir() string { return filepath.Join(b.Root, "sources", b.Name) }

// WorkDir returns an absolute build workspace path.
func (b *Builder) WorkDir(name string) string {
	return filepath.Join(b.Root, ".graves", name)
}

// Version builds the recipe version and marks drafts with the builder run.
func Version(mode string, m *manifest.Manifest) string {
	patch, width := 0, 0
	if m.Upstream.Patch != nil {
		patch = *m.Upstream.Patch
	}
	if m.Upstream.PatchWidth != nil {
		width = *m.Upstream.PatchWidth
	}
	base := strings.TrimPrefix(m.Upstream.Ref, "v")
	if m.Upstream.Version != "" {
		base = m.Upstream.Version
	}
	version := base + fmt.Sprintf("-p%0*d", width, patch)
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

// Fetch creates a clean upstream clone or asset, applies only the selected target's
// patches, downloads its assets and records the pin of the upstream in Pin.
func (b *Builder) Fetch(ctx context.Context) error {
	for _, name := range []string{"upstream", "downloads", "out"} {
		if err := os.RemoveAll(b.WorkDir(name)); err != nil {
			return err
		}
		if name != "upstream" {
			if err := os.MkdirAll(b.WorkDir(name), 0o755); err != nil {
				return err
			}
		}
	}
	upstream := b.Manifest.Upstream
	if upstream.Release != nil {
		if err := download(ctx, *upstream.Release, b.WorkDir("upstream")); err != nil {
			return err
		}
		b.Pin = upstream.Release.SHA256
	} else if err := b.fetchGit(ctx); err != nil {
		return err
	}
	for _, asset := range b.Target.Downloads {
		if err := download(ctx, asset, b.WorkDir("downloads")); err != nil {
			return err
		}
	}
	return nil
}

// fetchGit clones the git upstream, applies the target's patches and pins HEAD.
func (b *Builder) fetchGit(ctx context.Context) error {
	upstream, dir := b.Manifest.Upstream, b.WorkDir("upstream")
	if manifest.IsCommitSHA(upstream.Ref) {
		if err := fetchCommit(ctx, upstream.URL, upstream.Ref, dir); err != nil {
			return err
		}
	} else if err := ci.GitClone(ctx, ci.WithGitURL(upstream.URL),
		ci.WithGitRef(upstream.Ref), ci.WithGitDepth(1), ci.WithGitDir(dir)); err != nil {
		return err
	}
	var dirs []string
	for _, patch := range b.Target.Patches {
		dirs = append(dirs, filepath.Join(b.SourceDir(), patch))
	}
	if len(dirs) != 0 {
		_, err := ci.ApplyPatches(ctx, ci.WithPatchTarget(dir), ci.WithPatchDirs(dirs...))
		if err != nil {
			return err
		}
	}
	head, err := ci.CommandOutput(ctx, ci.WithCommand("git", "rev-parse", "HEAD"),
		ci.WithCommandDir(dir))
	if err != nil {
		return err
	}
	b.Pin = strings.TrimSpace(head)
	return nil
}

// download fetches one release asset into dir and checks the recipe SHA256 after the
// API digest checked by the library; a mismatching file is removed.
func download(ctx context.Context, asset manifest.ReleaseAsset, dir string) error {
	ciMode := true
	file, err := fetchRelease(ctx, ci.FetchOptions{Repo: asset.Repo, Tag: asset.Tag,
		AssetName: asset.Asset, DestDir: dir, FileMode: 0o644,
		Token: os.Getenv("RELEASES_TOKEN"), APIBaseURL: os.Getenv("GITHUB_API_URL"),
		CIMode: &ciMode})
	if err != nil {
		return fmt.Errorf("%s release %s asset %s: %w", asset.Repo, asset.Tag, asset.Asset,
			err)
	}
	sum, err := fileSHA256(file)
	if err != nil {
		return err
	}
	if sum != asset.SHA256 {
		if err := os.Remove(file); err != nil {
			return err
		}
		return fmt.Errorf("%s release %s asset %s: sha256 %s, recipe pins %s",
			asset.Repo, asset.Tag, asset.Asset, sum, asset.SHA256)
	}
	fmt.Printf("downloaded %s, sha256 %s\n", file, sum)
	return nil
}
func fileSHA256(file string) (string, error) {
	f, err := os.Open(file)
	if err != nil {
		return "", err
	}
	defer func() { _ = f.Close() }()
	hash := sha256.New()
	if _, err := io.Copy(hash, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

// GitInitFetch initializes dir with an origin remote and fetches args from it, because
// ci.GitClone always runs git clone --branch, which cannot fetch a commit SHA.
func GitInitFetch(ctx context.Context, url, dir string, bare bool, args ...string) error {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	initArgs := []string{"init", "-q"}
	if bare {
		initArgs = append(initArgs, "--bare")
	}
	for _, command := range [][]string{initArgs, {"remote", "add", "origin", url},
		append([]string{"fetch", "-q"}, args...)} {
		if err := ci.RunCommand(ctx, ci.WithCommand("git", command...),
			ci.WithCommandDir(dir)); err != nil {
			return err
		}
	}
	return nil
}

// fetchCommit checks out one pinned commit SHA into dir and verifies the result.
func fetchCommit(ctx context.Context, url, sha, dir string) error {
	if err := GitInitFetch(ctx, url, dir, false, "--depth", "1", "origin", sha); err != nil {
		return err
	}
	if err := ci.RunCommand(ctx, ci.WithCommand("git", "checkout", "-q", "--detach",
		"FETCH_HEAD"), ci.WithCommandDir(dir)); err != nil {
		return err
	}
	head, err := ci.CommandOutput(ctx, ci.WithCommand("git", "rev-parse", "HEAD"),
		ci.WithCommandDir(dir))
	if err != nil {
		return err
	}
	if head = strings.TrimSpace(head); head != sha {
		return fmt.Errorf("fetched %s instead of %s", head, sha)
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
	env := []string{"GRAVES_NAME=" + b.Name, "GRAVES_TARGET=" + b.Target.Name,
		"GRAVES_SOURCE_DIR=" + b.SourceDir(), "GRAVES_UPSTREAM_DIR=" + b.WorkDir("upstream"),
		"GRAVES_DOWNLOADS_DIR=" + b.WorkDir("downloads"), "GRAVES_OUT_DIR=" + b.WorkDir("out"),
		"GRAVES_UPSTREAM_REF=" + b.Manifest.Upstream.PinnedRef(),
		"GRAVES_MODE=" + b.Mode, "GRAVES_TAG=" + tag,
		"GRAVES_VERSION=" + version, "GRAVES_FILE_VERSION=" + FileVersion(version),
		"GRAVES_RUN=" + Run()}
	if r := b.Manifest.Upstream.Release; r != nil {
		env = append(env, "GRAVES_UPSTREAM_ASSET="+filepath.Join(b.WorkDir("upstream"),
			r.Asset))
	}
	return env
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

// declared returns the names of the target's secrets and variables, which must be
// non-empty in the builder environment.
func (b *Builder) declared() ([]string, error) {
	names := append(append([]string(nil), b.Target.Secrets...), b.Target.Vars...)
	for _, name := range names {
		if os.Getenv(name) == "" {
			return nil, fmt.Errorf("%s/%s: declared %s is empty", b.Name, b.Target.Name,
				name)
		}
	}
	return names, nil
}

// Build executes every recipe step and only then checks the produced assets.
func (b *Builder) Build(ctx context.Context) error {
	declared, err := b.declared()
	if err != nil {
		return err
	}
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
			// Names only, so podman copies the values without exposing them in arguments.
			for _, env := range declared {
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
