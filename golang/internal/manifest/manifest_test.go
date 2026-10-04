package manifest

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const validYAML = "upstream:\n  url: https://example.test/upstream.git\n" +
	"  ref: v1.2.3\n  patch: 0\nshared: [_shared/tool]\nrelease:\n" +
	"  notes_limit: 4096\n  targets:\n    - type: self\n    - type: repo\n" +
	"      repo: private-releases\n    - type: telegram\n      chat: '@releases'\n" +
	"targets:\n  - name: linux\n    patches: [patches/common]\n    cache:\n" +
	"      paths: ['~/.cache/app']\n      key_files: ['scripts/**']\n" +
	"    steps: [bash scripts/build.sh]\n"

func TestDecode(t *testing.T) {
	for _, tt := range []struct{ name, from, to, field string }{
		{"url", "  url: https://example.test/upstream.git", "  url: ''", "upstream.url"},
		{"ref", "  ref: v1.2.3", "  ref: ''", "upstream.ref"},
		{"patch required", "  patch: 0\n", "", "upstream.patch"},
		{"patch negative", "patch: 0", "patch: -1", "upstream.patch"},
		{"shared root", "_shared/tool", "scripts/tool", "shared[0]"},
		{"shared escape", "_shared/tool", "_shared/../tool", "shared[0]"},
		{"shared glob", "_shared/tool", "'_shared/[bad'", "shared[0]"},
		{"budget", "notes_limit: 4096", "notes_limit: 200", "release.notes_limit"},
		{"budget zero", "notes_limit: 4096", "notes_limit: 0", "release.notes_limit"},
		{"release type", "type: self", "type: unknown", "release.targets[0].type"},
		{"repo required", "repo: private-releases", "repo: ''", ".repo"},
		{"repo owner", "repo: private-releases", "repo: owner/repo", ".repo"},
		{"chat required", "chat: '@releases'", "chat: ''", ".chat"},
		{"targets required", "  - name: linux", "  - name: ''", "targets[0].name"},
		{"target regex", "name: linux", "name: Linux!", "targets[0].name"},
		{"target duplicate", "targets:\n  - name: linux", "targets:\n  - name: linux\n    " +
			"steps: [true]\n  - name: linux", "duplicate target"},
		{"patch escape", "patches/common", "../patches", ".patches"},
		{"steps required", "steps: [bash scripts/build.sh]", "steps: []", ".steps"},
		{"step empty", "steps: [bash scripts/build.sh]", "steps: ['']", ".steps"},
		{"cache paths", "paths: ['~/.cache/app']", "paths: []", ".cache"},
		{"cache files", "key_files: ['scripts/**']", "key_files: []", ".cache"},
		{
			"cache relative",
			"key_files: ['scripts/**']",
			"key_files: ['../scripts']",
			".cache.key_files",
		},
		{
			"cache glob",
			"key_files: ['scripts/**']",
			"key_files: ['[bad']",
			".cache.key_files",
		},
		{"unknown", "  ref: v1.2.3", "  ref: v1.2.3\n  typo: true", "field typo"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			_, err := Decode(
				[]byte(strings.Replace(validYAML, tt.from, tt.to, 1)),
				"recipe.yaml",
			)
			if err == nil || !strings.Contains(err.Error(), "recipe.yaml") ||
				!strings.Contains(err.Error(), tt.field) {
				t.Fatalf("expected %s: %v", tt.field, err)
			}
		})
	}
	for _, data := range []string{"", "[broken", validYAML + "---\n{}\n", "upstream: {ur" +
		"l: u, ref: r, patch: 0}\ntargets: []"} {
		if _, err := Decode([]byte(data), "bad.yaml"); err == nil {
			t.Fatal("expected invalid document")
		}
	}
	m, err := Decode([]byte(validYAML), "valid.yaml")
	if err != nil || m.Targets[0].Runner != "ubuntu-24.04" || len(m.Release.Targets) != 3 {
		t.Fatalf("%+v %v", m, err)
	}
	defaults := "upstream: {url: u, ref: r, patch: 0}\ntargets:\n  - name" +
		": npm\n    steps: [echo ok]\n"
	m, err = Decode([]byte(defaults), "defaults.yaml")
	if err != nil || m.Release.NotesLimit != 4096 || len(m.Release.Targets) != 1 ||
		m.Release.Targets[0].Type != "self" {
		t.Fatalf("%+v %v", m, err)
	}
	if _, err := m.TargetByName("npm"); err != nil {
		t.Fatal(err)
	}
	if _, err := m.TargetByName("missing"); err == nil {
		t.Fatal("missing target")
	}
}
func TestLoad(t *testing.T) {
	file := filepath.Join(t.TempDir(), "build.yaml")
	if _, err := Load(file); err == nil {
		t.Fatal("read error")
	}
	if err := os.WriteFile(file, []byte(validYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(file); err != nil {
		t.Fatal(err)
	}
}
func Test_safeRelative(t *testing.T) {
	for _, path := range []string{
		"",
		".",
		"..",
		"/tmp/file",
		"../x",
		"a/../x",
		"a/..",
		`a\b`,
	} {
		if safeRelative(path) {
			t.Fatal(path)
		}
	}
	if !safeRelative("patches/common") {
		t.Fatal("relative path")
	}
}
