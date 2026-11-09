package manifest

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
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

// The block style keeps values with '?' and '#' intact, unlike the flow style.
var validRelease = "upstream:\n  release:\n    repo: owner/sources\n" +
	"    tag: sources-app\n    asset: app.apk\n    sha256: " + strings.Repeat("a", 64) +
	"\n  version: 1.2.3\n  patch: 0\ntargets:\n  - name: android\n    downloads:\n" +
	"      - repo: owner/tools\n        tag: v1.0.0\n        asset: tool.jar\n" +
	"        sha256: " + strings.Repeat("b", 64) + "\n    secrets: [SIGN_KEY]\n" +
	"    vars: [SIGN_CERT]\n    steps: [bash scripts/build.sh]\n"

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
		{"cache scalar", "key_files: ['scripts/**']", "key_files: scripts/**", "sequence"},
		{"cache map", "key_files: ['scripts/**']", "key_files: {a: b}", "sequence"},
		{"cache map item", "key_files: ['scripts/**']", "key_files: [{a: b}]", "!!map"},
		{"cache nested map", "key_files: ['scripts/**']", "key_files: [[{a: b}]]", "!!map"},
		{
			"cache null item",
			"key_files: ['scripts/**']",
			"key_files: [~]",
			".cache.key_files",
		},
		{"cache nested empty", "key_files: ['scripts/**']", "key_files: [[], []]", ".cache"},
		{
			"cache nested relative",
			"key_files: ['scripts/**']",
			"key_files: [scripts/**, ['../scripts']]",
			".cache.key_files",
		},
		{
			"cache nested glob",
			"key_files: ['scripts/**']",
			"key_files: [[scripts/**, '[bad']]",
			".cache.key_files",
		},
		{
			"cache self alias",
			"key_files: ['scripts/**']",
			"key_files: &files [scripts/**, *files]",
			"contains itself",
		},
		{
			"cache nested self alias",
			"key_files: ['scripts/**']",
			"key_files: [scripts/**, &inner [patches/**, [*inner]]]",
			"contains itself",
		},
		{"short sha", "  ref: v1.2.3", "  ref: abcdef1", "upstream.ref"},
		{"upper sha", "  ref: v1.2.3", "  ref: " + strings.Repeat("A", 40), "upstream.ref"},
		{
			"sha version",
			"  ref: v1.2.3",
			"  ref: " + strings.Repeat("a", 40),
			"upstream.version",
		},
		{"version v", "  ref: v1.2.3", "  ref: v1.2.3\n  version: v3.3.0", "upstream.version"},
		{
			"version zero",
			"  ref: v1.2.3",
			"  ref: v1.2.3\n  version: 3.03.0",
			"upstream.version",
		},
		{"width range", "  patch: 0", "  patch: 0\n  patch_width: 0", "upstream.patch_width"},
		{
			"width narrow",
			"  patch: 0",
			"  patch: 1000\n  patch_width: 3",
			"upstream.patch_width",
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
	pinned := strings.Replace(validYAML, "  ref: v1.2.3", "  ref: "+strings.Repeat("a", 40)+
		"\n  version: 3.3.0\n  patch_width: 3", 1)
	if _, err := Decode([]byte(pinned), "pinned.yaml"); err != nil {
		t.Fatal(err)
	}
	if _, err := m.TargetByName("npm"); err != nil {
		t.Fatal(err)
	}
	if _, err := m.TargetByName("missing"); err == nil {
		t.Fatal("missing target")
	}
}
func TestGlobsUnmarshalYAML(t *testing.T) {
	const head = "upstream: {url: u, ref: r, patch: 0}\ntargets:\n"
	target := func(name, cache string) string {
		return "  - name: " + name + "\n    cache:\n      paths: [p]\n" + cache +
			"    steps: [true]\n"
	}
	for _, tt := range []struct {
		name, data string
		want       [][]string
	}{
		{
			"flat",
			target("a", "      key_files: [Dockerfile, scripts/**]\n"),
			[][]string{{"Dockerfile", "scripts/**"}},
		},
		{
			"inline nested",
			target("a", "      key_files: [Dockerfile, [scripts/**, [patches/**]], []]\n"),
			[][]string{{"Dockerfile", "scripts/**", "patches/**"}},
		},
		{
			"flow alias",
			target("a", "      key_files: &files [Dockerfile, scripts/**]\n") +
				target("b", "      key_files: [*files, patches/windows/**]\n"),
			[][]string{
				{"Dockerfile", "scripts/**"},
				{"Dockerfile", "scripts/**", "patches/windows/**"},
			},
		},
		{
			"block alias first and last",
			target("a", "      key_files: &files\n        - Dockerfile\n") +
				target("b", "      key_files:\n        - *files\n        - scripts/**\n") +
				target("c", "      key_files:\n        - scripts/**\n        - *files\n"),
			[][]string{
				{"Dockerfile"},
				{"Dockerfile", "scripts/**"},
				{"scripts/**", "Dockerfile"},
			},
		},
		{
			"whole list alias",
			target("a", "      key_files: &files [Dockerfile]\n") +
				target("b", "      key_files: *files\n"),
			[][]string{{"Dockerfile"}, {"Dockerfile"}},
		},
		{
			"chained aliases",
			target("a", "      key_files: &base [Dockerfile]\n") +
				target("b", "      key_files: &common [*base, patches/common/**]\n") +
				target("c", "      key_files: [*common, patches/windows/**]\n"),
			[][]string{
				{"Dockerfile"},
				{"Dockerfile", "patches/common/**"},
				{"Dockerfile", "patches/common/**", "patches/windows/**"},
			},
		},
		{
			"repeated alias",
			target("a", "      key_files: &files [Dockerfile]\n") +
				target("b", "      key_files: [*files, *files]\n"),
			[][]string{{"Dockerfile"}, {"Dockerfile", "Dockerfile"}},
		},
		{
			"scalar alias",
			target("a", "      key_files: [&file Dockerfile]\n") +
				target("b", "      key_files: [*file, scripts/**]\n"),
			[][]string{{"Dockerfile"}, {"Dockerfile", "scripts/**"}},
		},
		{
			"merge key",
			"  - name: a\n    cache: &cache\n      paths: [p]\n      key_files: &files\n" +
				"        - Dockerfile\n    steps: [true]\n  - name: b\n    cache:\n" +
				"      <<: *cache\n      key_files:\n        - *files\n" +
				"        - patches/windows/**\n    steps: [true]\n",
			[][]string{{"Dockerfile"}, {"Dockerfile", "patches/windows/**"}},
		},
	} {
		t.Run(tt.name, func(t *testing.T) {
			m, err := Decode([]byte(head+tt.data), "recipe.yaml")
			if err != nil {
				t.Fatal(err)
			}
			var got [][]string
			for _, target := range m.Targets {
				if len(target.Cache.Paths) != 1 {
					t.Fatalf("%s paths %v", target.Name, target.Cache.Paths)
				}
				got = append(got, target.Cache.KeyFiles)
			}
			if !reflect.DeepEqual(got, tt.want) {
				t.Fatalf("got %q, want %q", got, tt.want)
			}
		})
	}
}
func TestDecodeRelease(t *testing.T) {
	type decodeCase struct{ name, from, to, field string }
	cases := []decodeCase{
		{"url", "  version: 1.2.3\n", "  version: 1.2.3\n  url: u\n", "upstream.release"},
		{"ref", "  version: 1.2.3\n", "  version: 1.2.3\n  ref: r\n", "upstream.release"},
		{"version required", "  version: 1.2.3\n", "", "upstream.version"},
		{"patches", "    steps:", "    patches: [patches]\n    steps:", "targets[0].patches"},
		{"empty patches", "    steps:", "    patches: []\n    steps:", "targets[0].patches"},
		{"null patches", "    steps:", "    patches: null\n    steps:", "targets[0].patches"},
		{"tilde patches", "    steps:", "    patches: ~\n    steps:", "targets[0].patches"},
		{"bare patches", "    steps:", "    patches:\n    steps:", "targets[0].patches"},
		{
			"download duplicate",
			"    secrets:",
			"      - repo: owner/tools\n        tag: v1.0.1\n        asset: tool.jar\n" +
				"        sha256: " + strings.Repeat("b", 64) + "\n    secrets:",
			".downloads[1].asset",
		},
		{
			"download sha256",
			"        sha256: " + strings.Repeat("b", 64),
			"        sha256: " + strings.Repeat("B", 64),
			"targets[0].downloads[0].sha256",
		},
		{"secret and var", "vars: [SIGN_CERT]", "vars: [SIGN_KEY]", "targets[0].vars[0]"},
	}
	sum := strings.Repeat("a", 64)
	for _, list := range []struct {
		field, from, key string
		values           []string
	}{
		{".repo", "    repo: owner/sources\n", "repo", []string{
			"owner", "owner/", "/name", "a/b/c", `a\b/c`, "./name", "../name", "owner/.",
			"owner/..", "own#er/name", "owner/na?me", "owner/na me",
		}},
		{".tag", "    tag: sources-app\n", "tag", []string{
			"", "a b", "release/1.2", "v1#fix", "v1?x", ".v1", "-v1",
		}},
		{".asset", "    asset: app.apk\n", "asset", []string{
			"dir/app.apk", `dir\app.apk`, "..",
		}},
		{".sha256", "    sha256: " + sum + "\n", "sha256", []string{
			strings.Repeat("A", 64), sum[:63],
		}},
	} {
		for _, value := range list.values {
			cases = append(cases, decodeCase{fmt.Sprintf("%s %q", list.field, value),
				list.from, "    " + list.key + ": '" + value + "'\n", list.field})
		}
	}
	for _, tt := range cases {
		t.Run(tt.name, func(t *testing.T) {
			_, err := Decode(
				[]byte(strings.Replace(validRelease, tt.from, tt.to, 1)),
				"recipe.yaml",
			)
			if err == nil || !strings.Contains(err.Error(), "recipe.yaml") ||
				!strings.Contains(err.Error(), tt.field) {
				t.Fatalf("expected %s: %v", tt.field, err)
			}
		})
	}
	for _, field := range []string{"secrets", "vars"} {
		for _, name := range []string{"lower", "1KEY", "GRAVES_X", "GITHUB_X"} {
			t.Run(field+" "+name, func(t *testing.T) {
				from := map[string]string{"secrets": "[SIGN_KEY]", "vars": "[SIGN_CERT]"}[field]
				data := strings.Replace(validRelease, field+": "+from, field+": ["+name+"]", 1)
				_, err := Decode([]byte(data), "recipe.yaml")
				want := "targets[0]." + field + "[0]"
				if err == nil || !strings.Contains(err.Error(), "recipe.yaml") ||
					!strings.Contains(err.Error(), want) {
					t.Fatalf("expected %s: %v", want, err)
				}
			})
		}
	}
	nullTarget := strings.Replace(validRelease, "targets:\n", "targets:\n  - ~\n", 1)
	head, _, ok := strings.Cut(validRelease, "targets:")
	if !ok {
		t.Fatal("validRelease has no targets section")
	}
	for _, tt := range []struct{ name, data, field string }{
		{"null target", nullTarget, ""},
		{"null target patches", strings.Replace(nullTarget, "    steps:",
			"    patches: ~\n    steps:", 1), "targets[0].patches"},
		{"only null target", head + "targets: [~]\n", "targets"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			m, err := Decode([]byte(tt.data), "recipe.yaml")
			if tt.field == "" {
				if err != nil || len(m.Targets) != 1 {
					t.Fatalf("%+v %v", m, err)
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), "recipe.yaml: "+tt.field+":") {
				t.Fatalf("expected %s: %v", tt.field, err)
			}
		})
	}
	m, err := Decode([]byte(validRelease), "release.yaml")
	if err != nil || m.Upstream.PinnedRef() != "sources-app" ||
		m.Upstream.Source() != "owner/sources release sources-app asset app.apk" ||
		len(m.Targets[0].Downloads) != 1 || m.Targets[0].Downloads[0].Asset != "tool.jar" ||
		!reflect.DeepEqual(m.Targets[0].Secrets, []string{"SIGN_KEY"}) ||
		!reflect.DeepEqual(m.Targets[0].Vars, []string{"SIGN_CERT"}) {
		t.Fatalf("%+v %v", m, err)
	}
	m, err = Decode([]byte(validYAML), "valid.yaml")
	if err != nil || m.Upstream.PinnedRef() != "v1.2.3" ||
		m.Upstream.Source() != "https://example.test/upstream.git v1.2.3" {
		t.Fatalf("%+v %v", m, err)
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
