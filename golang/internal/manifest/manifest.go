// Package manifest loads declarative application build recipes.
package manifest

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"path"
	"regexp"
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

// Upstream pins the cloned ref and the local patch number of the recipe.
type Upstream struct {
	URL   string `yaml:"url"`
	Ref   string `yaml:"ref"`
	Patch *int   `yaml:"patch"`
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
	Name    string   `yaml:"name"`
	Runner  string   `yaml:"runner"`
	Image   string   `yaml:"image"`
	Patches []string `yaml:"patches"`
	Cache   *Cache   `yaml:"cache"`
	Steps   []string `yaml:"steps"`
}

// Cache declares exact cache paths and the source files used to hash its key.
type Cache struct {
	Paths    []string `yaml:"paths"`
	KeyFiles []string `yaml:"key_files"`
}

var targetName = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]*$`)

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
	if err := m.validate(file); err != nil {
		return nil, err
	}
	return m, nil
}
func (m *Manifest) validate(file string) error {
	bad := func(field, why string) error {
		return fmt.Errorf("%s: %s: %s", file, field, why)
	}
	if strings.TrimSpace(m.Upstream.URL) == "" {
		return bad("upstream.url", "required")
	}
	if strings.TrimSpace(m.Upstream.Ref) == "" {
		return bad("upstream.ref", "required")
	}
	if m.Upstream.Patch == nil || *m.Upstream.Patch < 0 {
		return bad("upstream.patch", "required non-negative integer")
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
		for _, dir := range t.Patches {
			if !safeRelative(dir) {
				return bad(field+".patches", "paths must be relative")
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
