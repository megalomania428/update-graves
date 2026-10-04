// Package selector chooses applications and expands their build matrix.
package selector

import (
	"crypto/sha256"
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"

	"github.com/megalomania428/update-graves/golang/internal/manifest"
	"gopkg.in/yaml.v3"
)

// Entry is one application and target pair in the Actions matrix.
type Entry struct {
	Source     string `json:"source"`
	Target     string `json:"target"`
	Runner     string `json:"runner"`
	CacheKey   string `json:"cache_key"`
	CachePaths string `json:"cache_paths"`
}

// Matrix is serialized directly as a GitHub Actions include matrix.
type Matrix struct {
	Include []Entry `json:"include"`
}

// Discover returns existing source directories that contain build.yaml.
func Discover(root string) ([]string, error) {
	entries, err := os.ReadDir(filepath.Join(root, "sources"))
	if err != nil {
		return nil, fmt.Errorf("discover sources: %w", err)
	}
	var names []string
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		info, err := os.Stat(filepath.Join(root, "sources", e.Name(), "build.yaml"))
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return nil, err
		}
		if info.Mode().IsRegular() {
			names = append(names, e.Name())
		}
	}
	return names, nil
}

// FromTag chooses the longest existing source prefix followed by a dash.
func FromTag(tag string, names []string) (string, error) {
	found := ""
	for _, name := range names {
		if strings.HasPrefix(tag, name+"-") && len(name) > len(found) {
			found = name
		}
	}
	if found == "" {
		return "", fmt.Errorf("tag %q does not match a source", tag)
	}
	return found, nil
}

// FromList parses a dispatch list, validates names and removes duplicates.
func FromList(list string, names []string) ([]string, error) {
	requested := strings.FieldsFunc(list, func(r rune) bool {
		return r == ',' || r == ' ' || r == '\n' || r == '\t' || r == '\r'
	})
	if len(requested) == 0 {
		return nil, fmt.Errorf("GRAVES_SOURCES is required")
	}
	known, selected := map[string]bool{}, map[string]bool{}
	for _, name := range names {
		known[name] = true
	}
	for _, name := range requested {
		if !known[name] {
			return nil, fmt.Errorf("unknown source %q", name)
		}
		selected[name] = true
	}
	return sortedNames(selected), nil
}

// FromChanges selects only source or declared shared path changes, never common code.
func FromChanges(root string, names, files []string) ([]string, error) {
	selected := map[string]bool{}
	var sharedChanges []string
	for _, file := range files {
		if strings.HasPrefix(file, "_shared/") {
			sharedChanges = append(sharedChanges, file)
		}
	}
	for _, name := range names {
		for _, file := range files {
			if strings.HasPrefix(file, "sources/"+name+"/") {
				selected[name] = true
			}
		}
		if selected[name] || len(sharedChanges) == 0 {
			continue
		}
		shared, err := readShared(filepath.Join(root, "sources", name, "build.yaml"))
		if err != nil {
			return nil, err
		}
		for _, file := range sharedChanges {
			for _, pattern := range shared {
				match, _ := path.Match(pattern, file)
				prefix := strings.TrimRight(pattern, "/") + "/"
				if match || file == pattern || strings.HasPrefix(file, prefix) {
					selected[name] = true
				}
			}
		}
	}
	return sortedNames(selected), nil
}
func readShared(file string) ([]string, error) {
	data, err := os.ReadFile(file)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", file, err)
	}
	var metadata struct {
		Shared []string `yaml:"shared"`
	}
	if err := yaml.Unmarshal(data, &metadata); err != nil {
		return nil, fmt.Errorf("%s: shared metadata: %w", file, err)
	}
	return metadata.Shared, nil
}
func sortedNames(set map[string]bool) []string {
	names := make([]string, 0, len(set))
	for name := range set {
		names = append(names, name)
	}
	sort.Strings(names)
	return names
}

// BuildMatrix keeps source order sorted and target order as declared in each recipe.
func BuildMatrix(root string, names []string) (*Matrix, error) {
	matrix := &Matrix{Include: []Entry{}}
	names = append([]string(nil), names...)
	sort.Strings(names)
	for _, name := range names {
		dir := filepath.Join(root, "sources", name)
		m, err := manifest.Load(filepath.Join(dir, "build.yaml"))
		if err != nil {
			return nil, err
		}
		for _, target := range m.Targets {
			e := Entry{Source: name, Target: target.Name, Runner: target.Runner}
			if target.Cache != nil {
				// Patches and steps come from the recipe, so it is always hashed.
				globs := append([]string{"build.yaml"}, target.Cache.KeyFiles...)
				digest, err := CacheDigest(dir, globs)
				if err != nil {
					return nil, err
				}
				e.CacheKey = fmt.Sprintf("graves-%s-%s-%s-%s", name, target.Name,
					m.Upstream.Ref, digest)
				e.CachePaths = strings.Join(target.Cache.Paths, "\n")
			}
			matrix.Include = append(matrix.Include, e)
		}
	}
	return matrix, nil
}

// CacheDigest hashes sorted, unique file paths and contents, supporting ** globs.
func CacheDigest(dir string, globs []string) (string, error) {
	var files []string
	err := filepath.WalkDir(dir, func(file string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if !entry.Type().IsRegular() {
			return nil
		}
		rel, err := filepath.Rel(dir, file)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		for _, glob := range globs {
			if globMatch(glob, rel) {
				files = append(files, rel)
				break
			}
		}
		return nil
	})
	if err != nil {
		return "", fmt.Errorf("cache key files: %w", err)
	}
	sort.Strings(files)
	hash := sha256.New()
	for _, file := range files {
		data, err := os.ReadFile(filepath.Join(dir, file))
		if err != nil {
			return "", fmt.Errorf("cache key %s: %w", file, err)
		}
		_, _ = hash.Write([]byte(file + "\x00"))
		_, _ = hash.Write(data)
	}
	return fmt.Sprintf("%x", hash.Sum(nil))[:16], nil
}
func globMatch(pattern, file string) bool {
	p, f := strings.Split(pattern, "/"), strings.Split(file, "/")
	var match func(int, int) bool
	match = func(i, j int) bool {
		if i == len(p) {
			return j == len(f)
		}
		if p[i] == "**" {
			return match(i+1, j) || (j < len(f) && match(i, j+1))
		}
		if j == len(f) {
			return false
		}
		ok, _ := path.Match(p[i], f[j])
		return ok && match(i+1, j+1)
	}
	return match(0, 0)
}
