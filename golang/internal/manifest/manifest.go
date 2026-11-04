// Package manifest loads declarative application build recipes.
package manifest

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"path"
	"regexp"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

// Manifest describes an upstream application and its build targets.
type Manifest struct {
	Upstream Upstream `yaml:"upstream"`
	Shared   []string `yaml:"shared"`
	Release  Release  `yaml:"release"`
	Targets  []Target `yaml:"targets"`
}

// Upstream pins a git ref or a release asset, the release version and the local patch
// number.
type Upstream struct {
	URL        string        `yaml:"url"`
	Ref        string        `yaml:"ref"`
	Release    *ReleaseAsset `yaml:"release"`
	Version    string        `yaml:"version"`
	Patch      *int          `yaml:"patch"`
	PatchWidth *int          `yaml:"patch_width"`
}

// ReleaseAsset is one GitHub release asset of any owner, pinned by its SHA256.
type ReleaseAsset struct {
	Repo   string `yaml:"repo"`
	Tag    string `yaml:"tag"`
	Asset  string `yaml:"asset"`
	SHA256 string `yaml:"sha256"`
}

// Release describes destinations and the Unicode release notes limit.
type Release struct {
	Targets    []Destination `yaml:"targets"`
	NotesLimit int           `yaml:"notes_limit"`
}

// Destination is a GitHub repository or Telegram channel.
type Destination struct {
	Type string `yaml:"type"`
	Repo string `yaml:"repo"`
	Chat string `yaml:"chat"`
}

// Target is one independent matrix job.
type Target struct {
	Name      string         `yaml:"name"`
	Runner    string         `yaml:"runner"`
	Image     string         `yaml:"image"`
	Patches   []string       `yaml:"patches"`
	Downloads []ReleaseAsset `yaml:"downloads"`
	Secrets   []string       `yaml:"secrets"`
	Vars      []string       `yaml:"vars"`
	Cache     *Cache         `yaml:"cache"`
	Steps     []string       `yaml:"steps"`
	// patchesKey records an explicit patches key, even with a null value.
	patchesKey bool
}

// Cache declares exact cache paths and the source files used to hash its key.
type Cache struct {
	Paths    []string `yaml:"paths"`
	KeyFiles []string `yaml:"key_files"`
}

var (
	targetName     = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]*$`)
	commitSHA      = regexp.MustCompile(`^[0-9a-f]{40}$`)
	hexRef         = regexp.MustCompile(`^[0-9a-fA-F]{7,40}$`)
	sha256Hex      = regexp.MustCompile(`^[0-9a-f]{64}$`)
	envName        = regexp.MustCompile(`^[A-Z][A-Z0-9_]*$`)
	repoPart       = regexp.MustCompile(`^[A-Za-z0-9_.-]+$`)
	releaseTag     = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.+-]*$`)
	releaseVersion = regexp.MustCompile(
		`^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$`)
)

// IsCommitSHA reports whether ref pins a full lowercase commit SHA.
func IsCommitSHA(ref string) bool { return commitSHA.MatchString(ref) }

// PinnedRef returns the git ref or, for a release asset, its release tag.
func (u Upstream) PinnedRef() string {
	if u.Release != nil {
		return u.Release.Tag
	}
	return u.Ref
}

// Source describes where the upstream comes from in release notes.
func (u Upstream) Source() string {
	if r := u.Release; r != nil {
		return fmt.Sprintf("%s release %s asset %s", r.Repo, r.Tag, r.Asset)
	}
	return u.URL + " " + u.Ref
}

// Load reads and validates one manifest, rejecting unknown YAML fields.
func Load(file string) (*Manifest, error) {
	data, err := os.ReadFile(file)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", file, err)
	}
	return Decode(data, file)
}

