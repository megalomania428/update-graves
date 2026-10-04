// cspell:ignore versio pstream rtifacts vextra vanything shscripts appv
package selector

import (
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func fixture(t *testing.T, root, name, shared string, cache bool) string {
	t.Helper()
	dir := filepath.Join(root, "sources", name)
	if err := os.MkdirAll(filepath.Join(dir, "scripts", "nested"), 0o755); err != nil {
		t.Fatal(err)
	}
	data := "upstream: {url: 'https://example.test/repo', r" +
		"ef: v1, patch: 0}\nshared: [" + shared + "]\ntar" +
		"gets:\n  - name: linux\n    steps: [echo ok]\n"
	if cache {
		data += "    cache: {paths: ['~/.cache/app', '.graves/u" +
			"pstream/desktop'], key_files: ['scripts/**']}" +
			"\n"
	}
	data += "  - name: windows\n    steps: [echo ok]\n"
	if err := os.WriteFile(
		filepath.Join(dir, "build.yaml"),
		[]byte(data),
		0o644,
	); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(
		filepath.Join(dir, "scripts", "build.sh"),
		[]byte("echo build\n"),
		0o644,
	); err != nil {
		t.Fatal(err)
	}
	return dir
}
func TestFromTag(t *testing.T) {
	names := []string{"app", "app-vextra", "pi-web"}
	for _, tt := range []struct {
		tag, want string
		fail      bool
	}{
		{"app-v1", "app", false}, {"app-vextra-vanything", "app-vextra", false},
		{
			"pi-web-v1.202609.0p1",
			"pi-web",
			false,
		}, {
			"unknown-v1",
			"",
			true,
		}, {
			"app-1",
			"app",
			false,
		}, {
			"appv1",
			"",
			true,
		}, {
			"app",
			"",
			true,
		},
	} {
		got, err := FromTag(tt.tag, names)
		if got != tt.want || (err != nil) != tt.fail {
			t.Fatalf("%s: %s %v", tt.tag, got, err)
		}
	}
}
func TestFromList(t *testing.T) {
	got, err := FromList("b, a\n b\t", []string{"a", "b"})
	if err != nil || !reflect.DeepEqual(got, []string{"a", "b"}) {
		t.Fatalf("%v %v", got, err)
	}
	for _, list := range []string{" , ", "unknown"} {
		if _, err := FromList(list, []string{"a"}); err == nil {
			t.Fatal(list)
		}
	}
}
func TestFromChanges(t *testing.T) {
	root := t.TempDir()
	fixture(t, root, "app", "_shared/tool, '_shared/*.sh'", true)
	fixture(t, root, "pi-web", "_shared/pi", false)
	if err := os.Mkdir(filepath.Join(root, "sources", "deleted"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(
		filepath.Join(root, "sources", "not-a-dir"),
		nil,
		0o644,
	); err != nil {
		t.Fatal(err)
	}
	names, err := Discover(root)
	if err != nil || !reflect.DeepEqual(names, []string{"app", "pi-web"}) {
		t.Fatalf("%v %v", names, err)
	}
	for _, tt := range []struct{ files, want []string }{
		{[]string{"sources/app/build.yaml"}, []string{"app"}},
		{
			[]string{"_shared/tool/file", "sources/pi-web/scripts/build.sh"},
			[]string{"app", "pi-web"},
		},
		{[]string{"_shared/test.sh"}, []string{"app"}},
		{[]string{"_shared/pi"}, []string{"pi-web"}},
		{[]string{"golang/main.go", ".github/workflows/build.yaml"}, []string{}},
		{[]string{"sources/deleted/build.yaml", "sources/application/file"}, []string{}},
		{nil, []string{}},
	} {
		got, err := FromChanges(root, names, tt.files)
		if err != nil || !reflect.DeepEqual(got, tt.want) {
			t.Fatalf("%v: %v %v", tt.files, got, err)
		}
	}
	if _, err := Discover(t.TempDir()); err == nil {
		t.Fatal("missing sources")
	}
	if err := os.WriteFile(
		filepath.Join(root, "sources", "app", "build.yaml"),
		[]byte("invalid"),
		0o644,
	); err != nil {
		t.Fatal(err)
	}
	selected, err := FromChanges(root, names, []string{"sources/app/x"})
	if err != nil || !reflect.DeepEqual(selected, []string{"app"}) {
		t.Fatalf("direct selection must precede recipe validation: %v %v", selected, err)
	}
	if _, err := BuildMatrix(root, selected); err == nil {
		t.Fatal("selected invalid manifest was not validated")
	}
	selected, err = FromChanges(root, names, []string{"golang/main.go"})
	if err != nil || len(selected) != 0 {
		t.Fatalf("unselected invalid manifest blocked common-code PR: %v %v", selected, err)
	}
}
func TestFromChangesDeletedFile(t *testing.T) {
	root := t.TempDir()
	dir := fixture(t, root, "a", "", false)
	fixture(t, root, "b", "", false)
	removed := "sources/a/patches/x.patch"
	file := filepath.Join(dir, "patches", "x.patch")
	if err := os.MkdirAll(filepath.Dir(file), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(file, []byte("patch\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(file); err != nil {
		t.Fatal(err)
	}
	names, err := Discover(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, tt := range []struct {
		name        string
		files, want []string
	}{
		{"deleted patch", []string{removed}, []string{"a"}},
		{"moved patch", []string{removed, "sources/b/patches/x.patch"}, []string{"a", "b"}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			got, err := FromChanges(root, names, tt.files)
			if err != nil || !reflect.DeepEqual(got, tt.want) {
				t.Fatalf("FromChanges() = %v, want %v, error %v", got, tt.want, err)
			}
		})
	}
}
func TestBuildMatrix(t *testing.T) {
	root := t.TempDir()
	fixture(t, root, "app", "", true)
	fixture(t, root, "npm", "", false)
	matrix, err := BuildMatrix(root, []string{"npm", "app"})
	if err != nil {
		t.Fatal(err)
	}
	if len(matrix.Include) != 4 || matrix.Include[0].Source != "app" ||
		matrix.Include[0].Target != "linux" ||
		matrix.Include[1].Target != "windows" ||
		matrix.Include[2].Source != "npm" {
		t.Fatal(matrix)
	}
	first := matrix.Include[0]
	if !strings.HasPrefix(first.CacheKey, "graves-app-linux-v1-") ||
		first.CachePaths != "~/.cache/app\n.graves/upstream/desktop" ||
		first.Runner != "ubuntu-24.04" {
		t.Fatal(first)
	}
	if matrix.Include[2].CacheKey != "" || matrix.Include[2].CachePaths != "" {
		t.Fatal(matrix.Include[2])
	}
	recipe := filepath.Join(root, "sources", "app", "build.yaml")
	data, err := os.ReadFile(recipe)
	if err != nil {
		t.Fatal(err)
	}
	data = append(data, "# changed recipe\n"...)
	if err := os.WriteFile(recipe, data, 0o644); err != nil {
		t.Fatal(err)
	}
	changed, err := BuildMatrix(root, []string{"app"})
	if err != nil || changed.Include[0].CacheKey == first.CacheKey {
		t.Fatalf("recipe change kept cache key: %v %v", changed, err)
	}
	empty, err := BuildMatrix(root, nil)
	if err != nil || empty.Include == nil || len(empty.Include) != 0 {
		t.Fatalf("%v %v", empty, err)
	}
	if _, err := BuildMatrix(root, []string{"missing"}); err == nil {
		t.Fatal("missing manifest")
	}
}
func TestCacheDigest(t *testing.T) {
	dir := t.TempDir()
	for _, file := range []string{"scripts/z.sh", "scripts/nested/a.sh", "ignored"} {
		full := filepath.Join(dir, file)
		if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(full, []byte(file), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	got, err := CacheDigest(dir, []string{"scripts/**", "scripts/*.sh"})
	want := sha256.Sum256(
		[]byte("scripts/nested/a.sh\x00scripts/nested/a.shscripts/z.sh\x00scripts/z.sh"),
	)
	if err != nil || got != fmt.Sprintf("%x", want)[:16] {
		t.Fatalf("%s %v", got, err)
	}
	if err := os.WriteFile(
		filepath.Join(dir, "scripts", "z.sh"),
		[]byte("changed"),
		0o644,
	); err != nil {
		t.Fatal(err)
	}
	changed, err := CacheDigest(dir, []string{"scripts/**"})
	if err != nil || changed == got {
		t.Fatalf("%s %v", changed, err)
	}
	if _, err := CacheDigest(filepath.Join(dir, "missing"), []string{"**"}); err == nil {
		t.Fatal("walk error")
	}
	for _, tt := range []struct {
		pattern, file string
		want          bool
	}{
		{"scripts/**", "scripts/x/y.sh", true}, {"scripts/*.sh", "scripts/x/y.sh", false},
		{"**/*.sh", "build.sh", true}, {"scripts/**", "other/build.sh", false},
	} {
		if got := globMatch(tt.pattern, tt.file); got != tt.want {
			t.Fatalf("%+v", tt)
		}
	}
}

func Test_readShared(t *testing.T) {
	file := filepath.Join(t.TempDir(), "build.yaml")
	if _, err := readShared(file); err == nil {
		t.Fatal("missing metadata file")
	}
	for _, tt := range []struct {
		data string
		want []string
		fail bool
	}{
		{"shared: [_shared/tool]\nunknown: true\n", []string{"_shared/tool"}, false},
		{"shared: [broken\n", nil, true},
	} {
		if err := os.WriteFile(file, []byte(tt.data), 0o644); err != nil {
			t.Fatal(err)
		}
		got, err := readShared(file)
		if (err != nil) != tt.fail || !reflect.DeepEqual(got, tt.want) {
			t.Fatalf("metadata %v %v", got, err)
		}
	}
}