// Decode validates manifest data, labeling every diagnostic with its origin.
func Decode(data []byte, file string) (*Manifest, error) {
	m := &Manifest{Release: Release{NotesLimit: 4096}}
	decoder := yaml.NewDecoder(bytes.NewReader(data))
	decoder.KnownFields(true)
	if err := decoder.Decode(m); err != nil {
		return nil, fmt.Errorf("%s: %w", file, err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return nil, fmt.Errorf("%s: expected exactly one YAML document: %v", file, err)
	}
	// Struct elements keep the null targets dropped by the strict decode in sync.
	var keys struct {
		Targets []struct {
			Patches yaml.Node `yaml:"patches"`
		} `yaml:"targets"`
	}
	if err := yaml.Unmarshal(data, &keys); err != nil {
		return nil, fmt.Errorf("%s: %w", file, err)
	}
	for i, target := range keys.Targets {
		m.Targets[i].patchesKey = target.Patches.Kind != 0
	}
	if err := m.validate(file); err != nil {
		return nil, err
	}
	return m, nil
}
func (m *Manifest) validate(file string) error {
	bad := func(field, why string) error {
		return fmt.Errorf("%s: %s: %s", file, field, why)
	}
	if err := m.validateUpstream(bad); err != nil {
		return err
	}
	if m.Release.NotesLimit <= 200 {
		return bad("release.notes_limit", "must exceed 200")
	}
	for i, shared := range m.Shared {
		if !strings.HasPrefix(shared, "_shared/") || !safeRelative(shared) {
			return bad(fmt.Sprintf("shared[%d]", i), "must be under _shared/")
		}
		if _, err := path.Match(shared, ""); err != nil {
			return bad(fmt.Sprintf("shared[%d]", i), err.Error())
		}
	}
	if len(m.Release.Targets) == 0 {
		m.Release.Targets = []Destination{{Type: "self"}}
	}
	for i, dest := range m.Release.Targets {
		field := fmt.Sprintf("release.targets[%d]", i)
		switch dest.Type {
		case "self":
		case "repo":
			if dest.Repo == "" || strings.ContainsAny(dest.Repo, "/\\\n") ||
				dest.Repo == "." || dest.Repo == ".." {
				return bad(field+".repo", "required repository name without owner")
			}
		case "telegram":
			if strings.TrimSpace(dest.Chat) == "" {
				return bad(field+".chat", "required")
			}
		default:
			return bad(field+".type", "must be self, repo or telegram")
		}
	}
	if len(m.Targets) == 0 {
		return bad("targets", "at least one required")
	}
	seen := map[string]bool{}
	for i := range m.Targets {
		t := &m.Targets[i]
		field := fmt.Sprintf("targets[%d]", i)
		if !targetName.MatchString(t.Name) {
			return bad(field+".name", "invalid target name")
		}
		if seen[t.Name] {
			return bad(field+".name", "duplicate target name")
		}
		seen[t.Name] = true
		if t.Runner == "" {
			t.Runner = "ubuntu-24.04"
		}
		if err := m.validateTarget(t, field, bad); err != nil {
			return err
		}
	}
	return nil
}
func (m *Manifest) validateUpstream(bad func(field, why string) error) error {
	if r := m.Upstream.Release; r != nil {
		if strings.TrimSpace(m.Upstream.URL) != "" || strings.TrimSpace(m.Upstream.Ref) != "" {
			return bad("upstream.release", "excludes upstream.url and upstream.ref")
		}
		if err := r.validate("upstream.release", bad); err != nil {
			return err
		}
		if m.Upstream.Version == "" {
			return bad("upstream.version", "required with upstream.release")
		}
	} else {
		if strings.TrimSpace(m.Upstream.URL) == "" {
			return bad("upstream.url", "required")
		}
		if strings.TrimSpace(m.Upstream.Ref) == "" {
			return bad("upstream.ref", "required")
		}
		switch ref := m.Upstream.Ref; {
		case IsCommitSHA(ref):
			if m.Upstream.Version == "" {
				return bad("upstream.version", "required when upstream.ref is a commit SHA")
			}
		case len(ref) == 40 && hexRef.MatchString(ref):
			return bad("upstream.ref", "commit SHA must be lowercase")
		case hexRef.MatchString(ref):
			return bad("upstream.ref", "short commit SHA, use all 40 hex characters")
		}
	}
	if m.Upstream.Version != "" && !releaseVersion.MatchString(m.Upstream.Version) {
		return bad("upstream.version", "must be X.Y.Z without v and leading zeros")
	}
	if m.Upstream.Patch == nil || *m.Upstream.Patch < 0 {
		return bad("upstream.patch", "required non-negative integer")
	}
	if width := m.Upstream.PatchWidth; width != nil {
		if *width < 1 || *width > 9 {
			return bad("upstream.patch_width", "must be from 1 to 9")
		}
		if len(strconv.Itoa(*m.Upstream.Patch)) > *width {
			return bad("upstream.patch_width", "too narrow for upstream.patch")
		}
	}
	return nil
}
func (m *Manifest) validateTarget(t *Target, field string,
	bad func(field, why string) error) error {
	if m.Upstream.Release != nil && t.patchesKey {
		return bad(field+".patches", "not allowed with upstream.release")
	}
	for _, dir := range t.Patches {
		if !safeRelative(dir) {
			return bad(field+".patches", "paths must be relative")
		}
	}
	assets := map[string]bool{}
	for i, asset := range t.Downloads {
		item := fmt.Sprintf("%s.downloads[%d]", field, i)
		if err := asset.validate(item, bad); err != nil {
			return err
		}
		if assets[asset.Asset] {
			return bad(item+".asset", "duplicate asset name")
		}
		assets[asset.Asset] = true
	}
	names := map[string]bool{}
	for _, list := range []struct {
		field string
		names []string
	}{{"secrets", t.Secrets}, {"vars", t.Vars}} {
		for i, name := range list.names {
			item := fmt.Sprintf("%s.%s[%d]", field, list.field, i)
			if !envName.MatchString(name) || strings.HasPrefix(name, "GRAVES_") ||
				strings.HasPrefix(name, "GITHUB_") {
				return bad(item, "must match [A-Z][A-Z0-9_]* without GRAVES_ or GITHUB_ prefix")
			}
			if names[name] {
				return bad(item, "duplicate name in secrets and vars")
			}
			names[name] = true
		}
	}
	if t.Cache != nil {
		if len(t.Cache.Paths) == 0 || len(t.Cache.KeyFiles) == 0 {
			return bad(field+".cache", "paths and key_files are required")
		}
		for _, glob := range t.Cache.KeyFiles {
			if !safeRelative(glob) {
				return bad(field+".cache.key_files", "relative paths required")
			}
			if _, err := path.Match(glob, ""); err != nil {
				return bad(field+".cache.key_files", err.Error())
			}
		}
	}
	if len(t.Steps) == 0 {
		return bad(field+".steps", "at least one required")
	}
	for _, step := range t.Steps {
		if strings.TrimSpace(step) == "" {
			return bad(field+".steps", "empty step")
		}
	}
	return nil
}
func (a ReleaseAsset) validate(field string, bad func(field, why string) error) error {
	// Repo and tag go unescaped into GitHub API paths, so only URL-safe names pass.
	parts := strings.Split(a.Repo, "/")
	if len(parts) != 2 {
		return bad(field+".repo", "required owner/name")
	}
	for _, part := range parts {
		if !repoPart.MatchString(part) || part == "." || part == ".." {
			return bad(field+".repo", "required owner/name")
		}
	}
	if !releaseTag.MatchString(a.Tag) {
		return bad(field+".tag", "required tag of letters, digits, '.', '_', '+' and '-'")
	}
	if a.Asset == "" || strings.ContainsAny(a.Asset, "/\\\n") ||
		a.Asset == "." || a.Asset == ".." {
		return bad(field+".asset", "required file name without path separators")
	}
	if !sha256Hex.MatchString(a.SHA256) {
		return bad(field+".sha256", "must be 64 lowercase hex characters")
	}
	return nil
}
func safeRelative(s string) bool {
	return s != "" && !path.IsAbs(s) && !strings.Contains(s, "\\") &&
		s != "." && s != ".." && !strings.HasPrefix(s, "../") &&
		!strings.Contains(s, "/../") && !strings.HasSuffix(s, "/..")
}

// TargetByName finds a configured target.
func (m *Manifest) TargetByName(name string) (*Target, error) {
	for i := range m.Targets {
		if m.Targets[i].Name == name {
			return &m.Targets[i], nil
		}
	}
	return nil, fmt.Errorf("unknown target %q", name)
}
